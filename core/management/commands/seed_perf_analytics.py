"""Stress-seed one business with a LARGE transaction history, then time the analytics pages.

The scenario this exists for: a shop runs on Standard (no analytics) for ~2 years, piles up
daily sales (100/day) + purchases (10/day), then upgrades to Pro — and the analytics pages,
which recompute live on every load (there is no pre-built backend for them), suddenly have to aggregate all of
that on the first click. This command builds that worst case and MEASURES it, so the decision
about a loading state is made on numbers, not a guess.

    python manage.py seed_perf_analytics                 # seed 100 sales/day + 10 purchases/day (73k / 7.3k over 2 yrs)
    python manage.py seed_perf_analytics --days 30       # seed 100 sales/day + 10 purchases/day over last 30 days
    python manage.py seed_perf_analytics --benchmark     # measure the EXISTING dataset only, no seeding
    python manage.py seed_perf_analytics --flush         # remove everything this command created
"""
import random
import time
from datetime import timedelta
from decimal import Decimal, ROUND_CEILING
from itertools import islice

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from user.models import User, BusinessProfile
from subscription.models import BusinessPlan
from Product.models import Product
from Sales.models import Sale, SaleItem, SalesPayment, SaleSequence, OrderSequence
from Expense.models import Purchase, PurchaseItem
from Supplier.models import Supplier, Material

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
    help = 'Stress-seed a business with 100 sales/day and 10 purchases/day and time the analytics pages.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=100, help='Spread the history over the last N days (default 730 = ~2yr).')
        # Defaults dynamically calculated based on 100/day and 10/day
        parser.add_argument('--sales', type=int, default=None, help='Total sales to create (default: days * 100).')
        parser.add_argument('--purchases', type=int, default=None, help='Total purchases to create (default: days * 10).')
        parser.add_argument('--products', type=int, default=60, help='Goods products in the catalog (default 60).')
        parser.add_argument('--suppliers', type=int, default=15, help='Suppliers (default 15).')
        parser.add_argument('--materials', type=int, default=80, help='Materials spread across suppliers (default 80).')
        parser.add_argument('--batch', type=int, default=2000, help='bulk_create batch size (default 2000).')
        parser.add_argument('--seed', type=int, default=7, help='RNG seed for reproducibility.')
        parser.add_argument('--flush', action='store_true', help='Delete everything this command created, then stop.')
        parser.add_argument('--benchmark', action='store_true', help='Only time the existing dataset; do NOT seed.')
        parser.add_argument('--runs', type=int, default=3, help='Times to load each page when benchmarking (default 3).')

    def handle(self, *args, **opts):
        random.seed(opts['seed'])
        self._tune_sqlite()
        owner, biz = self._owner_and_business()

        # Set default sales and purchases based on daily target if not explicitly passed
        days = opts['days']
        opts['sales'] = opts['sales'] if opts['sales'] is not None else (days * 100)
        opts['purchases'] = opts['purchases'] if opts['purchases'] is not None else (days * 10)

        if opts['flush']:
            with transaction.atomic():
                self._flush(biz)

        if not opts['benchmark'] and not (opts['flush'] and opts['sales'] == 0):
            self._seed(owner, biz, opts)

        self._report_counts(biz)
        self._benchmark(owner, biz, runs=opts['runs'])

    def _tune_sqlite(self):
        from django.db import connection
        if connection.vendor != 'sqlite':
            return
        with connection.cursor() as c:
            c.execute('PRAGMA journal_mode=WAL;')
            c.execute('PRAGMA busy_timeout=30000;')
            c.execute('PRAGMA synchronous=NORMAL;')

    def _owner_and_business(self):
        owner, created = User.objects.get_or_create(
            username=OWNER_USERNAME, defaults={'role': 'owner'})
        if created:
            owner.set_password('testpassword')
            owner.save()

        biz, _ = BusinessProfile.objects.get_or_create(
            user=owner, business_name=BIZ_NAME)

        bp = BusinessPlan.objects.get(business=biz)
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

    def _seed(self, owner, biz, opts):
        t0 = time.perf_counter()
        goods, pool = self._catalog(owner, biz, opts['products'])
        materials = self._materials(owner, biz, opts['suppliers'], opts['materials'])
        today = timezone.localdate()

        self.stdout.write(f"Seeding {opts['sales']:,} sales (~{opts['sales'] // opts['days']:,}/day over {opts['days']} days)...")
        self._seed_sales(owner, biz, pool, n=opts['sales'], days=opts['days'], batch=opts['batch'], today=today)

        self.stdout.write(f"Seeding {opts['purchases']:,} purchases (~{opts['purchases'] // opts['days']:,}/day over {opts['days']} days)...")
        self._seed_purchases(owner, biz, materials, n=opts['purchases'], days=opts['days'], batch=opts['batch'], today=today)

        self.stdout.write(self.style.SUCCESS(f"Seed done in {time.perf_counter() - t0:.1f}s."))

    def _catalog(self, owner, biz, n_goods):
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
        per_day = max(1, n // days)
        remainder = n % days

        # Real sales get a reference stamped in Sale.save() (SI- in BIR mode, else ORD-),
        # but bulk_create skips save(), so number them here and advance the sequence once
        # at the end. Assign OLDEST day first so the serial ascends with the date, exactly
        # like production — that's what makes the list's `-reference` sort show newest first.
        seq_cls = SaleSequence if biz.is_bir_active else OrderSequence
        prefix = 'SI' if biz.is_bir_active else 'ORD'
        seq, _ = seq_cls.objects.get_or_create(business=biz)
        ref_num = seq.next_number

        def gen():
            nonlocal ref_num
            total_generated = 0
            for day_offset in range(days - 1, -1, -1):   # oldest → newest
                d = today - timedelta(days=day_offset)
                # Ensure exact count match even with integer division rounding
                count_for_day = per_day + (1 if day_offset < remainder else 0)

                for _ in range(count_for_day):
                    if total_generated >= n:
                        return
                    lines = random.sample(pool, random.randint(1, min(3, len(pool))))
                    items = [(p, random.randint(1, 5)) for p in lines]
                    gross = sum((p.selling_price * q for p, q in items), Decimal('0'))
                    qty = sum(q for _, q in items)
                    reference = f"{prefix}-{ref_num:010d}"
                    ref_num += 1
                    yield (Sale(user=owner, business=biz, created_by=owner, date=d,
                                status='completed', total_revenue=gross, reference=reference,
                                line_count=len(items), total_quantity=qty), items)
                    total_generated += 1

        done = 0
        for chunk in _chunks(gen(), batch):
            sales = [s for s, _ in chunk]
            Sale.objects.bulk_create(sales, batch_size=batch)
            items = []
            payments = []
            for s, plan in chunk:
                for p, q in plan:
                    items.append(SaleItem(sale=s, product=p, name=p.name,
                                          price_at_sale=p.selling_price, cost_price=p.cost_price,
                                          quantity=q))
                payments.append(self._payment_for(owner, biz, s))
            SaleItem.objects.bulk_create(items, batch_size=batch)
            # A completed sale is settled — give it a payment so payment_method_code,
            # the settlement badges, and receivables reflect real money (bulk_create
            # skips SalesPayment.save(), so date + the cash-only tender are set here).
            SalesPayment.objects.bulk_create(payments, batch_size=batch)
            done += len(sales)
            if done % (batch * 10) == 0 or done == n:
                self.stdout.write(f"   ... {done:,}/{n:,} sales")

        # Persist where the serial run left off, so a real sale rung after this (or a
        # re-seed) continues the series instead of colliding on numbers we just used.
        seq.next_number = ref_num
        seq.save(update_fields=['next_number'])

    def _payment_for(self, owner, biz, sale):
        """One settling payment for a completed sale. Cash is the common counter case
        (and the only method that records a tender + change); GCash/bank are exact."""
        method = random.choices(['cash', 'gcash', 'bank'], weights=[70, 20, 10])[0]
        amount = sale.total_revenue or Decimal('0')
        tendered = None
        if method == 'cash':
            # Round the sticker up to the next ₱50 so there's realistic change to hand back.
            step = Decimal('50')
            tendered = (amount / step).to_integral_value(rounding=ROUND_CEILING) * step
        return SalesPayment(sale=sale, business=biz, created_by=owner,
                            amount=amount, date=sale.date, method=method, tendered=tendered)

    def _seed_purchases(self, owner, biz, materials, *, n, days, batch, today):
        per_day = max(1, n // days)
        remainder = n % days

        def gen():
            total_generated = 0
            for day_offset in range(days):
                d = today - timedelta(days=day_offset)
                count_for_day = per_day + (1 if day_offset < remainder else 0)
                
                for _ in range(count_for_day):
                    if total_generated >= n:
                        return
                    lines = random.sample(materials, random.randint(1, min(4, len(materials))))
                    plan = [(m, random.randint(1, 20)) for m in lines]
                    total = sum((m.price * q for m, q in plan), Decimal('0'))
                    yield (Purchase(user=owner, business=biz, created_by=owner, purchase_date=d,
                                    total_cost=total, line_count=len(plan), is_paid=True), plan)
                    total_generated += 1

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
                self.stdout.write(f"   ... {done:,}/{n:,} purchases")

    def _report_counts(self, biz):
        self.stdout.write(self.style.HTTP_INFO(
            f"\n{biz.business_name}: "
            f"{Sale.objects.filter(business=biz).count():,} sales, "
            f"{SalesPayment.objects.filter(business=biz).count():,} sales payments, "
            f"{SaleItem.objects.filter(sale__business=biz).count():,} sale items, "
            f"{Purchase.objects.filter(business=biz).count():,} purchases, "
            f"{PurchaseItem.objects.filter(purchase__business=biz).count():,} purchase items."))

    def _benchmark(self, owner, biz, *, runs):
        from django.conf import settings
        from django.test import Client
        from django.urls import reverse

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
            flag = '' if status == 200 else f'   [!! HTTP {status} -- gate/redirect, not a real timing]'
            self.stdout.write(
                f"  {label:<20} median {median:7.0f} ms   (best {times[0]:.0f}, worst {times[-1]:.0f}){flag}")

    def _flush(self, biz):
        pi = PurchaseItem.objects.filter(purchase__business=biz).delete()[0]
        pu = Purchase.objects.filter(business=biz).delete()[0]
        sa = Sale.objects.filter(business=biz).delete()[0]
        Material.all_objects.filter(business=biz).delete()
        Supplier.all_objects.filter(business=biz).delete()
        Product.all_objects.filter(business=biz).delete()
        self.stdout.write(self.style.WARNING(
            f"Flushed {sa:,} sales, {pu:,} purchases, {pi:,} purchase items and the perf catalog."))