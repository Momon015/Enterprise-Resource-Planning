"""Stress-seed one business with a LARGE transaction history, then time the analytics pages.

The scenario this exists for: a shop runs on Standard (no analytics) for ~2 years, piles up
100k sales + 10k purchases, then upgrades to Pro — and the analytics pages, which recompute
live on every load (there is no pre-built backend for them), suddenly have to aggregate all of
that on the first click. This command builds that worst case and MEASURES it, so the decision
about a loading state is made on numbers, not a guess.

    python manage.py seed_perf_analytics                 # seed 100k sales + 10k purchases, then time
    python manage.py seed_perf_analytics --sales 20000 --purchases 2000
    python manage.py seed_perf_analytics --benchmark     # measure the EXISTING dataset only, no seeding
    python manage.py seed_perf_analytics --flush         # remove everything this command created

Note on numbers: dev runs on SQLite. These timings are DIRECTIONAL — they tell you the shape of
the curve and whether a loading state is warranted, not the production figure. On PostgreSQL the
same aggregates generally run FASTER and scale better (real query planner, row-level locking).
The rule of thumb: if it's snappy on SQLite you're safe; if it drags, add the loading state AND
re-measure on Postgres before calling it a real problem.

Bulk path: uses bulk_create (needs SQLite >= 3.35 to return PKs — Python 3.11+ bundles it). A
row-at-a-time create() like seed_services_demo would be ~300k INSERTs and run for many minutes.
"""
import random
import time
from datetime import timedelta
from decimal import Decimal
from itertools import islice

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from user.models import User, BusinessProfile
from subscription.models import BusinessPlan
from Product.models import Product
from Sales.models import Sale, SaleItem
from Expense.models import Purchase, PurchaseItem
from Supplier.models import Supplier, Material

# A dedicated owner + business so the stress data never mixes with the demo store or real work.
# get_or_create keyed on these, so re-running doesn't spawn duplicates.
OWNER_USERNAME = 'perfowner'
BIZ_NAME = 'Perf Analytics Store'


def _chunks(iterable, size):
    it = iter(iterable)
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            return
        yield chunk


class Command(BaseCommand):
    help = 'Stress-seed a business with a large sales/purchase history and time the analytics pages.'

    def add_arguments(self, parser):
        parser.add_argument('--sales', type=int, default=100_000, help='Sale transactions to create (default 100,000).')
        parser.add_argument('--purchases', type=int, default=10_000, help='Purchase records to create (default 10,000).')
        parser.add_argument('--products', type=int, default=60, help='Goods products in the catalog (default 60).')
        parser.add_argument('--suppliers', type=int, default=15, help='Suppliers (default 15).')
        parser.add_argument('--materials', type=int, default=80, help='Materials spread across suppliers (default 80).')
        parser.add_argument('--days', type=int, default=730, help='Spread the history over the last N days (default 730 = ~2yr).')
        parser.add_argument('--batch', type=int, default=2000, help='bulk_create batch size (default 2000).')
        parser.add_argument('--seed', type=int, default=7, help='RNG seed for reproducibility.')
        parser.add_argument('--flush', action='store_true', help='Delete everything this command created, then stop (unless seeding too).')
        parser.add_argument('--benchmark', action='store_true', help='Only time the existing dataset; do NOT seed.')
        parser.add_argument('--runs', type=int, default=3, help='Times to load each page when benchmarking (default 3).')

    def handle(self, *args, **opts):
        random.seed(opts['seed'])
        self._tune_sqlite()
        owner, biz = self._owner_and_business()

        if opts['flush']:
            with transaction.atomic():
                self._flush(biz)

        if not opts['benchmark'] and not (opts['flush'] and opts['sales'] == 0):
            self._seed(owner, biz, opts)

        self._report_counts(biz)
        self._benchmark(owner, biz, runs=opts['runs'])

    def _tune_sqlite(self):
        """WAL + a long busy-timeout so a bulk seed can coexist with a running dev server
        (SQLite locks the whole file per write); synchronous=NORMAL is safe under WAL and
        cuts the 100k-row insert time sharply. No-op on any non-SQLite backend."""
        from django.db import connection
        if connection.vendor != 'sqlite':
            return
        with connection.cursor() as c:
            c.execute('PRAGMA journal_mode=WAL;')
            c.execute('PRAGMA busy_timeout=30000;')
            c.execute('PRAGMA synchronous=NORMAL;')

    # ── the Pro business ────────────────────────────────────────────────────────

    def _owner_and_business(self):
        """Owner + a PRO business, built the way production builds it (signals make the free
        Subscription and BusinessPlan; we just upgrade the plan to unlock analytics)."""
        owner, created = User.objects.get_or_create(
            username=OWNER_USERNAME, defaults={'role': 'owner'})
        if created:
            owner.set_password('testpassword')
            owner.save()

        biz, _ = BusinessProfile.objects.get_or_create(
            user=owner, business_name=BIZ_NAME)

        bp = BusinessPlan.objects.get(business=biz)      # signal created it on 'free'
        if bp.plan != 'pro' or not bp.is_active:
            started = timezone.now() - timedelta(days=365)
            bp.plan = 'pro'
            bp.is_active = True
            bp.plan_started_at = started
            bp.expires_at = started + timedelta(days=730)
            bp.save()
            biz.refresh_from_db()

        if not biz.offers_services:
            biz.offers_services = True
            biz.save(update_fields=['offers_services'])
        return owner, biz

    # ── seeding ─────────────────────────────────────────────────────────────────

    def _seed(self, owner, biz, opts):
        t0 = time.perf_counter()
        goods, pool = self._catalog(owner, biz, opts['products'])
        materials = self._materials(owner, biz, opts['suppliers'], opts['materials'])
        today = timezone.localdate()

        self.stdout.write(f"Seeding {opts['sales']:,} sales ...")
        self._seed_sales(owner, biz, pool, n=opts['sales'], days=opts['days'], batch=opts['batch'], today=today)

        self.stdout.write(f"Seeding {opts['purchases']:,} purchases ...")
        self._seed_purchases(owner, biz, materials, n=opts['purchases'], days=opts['days'], batch=opts['batch'], today=today)

        self.stdout.write(self.style.SUCCESS(f"Seed done in {time.perf_counter() - t0:.1f}s."))

    def _catalog(self, owner, biz, n_goods):
        """n_goods stockable products (real cost, so profit has margin) + 10 services + 10 rentals,
        so the Services/Rentals boards on all three pages get exercised too."""
        goods = []
        for i in range(n_goods):
            price = Decimal(random.randint(20, 800))
            cost = (price * Decimal(random.randint(45, 75)) / Decimal(100)).quantize(Decimal('0.01'))
            p, _ = Product.all_objects.get_or_create(
                business=biz, name=f'Perf Good {i:03d}',
                defaults=dict(user=owner, selling_price=price, cost_price=cost,
                              prepared_quantity=random.randint(0, 500), is_active=True))
            goods.append(p)

        services, rentals = [], []
        for i in range(10):
            s, _ = Product.all_objects.get_or_create(
                business=biz, name=f'Perf Service {i:02d}',
                defaults=dict(user=owner, selling_price=Decimal(random.randint(5, 120)),
                              cost_price=Decimal('0'), prepared_quantity=0,
                              is_service=True, is_session_based=False, is_active=True))
            services.append(s)
            r, _ = Product.all_objects.get_or_create(
                business=biz, name=f'Perf Rental {i:02d}',
                defaults=dict(user=owner, selling_price=Decimal(random.randint(50, 500)),
                              cost_price=Decimal('0'), prepared_quantity=0,
                              is_service=True, is_session_based=True, is_active=True))
            rentals.append(r)

        return goods, goods + services + rentals

    def _materials(self, owner, biz, n_suppliers, n_materials):
        suppliers = []
        for i in range(n_suppliers):
            s, _ = Supplier.all_objects.get_or_create(
                business=biz, name=f'Perf Supplier {i:02d}',
                defaults=dict(user=owner, status='active'))
            suppliers.append(s)

        materials = []
        for i in range(n_materials):
            sup = random.choice(suppliers)
            m, _ = Material.all_objects.get_or_create(
                business=biz, name=f'Perf Material {i:03d}',
                defaults=dict(user=owner, supplier=sup,
                              price=Decimal(random.randint(10, 400)), quantity=1))
            materials.append(m)
        return materials

    def _seed_sales(self, owner, biz, pool, *, n, days, batch, today):
        def gen():
            for _ in range(n):
                d = today - timedelta(days=random.randint(0, days - 1))
                lines = random.sample(pool, random.randint(1, min(3, len(pool))))
                items = [(p, random.randint(1, 5)) for p in lines]
                gross = sum((p.selling_price * q for p, q in items), Decimal('0'))
                yield (Sale(user=owner, business=biz, created_by=owner, date=d,
                            status='completed', total_revenue=gross, line_count=len(items)), items)

        done = 0
        for chunk in _chunks(gen(), batch):
            sales = [s for s, _ in chunk]
            Sale.objects.bulk_create(sales, batch_size=batch)     # pks populated (SQLite >= 3.35)
            items = []
            for s, plan in chunk:
                for p, q in plan:
                    items.append(SaleItem(sale=s, product=p, name=p.name,
                                          price_at_sale=p.selling_price, cost_price=p.cost_price,
                                          quantity=q))
            SaleItem.objects.bulk_create(items, batch_size=batch)
            done += len(sales)
            if done % (batch * 10) == 0 or done == n:
                self.stdout.write(f"  ... {done:,}/{n:,} sales")

    def _seed_purchases(self, owner, biz, materials, *, n, days, batch, today):
        def gen():
            for _ in range(n):
                d = today - timedelta(days=random.randint(0, days - 1))
                lines = random.sample(materials, random.randint(1, min(4, len(materials))))
                plan = [(m, random.randint(1, 20)) for m in lines]
                total = sum((m.price * q for m, q in plan), Decimal('0'))
                yield (Purchase(user=owner, business=biz, created_by=owner, purchase_date=d,
                                total_cost=total, line_count=len(plan), is_paid=True), plan)

        done = 0
        for chunk in _chunks(gen(), batch):
            purchases = [p for p, _ in chunk]
            Purchase.objects.bulk_create(purchases, batch_size=batch)
            items = []
            for pu, plan in chunk:
                for m, q in plan:
                    items.append(PurchaseItem(purchase=pu, material=m, name=m.name,
                                              price=m.price, quantity=q,
                                              supplier=m.supplier.name if m.supplier else ''))
            PurchaseItem.objects.bulk_create(items, batch_size=batch)
            done += len(purchases)
            if done % (batch * 5) == 0 or done == n:
                self.stdout.write(f"  ... {done:,}/{n:,} purchases")

    # ── measure ──────────────────────────────────────────────────────────────────

    def _report_counts(self, biz):
        self.stdout.write(self.style.HTTP_INFO(
            f"\n{biz.business_name}: "
            f"{Sale.objects.filter(business=biz).count():,} sales, "
            f"{SaleItem.objects.filter(sale__business=biz).count():,} sale items, "
            f"{Purchase.objects.filter(business=biz).count():,} purchases, "
            f"{PurchaseItem.objects.filter(purchase__business=biz).count():,} purchase items."))

    def _benchmark(self, owner, biz, *, runs):
        # Full end-to-end: the real view (query + aggregate + template render) through the test
        # client, exactly the work a browser triggers on 'All time' (the default range = worst case).
        from django.conf import settings
        from django.test import Client
        from django.urls import reverse

        # The test client speaks as host 'testserver'; ALLOWED_HOSTS is env-locked when DEBUG is
        # off (pre-launch hardening), so whitelist it for the run or every load 400s before it
        # ever touches the query we're trying to time.
        if 'testserver' not in settings.ALLOWED_HOSTS:
            settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']

        client = Client()
        client.force_login(owner)
        pages = [
            ('Sales Analytics',   'sales-analytics'),
            ('Expense Analytics', 'expense-analytics'),
            ('Profit Analytics',  'profit-analytics'),
        ]

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nAnalytics load time  (all-time, {runs} runs, SQLite dev -- directional):"))
        for label, name in pages:
            url = reverse(name, kwargs={'business_slug': biz.slug})
            times = []
            status = None
            for _ in range(runs):
                t0 = time.perf_counter()
                resp = client.get(url)
                times.append((time.perf_counter() - t0) * 1000)
                status = resp.status_code
            times.sort()
            median = times[len(times) // 2]
            flag = '' if status == 200 else f'  [!! HTTP {status} -- gate/redirect, not a real timing]'
            self.stdout.write(
                f"  {label:<20} median {median:7.0f} ms   (best {times[0]:.0f}, worst {times[-1]:.0f}){flag}")

    # ── flush ────────────────────────────────────────────────────────────────────

    def _flush(self, biz):
        # PurchaseItem.purchase is SET_NULL, so deleting purchases would orphan items -> items first.
        pi = PurchaseItem.objects.filter(purchase__business=biz).delete()[0]
        pu = Purchase.objects.filter(business=biz).delete()[0]
        # SaleItem.sale is CASCADE, so deleting sales takes their items with them.
        sa = Sale.objects.filter(business=biz).delete()[0]
        Material.all_objects.filter(business=biz).delete()
        Supplier.all_objects.filter(business=biz).delete()
        Product.all_objects.filter(business=biz).delete()
        self.stdout.write(self.style.WARNING(
            f"Flushed {sa:,} sales, {pu:,} purchases, {pi:,} purchase items and the perf catalog."))
