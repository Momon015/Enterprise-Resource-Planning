"""Seed a demo business with dummy SERVICES and RENTALS plus sales, so the Sales Analytics
Top Services / Top Rentals boards (and their Services⇄Rentals toggle) have something to show.

Services  = is_service=True,  is_session_based=False  (xerox, GCash cash-in, bills payment…)
Rentals   = is_service=True,  is_session_based=True   (videoke, PS, function-room hours…)

Also flips the business's `offers_services` on — the master switch those boards gate on.

Run:  python manage.py seed_services_demo            # defaults to Z Demo Store (login: zdemo)
      python manage.py seed_services_demo --business <slug> --count 10 --days 30
      python manage.py seed_services_demo --flush     # remove previously-seeded demo items + their sales
"""
import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from user.models import BusinessProfile
from Product.models import Product
from Sales.models import Sale, SaleItem

# Marker so --flush and re-runs can find exactly what this command created, without touching
# any real catalog items an owner made by hand.
TAG = '[demo]'

SERVICE_NAMES = [
    'Photocopy (B&W)', 'Photocopy (Color)', 'Print & Scan', 'Lamination',
    'GCash Cash-in', 'GCash Cash-out', 'Bills Payment', 'Load Retail',
    'Document Encoding', 'Passport Photo',
]
RENTAL_NAMES = [
    'Videoke (per hour)', 'PlayStation (per hour)', 'Billiards (per hour)',
    'Function Room (per hour)', 'Bike Rental (per hour)', 'Costume Rental (per day)',
    'Tent Rental (per day)', 'Sound System (per day)', 'Projector (per day)',
    'Table & Chairs Set (per day)',
]


class Command(BaseCommand):
    help = 'Seed dummy services + rentals with sales for a demo business (Sales Analytics boards).'

    def add_arguments(self, parser):
        parser.add_argument('--business', default='z-demo-store', help='Business slug (default z-demo-store).')
        parser.add_argument('--count', type=int, default=10, help='How many of EACH to create (default 10).')
        parser.add_argument('--days', type=int, default=30, help='Spread sales over the last N days (default 30).')
        parser.add_argument('--sales', type=int, default=45, help='How many sale transactions to generate (default 45).')
        parser.add_argument('--seed', type=int, default=7, help='RNG seed for reproducibility.')
        parser.add_argument('--flush', action='store_true', help='Delete previously-seeded [demo] services/rentals + their sales first.')

    def handle(self, *args, **opts):
        random.seed(opts['seed'])
        biz = BusinessProfile.all_objects.filter(slug=opts['business']).first()
        if not biz:
            raise CommandError(f"No business with slug {opts['business']!r}. "
                               f"Try --business demo-store, or check the slug.")

        with transaction.atomic():
            if opts['flush']:
                self._flush(biz)

            if not biz.offers_services:
                biz.offers_services = True
                biz.save(update_fields=['offers_services'])
                self.stdout.write(self.style.SUCCESS(f"Enabled services on {biz.business_name}."))

            services = self._make_products(biz, SERVICE_NAMES[:opts['count']], session=False,
                                            lo=5, hi=120)
            rentals = self._make_products(biz, RENTAL_NAMES[:opts['count']], session=True,
                                          lo=50, hi=500)
            pool = services + rentals

            made = self._make_sales(biz, pool, n=opts['sales'], days=opts['days'])

        self.stdout.write(self.style.SUCCESS(
            f"{biz.business_name}: {len(services)} services + {len(rentals)} rentals, "
            f"{made} sale transactions over {opts['days']} days.\n"
            f"-> Sales Analytics -> scroll to the Services & Rentals card."))

    # ── builders ──────────────────────────────────────────────────────────────

    def _make_products(self, biz, names, *, session, lo, hi):
        out = []
        for name in names:
            tagged = f"{name} {TAG}"
            product, created = Product.all_objects.get_or_create(
                business=biz, name=tagged,
                defaults=dict(
                    user=biz.user,
                    selling_price=Decimal(random.randint(lo, hi)),
                    cost_price=Decimal('0'),        # a service carries no COGS
                    prepared_quantity=0,            # required column; a service has no stock
                    is_service=True,
                    is_session_based=session,
                    is_active=True,
                ),
            )
            out.append(product)
        return out

    def _make_sales(self, biz, pool, *, n, days):
        now = timezone.localtime()
        made = 0
        for _ in range(n):
            day_offset = random.randint(0, max(days - 1, 0))
            when = now - timedelta(days=day_offset,
                                   hours=random.randint(0, 10), minutes=random.randint(0, 59))
            sale_date = when.date()

            lines = random.sample(pool, random.randint(1, 3))
            items = [(p, random.randint(1, 6)) for p in lines]

            sale = Sale.objects.create(
                user=biz.user,
                business=biz,
                created_by=biz.user,
                date=sale_date,
                status='completed',
                total_revenue=Decimal('0'),
                total_salary_cost=Decimal('0'),
                line_count=len(items),
            )
            gross = Decimal('0')
            for product, qty in items:
                gross += product.selling_price * qty
                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    name=product.name,
                    price_at_sale=product.selling_price,
                    cost_price=product.cost_price,
                    quantity=qty,
                )
            sale.total_revenue = gross
            sale.save(update_fields=['total_revenue'])

            # created_at is auto_now_add — a raw update is the only way to spread the rows
            # across the window so the trend/period views aren't all stacked on today.
            Sale._base_manager.filter(pk=sale.pk).update(created_at=when)
            made += 1
        return made

    def _flush(self, biz):
        demo = Product.all_objects.filter(business=biz, name__contains=TAG)
        sale_ids = list(
            SaleItem.objects.filter(product__in=demo).values_list('sale_id', flat=True).distinct()
        )
        SaleItem.objects.filter(product__in=demo).delete()
        Sale.objects.filter(id__in=sale_ids).delete()
        n = demo.count()
        demo.delete()
        self.stdout.write(self.style.WARNING(
            f"Flushed {n} [demo] service/rental products and {len(sale_ids)} of their sales."))
