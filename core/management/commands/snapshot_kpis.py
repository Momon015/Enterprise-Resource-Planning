from django.core.management.base import BaseCommand
from django.utils import timezone

from user.models import BusinessProfile
from core.models import KpiSnapshot
from core.utils.kpis import compute_product_kpis, compute_inventory_kpis, compute_sale_kpis, compute_supplier_kpis, compute_purchase_kpis


# Register page→compute_fn pairs here as you add KPI sets
PAGE_COMPUTERS = {
    'products': compute_product_kpis,
    'suppliers': compute_supplier_kpis,
    'inventory': compute_inventory_kpis,
    'sales':     compute_sale_kpis,
    'purchases': compute_purchase_kpis,
}


class Command(BaseCommand):
    help = "Snapshot today's KPIs for every business. Run daily (end of day)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--date',
            help='ISO date to snapshot (default: today). Useful for backfills.',
        )
        parser.add_argument(
            '--page',
            help='Snapshot only one page (products, suppliers, ...). Default: all.',
        )

    def handle(self, *args, **options):
        from datetime import date as date_cls

        target_date = (
            date_cls.fromisoformat(options['date'])
            if options['date'] else timezone.localdate()
        )

        pages = [options['page']] if options['page'] else list(PAGE_COMPUTERS.keys())

        total_snaps = 0
        for business in BusinessProfile.objects.all():
            for page in pages:
                compute_fn = PAGE_COMPUTERS.get(page)
                if not compute_fn:
                    self.stdout.write(self.style.WARNING(f"Unknown page: {page}"))
                    continue

                metrics = compute_fn(business)
                KpiSnapshot.objects.update_or_create(
                    business=business,
                    date=target_date,
                    page=page,
                    defaults={'metrics': metrics},
                )
                total_snaps += 1

        self.stdout.write(self.style.SUCCESS(
            f"Snapshotted {total_snaps} KPI row(s) for {target_date}."
        ))
