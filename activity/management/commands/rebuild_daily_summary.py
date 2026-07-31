"""Backfill / rebuild the DailyClose snapshots for one or all businesses.

WHY THIS EXISTS
    The Daily Summary page (Cash Flow + Accrual) reads sealed per-day snapshots from
    DailyClose instead of re-aggregating millions of rows on every load. Those snapshots
    are created lazily as past days are viewed — so a business that never opened the page
    (or opened it before some history existed) has gaps or, in a re-seeded dev DB, STALE
    rows frozen before the full day's data landed. This command seals every complete past
    day up front from CURRENT data, so the page's hot path is genuinely "today only".

TWO MODES
    default   fill only the days that have no snapshot yet (safe, additive — never
              touches a sealed day, honouring pen-not-pencil).
    --wipe    delete existing snapshots for the business first, then recompute all of
              them. This is the ONLY way to correct stale rows (DailyClose is append-only,
              so its instance .delete() is blocked — a queryset delete bypasses that guard).
              Use for dev re-seeds or a one-time correction; NOT routine in production,
              where a sealed day must never be rewritten.

    python manage.py rebuild_daily_summary                       # all businesses, fill gaps
    python manage.py rebuild_daily_summary --business my-shop    # one business
    python manage.py rebuild_daily_summary --wipe                # reseal everything from scratch
"""
from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum, Count
from django.utils import timezone

from user.models import BusinessProfile
from activity.models import DailyClose
from Sales.models import Sale, SaleItem, SalesPayment, SalesReturn, SalesReturnItem
from Expense.models import Purchase, Waste, Expense, PurchasePayment, PurchaseReturn
from Employee.models import Shift
from core.utils.profit import COGS_LINE, RETURNED_COGS_LINE, net_profit

ZERO = Decimal('0')


class Command(BaseCommand):
    help = 'Seal every complete past day into DailyClose (both accrual + cash lenses).'

    def add_arguments(self, parser):
        parser.add_argument('--business', default=None,
                            help='Slug of a single business (default: every business).')
        parser.add_argument('--wipe', action='store_true',
                            help='Delete existing snapshots first, then recompute all of them '
                                 '(corrects stale rows; append-only guard is bypassed via queryset).')

    def handle(self, *args, **opts):
        today = timezone.localdate()
        qs = BusinessProfile.objects.all()
        if opts['business']:
            qs = qs.filter(slug=opts['business'])
            if not qs.exists():
                self.stderr.write(self.style.ERROR(f"No business with slug {opts['business']!r}."))
                return

        for biz in qs:
            created = self._rebuild(biz, today, wipe=opts['wipe'])
            self.stdout.write(self.style.SUCCESS(
                f"{biz.business_name}: sealed {created:,} day(s)"
                f"{' (wiped first)' if opts['wipe'] else ''}."))

    @transaction.atomic
    def _rebuild(self, business, today, *, wipe):
        if wipe:
            # Queryset delete — bypasses DailyClose.delete()'s append-only guard on purpose.
            DailyClose.objects.filter(business=business).delete()

        have = set(DailyClose.objects.filter(business=business)
                   .values_list('date', flat=True))

        # ── ACCRUAL per day (dated by the record's own accrual date, net of returns) ──
        def by(qs, field, expr):
            return {r[field]: (r['v'] or ZERO)
                    for r in qs.values(field).annotate(v=Sum(expr))}

        sales     = Sale.objects.active().filter(business=business)
        purchases = Purchase.objects.filter(business=business, is_void=False)
        sret_qs   = SalesReturn.objects.filter(business=business)
        pret_qs   = PurchaseReturn.objects.filter(business=business)

        revenue  = by(sales, 'date', 'total_revenue')
        material = by(purchases, 'purchase_date', 'total_cost')
        waste    = by(Waste.objects.filter(business=business), 'date', 'total_cost')
        expense  = by(Expense.objects.filter(business=business), 'date', 'total_amount')
        salary   = by(Shift.objects.filter(business=business), 'date', 'amount')
        sret     = by(sret_qs, 'date', 'refund_total')
        pret     = by(pret_qs, 'date', 'refund_total')
        cogs     = {r['sale__date']: (r['v'] or ZERO) for r in SaleItem.objects
                    .filter(sale__in=sales).values('sale__date').annotate(v=Sum(COGS_LINE))}
        ret_cogs = {r['sales_return__date']: (r['v'] or ZERO) for r in SalesReturnItem.objects
                    .filter(sales_return__in=sret_qs, original_sale_item__isnull=False)
                    .values('sales_return__date').annotate(v=Sum(RETURNED_COGS_LINE))}

        # ── CASH per day (dated by PAYMENT date, net of cash refunds) ──
        collected = {r['date']: (r['v'] or ZERO) for r in SalesPayment.objects
                     .filter(business=business).exclude(method='credit')
                     .values('date').annotate(v=Sum('amount'))}
        paid      = by(PurchasePayment.objects.filter(business=business), 'date', 'amount')
        sret_cash = by(sret_qs, 'date', 'refund_cash')
        pret_cash = by(pret_qs, 'date', 'refund_cash')

        # ── SALES RECORDS list rollup — GROSS revenue + count over completed (incl void) ──
        # The exact set the Sales Records page paginates; immutable once the day passes.
        completed_all  = Sale.objects.filter(business=business, status='completed')
        completed_rev  = by(completed_all, 'date', 'total_revenue')
        completed_cnt  = {r['date']: r['n'] for r in
                          completed_all.values('date').annotate(n=Count('id'))}

        # ── PURCHASE RECORDS list rollup — active gross cost + active count, plus the void-
        # inclusive count the page's paginator needs (voids paginate inline). `material` above
        # is already Σ active total_cost by purchase_date (gross of returns) — reuse it. ──
        purchase_cnt     = {r['purchase_date']: r['n'] for r in
                            purchases.values('purchase_date').annotate(n=Count('id'))}
        purchase_all     = Purchase.objects.filter(business=business)
        purchase_cnt_all = {r['purchase_date']: r['n'] for r in
                            purchase_all.values('purchase_date').annotate(n=Count('id'))}

        # Every past day touched by ANY stream (returns can make a day appear on their own;
        # a day whose only sales were all voided still needs its completed rollup sealed).
        days = (set(revenue) | set(material) | set(waste) | set(expense) | set(salary)
                | set(sret) | set(pret) | set(cogs) | set(ret_cogs)
                | set(collected) | set(paid) | set(sret_cash) | set(pret_cash)
                | set(completed_rev) | set(completed_cnt)
                | set(purchase_cnt) | set(purchase_cnt_all))

        rows = []
        for d in days:
            if d is None or d >= today or d in have:
                continue   # today stays live; sealed days are left alone
            net_cogs = (cogs.get(d, ZERO)) - (ret_cogs.get(d, ZERO))
            rows.append(DailyClose(
                business=business, date=d,
                # accrual (net of returns) — mirrors the summary_list row exactly
                total_revenue=revenue.get(d, ZERO) - sret.get(d, ZERO),
                total_cogs=net_cogs,
                total_material_cost=material.get(d, ZERO) - pret.get(d, ZERO),
                total_salary_cost=salary.get(d, ZERO),
                total_waste_cost=waste.get(d, ZERO),
                total_expense_cost=expense.get(d, ZERO),
                net_profit=net_profit(
                    revenue.get(d, ZERO), net_cogs, salary.get(d, ZERO),
                    waste.get(d, ZERO), expense.get(d, ZERO), sret.get(d, ZERO)),
                # cash (net of cash refunds) — mirrors cash_summary_list
                collected=collected.get(d, ZERO) - sret_cash.get(d, ZERO),
                paid=paid.get(d, ZERO) - pret_cash.get(d, ZERO),
                cash_expense=expense.get(d, ZERO),
                cash_payroll=salary.get(d, ZERO),
                # sales-list rollup (gross completed incl void)
                completed_revenue=completed_rev.get(d, ZERO),
                completed_count=completed_cnt.get(d, 0),
                # purchase-list rollup (active gross cost + active count + void-inclusive count)
                purchase_cost=material.get(d, ZERO),
                purchase_count=purchase_cnt.get(d, 0),
                purchase_count_all=purchase_cnt_all.get(d, 0),
            ))

        if rows:
            DailyClose.objects.bulk_create(rows, ignore_conflicts=True)
        return len(rows)
