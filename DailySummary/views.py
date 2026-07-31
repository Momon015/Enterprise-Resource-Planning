from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404
from django.views.generic import ListView, UpdateView, CreateView, DeleteView, FormView, DetailView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages

from django.utils import timezone
from datetime import timedelta
import random

from django.views.decorators.http import require_POST
from django.urls import reverse

from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth import update_session_auth_hash

from Sales.models import (Sale, SaleItem, SaleEmployee, SalesPayment, SalesReturn,
                          SalesReturnItem)
from Sales.forms import SaleForm, SaleFilterForm

from Product.models import Product
from Product.forms import ProductForm

from Expense.models import (Purchase, PurchaseItem, Waste, WasteItem, Expense,
                            PurchasePayment, PurchaseReturn)
from Employee.models import Employee, Shift, ShiftEmployee
from Employee.forms import EmployeeForm

from core.models import StatusModel

# THE accrual profit formula — one definition, shared with the Dashboard and Analytics.
# Importing it (rather than re-typing `revenue - cost - ...` here) is what stops this page
# from drifting out of step with them again.
#
# 2026-07-13: profit now subtracts COST OF GOODS SOLD, not stock purchased. The accrual
# table's cost column changed with it — see the per-day fold below.
from core.utils.returns import _total, sales_returns_total
from core.utils.profit import COGS_LINE, RETURNED_COGS_LINE, cogs_in, net_profit

from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from urllib.parse import urlencode
from django.views.decorators.http import require_POST

from django.core.paginator import Paginator

from django.db.models import Q, F
from datetime import date, datetime
import calendar
from django.db.models import Sum, Avg, Max, Count

from DailySummary.forms import SummaryFilterForm

from activity.models import DailyClose

from user.models import User

from decimal import Decimal
from operator import itemgetter

from core.utils.owner import  get_owner, permission_required, get_queryset_for_user, get_business_for_user

# logging
import logging

# Create your views here.


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def view_summary(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    basis = request.GET.get('basis', 'cash')   # 'cash' (Cash Flow) default | 'accrual' (Business Performance)

    # Base querysets — unfiltered, used for year-wide aggregates (e.g. "best month")
    all_sales     = get_queryset_for_user(request.user, Sale.objects.active()).filter(business=business)
    all_purchases = get_queryset_for_user(request.user, Purchase.objects.active()).filter(business=business)
    all_wastes    = get_queryset_for_user(request.user, Waste.objects.all()).filter(business=business)
    all_expenses  = get_queryset_for_user(request.user, Expense.objects.all()).filter(business=business)
    all_shifts    = get_queryset_for_user(request.user, Shift.objects.all()).filter(business=business)

    # Working copies — these get filtered below for the daily summary table
    sales     = all_sales
    purchases = all_purchases
    wastes    = all_wastes
    expenses  = all_expenses
    shifts    = all_shifts

    form = SummaryFilterForm(request.GET or None)

    # ── The date filter, resolved ONCE ───────────────────────────────────────────
    # This used to be re-applied inside six separate `if period ==` branches, once per
    # queryset. Two bugs grew out of that duplication (both fixed 2026-07-13):
    #
    #   1. SALARY HAD TWO DEFINITIONS. The unfiltered path summed Shift.amount — which was
    #      0 on every row — while the filtered path summed shift_employees__daily_rate.
    #      So the DEFAULT view (no query params -> unbound form -> is_valid() False)
    #      reported ₱0 payroll and overstated net profit.
    #      2026-07-17: both now sum **Shift.amount**, and the reason the old one read 0
    #      is gone — Shift.recompute_amount() + a ShiftEmployee signal keep the column
    #      equal to Σ daily_rate, and migration 0013 backfilled every historical row.
    #      Shift.amount is now THE definition everywhere (a plain column can't fan out
    #      across a join the way the reverse relation could). See Employee/models.py.
    #
    #   2. THE FREEZE WAS NON-DETERMINISTIC. close_day() trusts the row it's handed, so
    #      whichever request first read a past day decided its books forever — land on the
    #      page unfiltered and salary froze at 0, land on it filtered and it froze at 400.
    #
    # One spec, applied to every queryset, kills that whole class of bug — and it means a
    # new queryset (like the two return streams below) is filtered correctly for free.
    date_filters = []   # (lookup suffix, value); '' suffix means the field itself

    period = request.GET.get('period', '')
    period = {'this_week': 'week', 'this_month': 'month'}.get(period, period)
    # Strip weekly filter for plans that don't include it
    if period in ('week', 'last_week') and not getattr(business.plan, 'has_weekly_summary', lambda: False)():
        period = ''


    today = timezone.localdate()
    
    iso_year, iso_week, iso_weekday = today.isocalendar()

    current_year = today.strftime('%Y-%m')   # zero-padded month (fixes 2026-010 for Oct-Dec)
    
    if form.is_valid():
        start_date = form.cleaned_data.get('start_date', '')
        end_date = form.cleaned_data.get('end_date', '')
        select_month = form.cleaned_data.get('select_month', '')

        if start_date and end_date:
            date_filters = [('range', (start_date, end_date))]

        if select_month:
            parsed_year, parsed_month = map(int, select_month.split('-'))
            date_filters = [('month', parsed_month), ('year', parsed_year)]

        if period == 'last_week':
            if iso_week == 1:
                last_year = iso_year - 1
                last_year_of_last_week = date(last_year, 12, 28).isocalendar()[1]
                date_filters = [('week', last_year_of_last_week), ('year', last_year)]
            else:
                date_filters = [('week', iso_week - 1), ('year', iso_year)]

        if period == 'week':
            date_filters = [('week', iso_week), ('year', iso_year)]

        if period == 'today':
            date_filters = [('', today)]

        if period == 'month':
            date_filters = [('month', today.month), ('year', today.year)]

        """
        I removed search filter for summary because
        when you search something like the revenue
        other aggregated values became 0 it got
        excluded whensearch filter is active. To
        make the filter accurate. I decided to
        remove it completely in this view summary.
        """

    def in_period(qs, field):
        """Apply the resolved window to any queryset, whatever its date column is called
        (Purchase dates on `purchase_date`, everything else on `date`)."""
        if not date_filters:
            return qs
        return qs.filter(**{
            (f'{field}__{suffix}' if suffix else field): value
            for suffix, value in date_filters
        })

    sales     = in_period(all_sales,     'date')
    purchases = in_period(all_purchases, 'purchase_date')
    wastes    = in_period(all_wastes,    'date')
    expenses  = in_period(all_expenses,  'date')
    shifts    = in_period(all_shifts,    'date')

    # The two return streams, dated by the RETURN's own date — a July refund against a
    # June sale belongs to July. Same window, same helper, so they can't drift out of
    # step with the five above.
    sales_returns_qs    = in_period(
        SalesReturn.objects.filter(business=business), 'date')
    purchase_returns_qs = in_period(
        PurchaseReturn.objects.filter(business=business), 'date')

    # ══ Accrual table = READ closed days from DailyClose, live-aggregate only TODAY ══
    # A closed day is immutable (pen-not-pencil), so re-summing its millions of raw rows
    # every load was pure waste — the frozen snapshot already IS the correct number. We
    # now read that snapshot (~8ms for a year of days) and live-aggregate only the
    # unfrozen tail: days AFTER the newest sealed day, which is normally just today. That
    # is the whole speed win — cost is O(days on screen), not O(every sale ever booked).
    #
    #   last_frozen = None on a brand-new business (nothing sealed yet) → the whole history
    #   is "unfrozen" and gets aggregated live, which is correct (there's little of it) and
    #   establishes the first snapshots.
    last_frozen = DailyClose.objects.filter(business=business).aggregate(m=Max('date'))['m']

    def _unfrozen(qs, field):
        """Restrict a stream to days not yet sealed. Freezes are contiguous from the past
        forward (backfill + daily close), so `date > last_frozen` is exactly the open tail."""
        return qs if last_frozen is None else qs.filter(**{f'{field}__gt': last_frozen})

    # Accrual streams, restricted to the unfrozen tail. Salary is Shift.amount, the same
    # column the Dashboard and Expense Analytics use (kept = Σ daily_rate by the signal).
    a_sales = _unfrozen(all_sales, 'date')
    a_sret  = _unfrozen(SalesReturn.objects.filter(business=business), 'date')
    sales_by_date       = a_sales.values('date').annotate(v=Sum('total_revenue'))
    purchase_by_date    = _unfrozen(all_purchases, 'purchase_date').values('purchase_date').annotate(v=Sum('total_cost'))
    wastes_by_date      = _unfrozen(all_wastes, 'date').values('date').annotate(v=Sum('total_cost'))
    expenses_by_date    = _unfrozen(all_expenses, 'date').values('date').annotate(v=Sum('total_amount'))
    shifts_live_by_date = _unfrozen(all_shifts, 'date').values('date').annotate(v=Sum('amount'))
    sales_ret_by_date   = a_sret.values('date').annotate(v=Sum('refund_total'))
    purch_ret_by_date   = _unfrozen(PurchaseReturn.objects.filter(business=business), 'date').values('date').annotate(v=Sum('refund_total'))

    # COST OF GOODS SOLD — what profit subtracts. Grouped by the parent SALE's date (a line
    # item has no date of its own), relieved by the cost of anything brought back. Same
    # unfrozen tail via the sale/return querysets. See core/utils/profit.py.
    cogs_by_date      = (SaleItem.objects.filter(sale__in=a_sales)
                         .values('sale__date').annotate(v=Sum(COGS_LINE)))
    ret_cogs_by_date  = (SalesReturnItem.objects
                         .filter(sales_return__in=a_sret, original_sale_item__isnull=False)
                         .values('sales_return__date').annotate(v=Sum(RETURNED_COGS_LINE)))

    # ── Fold the nine streams into one row per (unfrozen) day ─────────────────────
    # Identical shape and arithmetic to before — only the day SET is smaller (the open
    # tail), because closed days are served from the snapshot instead of recomputed.
    STREAMS = (
        (sales_by_date,       'date',                'total_revenue'),
        (purchase_by_date,    'purchase_date',       'total_material_cost'),
        (wastes_by_date,      'date',                'total_waste_cost'),
        (expenses_by_date,    'date',                'total_expense_cost'),
        (shifts_live_by_date, 'date',                'total_salary_cost'),
        (sales_ret_by_date,   'date',                'sales_returns'),
        (purch_ret_by_date,   'date',                'purchase_returns'),
        (cogs_by_date,        'sale__date',          'total_cogs'),
        (ret_cogs_by_date,    'sales_return__date',  'returned_cogs'),
    )
    FIELDS = tuple(field for _rows, _date_key, field in STREAMS)

    summary = defaultdict(lambda: dict.fromkeys(FIELDS, Decimal('0')))
    for rows, date_key, field in STREAMS:
        for row in rows:
            summary[row[date_key]][field] = row['v'] or Decimal('0')

    # One dict per unfrozen day, NET of returns — the arithmetic is byte-for-byte the old
    # per-day computation, just applied to the tail instead of all-time.
    live_list = []
    for day, v in summary.items():
        net_revenue  = v['total_revenue']       - v['sales_returns']
        net_material = v['total_material_cost'] - v['purchase_returns']
        net_cogs     = v['total_cogs']          - v['returned_cogs']
        day_net = net_profit(
            v['total_revenue'], net_cogs, v['total_salary_cost'],
            v['total_waste_cost'], v['total_expense_cost'],
            v['sales_returns'],
        )
        live_list.append({
            'date': day,
            'total_revenue':       net_revenue,
            'total_cogs':          net_cogs,
            'total_material_cost': net_material,
            'total_salary_cost':   v['total_salary_cost'],
            'total_waste_cost':    v['total_waste_cost'],
            'total_expense_cost':  v['total_expense_cost'],
            'total_cost': (net_cogs + v['total_salary_cost']
                           + v['total_waste_cost'] + v['total_expense_cost']),
            'net_profit': day_net,
        })

    from Sales.models import SalesPayment
    from Expense.models import PurchasePayment

    # ── Compute EVERY lens's per-day figure for the unfrozen tail BEFORE sealing ──────
    # close_days creates each DailyClose row exactly once ("first close wins"), so every
    # column the row will ever hold must be ready NOW — accrual (already on the live_list
    # rows), CASH (by payment date), and the Sales Records list rollup (gross completed incl
    # void). ★ Computing cash AFTER the freeze is why a lazily-sealed day used to store cash=0
    # forever (the nightly rebuild's fill-gaps mode won't overwrite a sealed row). All tail.
    live_collected = {r['date']: r['t'] for r in
        _unfrozen(SalesPayment.objects.filter(business=business, sale__in=all_sales), 'date')
        .exclude(method='credit').values('date').annotate(t=Sum('amount'))}
    live_paid = {r['date']: r['t'] for r in
        _unfrozen(PurchasePayment.objects.filter(business=business, purchase__in=all_purchases), 'date')
        .values('date').annotate(t=Sum('amount'))}
    live_cash_exp = {r['date']: r['t'] for r in
        _unfrozen(all_expenses, 'date').values('date').annotate(t=Sum('total_amount'))}
    live_cash_pay = {r['date']: (r['v'] or Decimal('0')) for r in shifts_live_by_date}
    live_sret_cash = {r['date']: r['t'] for r in
        _unfrozen(SalesReturn.objects.filter(business=business), 'date').values('date').annotate(t=Sum('refund_cash'))}
    live_pret_cash = {r['date']: r['t'] for r in
        _unfrozen(PurchaseReturn.objects.filter(business=business), 'date').values('date').annotate(t=Sum('refund_cash'))}

    # Sales Records list rollup — GROSS revenue + count over completed (incl void).
    _completed_tail = _unfrozen(Sale.objects.filter(business=business, status='completed'), 'date')
    live_completed_rev = {r['date']: (r['t'] or Decimal('0'))
                          for r in _completed_tail.values('date').annotate(t=Sum('total_revenue'))}
    live_completed_cnt = {r['date']: r['n']
                          for r in _completed_tail.values('date').annotate(n=Count('id'))}

    # Purchase Records list rollup — business-wide (NOT viewer-scoped, like completed_* above),
    # keyed on purchase_date. cost + active count feed the page's total/average; the void-
    # inclusive count seeds its paginator (voids paginate inline). GROSS of purchase returns.
    _purch_active_tail = _unfrozen(Purchase.objects.filter(business=business, is_void=False), 'purchase_date')
    _purch_all_tail    = _unfrozen(Purchase.objects.filter(business=business), 'purchase_date')
    live_purchase_cost = {r['purchase_date']: (r['t'] or Decimal('0'))
                          for r in _purch_active_tail.values('purchase_date').annotate(t=Sum('total_cost'))}
    live_purchase_cnt  = {r['purchase_date']: r['n']
                          for r in _purch_active_tail.values('purchase_date').annotate(n=Count('id'))}
    live_purchase_cnt_all = {r['purchase_date']: r['n']
                             for r in _purch_all_tail.values('purchase_date').annotate(n=Count('id'))}

    for r in live_list:
        d = r['date']
        r['collected']          = (live_collected.get(d) or Decimal('0')) - (live_sret_cash.get(d) or Decimal('0'))
        r['paid']               = (live_paid.get(d) or Decimal('0'))      - (live_pret_cash.get(d) or Decimal('0'))
        r['cash_expense']       = live_cash_exp.get(d) or Decimal('0')
        r['cash_payroll']       = live_cash_pay.get(d) or Decimal('0')
        r['completed_revenue']  = live_completed_rev.get(d) or Decimal('0')
        r['completed_count']    = live_completed_cnt.get(d, 0)
        r['purchase_cost']      = live_purchase_cost.get(d) or Decimal('0')
        r['purchase_count']     = live_purchase_cnt.get(d, 0)
        r['purchase_count_all'] = live_purchase_cnt_all.get(d, 0)

    # ── Freeze the unfrozen PAST days (today stays live & editable). "First close wins"
    #    (pen-not-pencil) — a later void/edit posts forward, never rewrites a sealed day. ──
    from activity.utils import close_days
    close_days(business, [r for r in live_list if r['date'] < today])

    # ── Assemble the DISPLAY list = frozen days (from the snapshot, windowed) + today.
    #    Reading AFTER the freeze means any day we just sealed is already in the snapshot,
    #    so the only rows still sourced from live_list are today (and any open gap-day the
    #    window happens to include). _in_window mirrors in_period() for the python side. ──
    def _in_window(d):
        for suffix, value in date_filters:
            if suffix == '':
                if d != value: return False
            elif suffix == 'range':
                start, end = value
                if not (start <= d <= end): return False
            elif suffix == 'month':
                if d.month != value: return False
            elif suffix == 'year':
                if d.year != value: return False
            elif suffix == 'week':
                if d.isocalendar()[1] != value: return False
        return True

    def _row_from_close(c):
        # Serve the FROZEN figures verbatim (pen-not-pencil) — identical to the old
        # overwrite, just sourced without first recomputing the day.
        return {
            'date': c.date,
            'total_revenue':       c.total_revenue,
            'total_cogs':          c.total_cogs,
            'total_material_cost': c.total_material_cost,
            'total_salary_cost':   c.total_salary_cost,
            'total_waste_cost':    c.total_waste_cost,
            'total_expense_cost':  c.total_expense_cost,
            'total_cost': (c.total_cogs + c.total_salary_cost
                           + c.total_waste_cost + c.total_expense_cost),
            'net_profit': c.net_profit,
            'is_closed': True,
            'closed_at': c.closed_at,
        }

    frozen_window = {c.date: c for c in
                     in_period(DailyClose.objects.filter(business=business), 'date')}
    sorted_list = [_row_from_close(c) for c in frozen_window.values()]
    for r in live_list:
        if r['date'] in frozen_window:        # sealed a moment ago — already added above
            continue
        if _in_window(r['date']):             # today (or a still-open gap day) in this window
            r['is_closed'] = False
            r['closed_at'] = None
            sorted_list.append(r)
    sorted_list.sort(key=lambda x: x['date'], reverse=True)

    # ── Grand totals = the SUM OF THE ROWS ON SCREEN ─────────────────────────────
    # These used to be accumulated from the LIVE figures before the freeze ran, so once a
    # day was closed the cards could quietly disagree with the rows underneath them. Now
    # they add up exactly what the reader can see.
    grand_total_revenue       = sum((r['total_revenue']       for r in sorted_list), Decimal('0'))
    grand_total_cogs          = sum((r['total_cogs']          for r in sorted_list), Decimal('0'))
    grand_material_total_cost = sum((r['total_material_cost'] for r in sorted_list), Decimal('0'))
    grand_total_salary_cost   = sum((r['total_salary_cost']   for r in sorted_list), Decimal('0'))
    grand_total_waste_cost    = sum((r['total_waste_cost']    for r in sorted_list), Decimal('0'))
    grand_total_expense_cost  = sum((r['total_expense_cost']  for r in sorted_list), Decimal('0'))
    grand_net_profit          = sum((r['net_profit']          for r in sorted_list), Decimal('0'))

    # The window's refund totals. Shown as a "− ₱x returned" line on the Revenue and
    # Expense cards rather than as two mostly-empty table columns.
    grand_sales_returns    = _total(sales_returns_qs)
    grand_purchase_returns = _total(purchase_returns_qs)

    # Gross = what the net figures above were derived FROM. The cards show the working
    # ("₱755.00 − ₱47.00 returned") so a number that shrank doesn't read as a lost sale.
    grand_gross_revenue  = grand_total_revenue + grand_sales_returns
    grand_gross_material = grand_material_total_cost + grand_purchase_returns

    # ── Receivables (sales) from the denormalized `outstanding` column, NOT a payment scan.
    # outstanding = total_revenue − amount_paid − credit per sale (kept correct on every write
    # by recompute_outstanding(), partial-indexed on > 0). So Σ outstanding over the window IS
    # receivables, and grand_collected is DERIVED from it (gross − outstanding − credit) — the
    # same number the old SalesPayment scan produced, without touching the payments table. ──
    grand_sales_credit    = sales_returns_qs.aggregate(t=Sum('refund_credit'))['t'] or Decimal('0')
    # `outstanding__gt=0` hits the PARTIAL index (unpaid sales only) — so this touches the
    # handful of open receivables, not every sale in the window. Paid sales sit at 0 and add
    # nothing; overpayments (negative) aren't receivables. This is what the `gt` index is for.
    grand_receivables = sales.filter(outstanding__gt=0).aggregate(t=Sum('outstanding'))['t'] or Decimal('0')
    grand_collected   = grand_gross_revenue - grand_receivables - grand_sales_credit

    # Payables side keeps the direct payment sum (cheap — few supplier payments) so it stays
    # correct even where Purchase.outstanding hasn't been backfilled (e.g. bulk-seeded rows).
    grand_paid            = PurchasePayment.objects.filter(purchase__in=purchases).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    grand_purchase_credit = purchase_returns_qs.aggregate(t=Sum('refund_credit'))['t'] or Decimal('0')
    grand_payables    = grand_gross_material - grand_paid      - grand_purchase_credit

    # Accrual Expense Cost card = payroll + other expenses + waste. Summed in Python so
    # it's exact (template |add truncates Decimals to int) and reconciles with Net Profit.
    grand_expense_cost = grand_total_salary_cost + grand_total_expense_cost + grand_total_waste_cost

    pagination = Paginator(sorted_list, 20)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    
    # "Best month" this YEAR (independent of the page's date filter). It used to recompute
    # six live year-wide aggregates (revenue/COGS/waste/expense/salary/returns by month) over
    # every sale in the year — the #4/#6 hot queries. But net_profit is LINEAR in its args
    # (see core/utils/profit.py), so a month's profit is exactly the SUM of its days' profits
    # — and each day's profit is already sealed in DailyClose. So we sum the frozen daily
    # net_profit per month (~ a year of tiny rows) and add the still-open tail (today) from
    # live_list. Same number the old formula produced, without touching the transaction tables.
    month_net = defaultdict(lambda: Decimal('0'))
    year_frozen_dates = set()
    for c in DailyClose.objects.filter(business=business, date__year=today.year):
        month_net[c.date.month] += c.net_profit
        year_frozen_dates.add(c.date)
    for r in live_list:
        if r['date'].year == today.year and r['date'] not in year_frozen_dates:
            month_net[r['date'].month] += r['net_profit']

    best_month_name = 'N/A'
    best_month_profit = 0   # months with negative profit won't beat 0 — kept N/A
    for m, net in month_net.items():
        if net > best_month_profit:
            best_month_profit = net
            best_month_name = calendar.month_name[m]
            
    days_recorded = len(sorted_list)
    
    # Profit margin (net / revenue)
    if grand_total_revenue > 0:
        profit_margin = (grand_net_profit / grand_total_revenue) * 100
    else:
        profit_margin = 0
    
    # Days profitable 
    days_profitable = sum(1 for d in sorted_list if d['net_profit'] > 0)

    # Best / Worst day (by net_profit)
    best_day = 0
    worst_day = 0
    if sorted_list:
        best_day = max(sorted_list, key=lambda d: d['net_profit'])
        worst_day = min(sorted_list, key=lambda d: d['net_profit'])
        
    # ══ CASH FLOW (by PAYMENT date) — read CLOSED days from the snapshot, live today ══
    # Same design as the accrual table: a sealed day's cash figures already live in
    # DailyClose (collected / paid / cash_expense / cash_payroll, net of cash refunds), so we
    # READ them instead of re-summing every payment. Only the unfrozen tail (today) is live.
    # Scope is active sales/purchases only (a voided sale's payment isn't cash collected) —
    # the snapshot was sealed from those same actives, so the scope is preserved.

    # Window refund totals — the returns table is tiny, so always live. They drive the
    # "gross − returned" working on the cash Revenue / Material cards.
    cash_sales_refunds = sales_returns_qs.aggregate(t=Sum('refund_cash'))['t'] or Decimal('0')
    cash_purch_refunds = purchase_returns_qs.aggregate(t=Sum('refund_cash'))['t'] or Decimal('0')

    # Method breakdowns (Cash/GCash/Bank split) are now LAZY — the ▼ popover fetches them via
    # htmx on first open (summary_method_breakdown view), so the GROUP-BY-over-all-payments
    # scan — the LAST O(payments) query on this page — leaves the hot path entirely. The card
    # TOTALS already come from the snapshot, so the card shows instantly and only the dropdown
    # detail is fetched on click. These keys stay defined (empty) for the context/templates.
    collected_by_method = paid_by_method = []
    collected_by_method_acc = paid_by_method_acc = []
    # Store-credit footnote (cash Money-in popover only) — small, cash-lens; kept inline so the
    # popover's total reconciles without a second fetch.
    cash_store_credit = 0
    if basis == 'cash':
        cash_store_credit = in_period(
            SalesPayment.objects.filter(business=business, sale__in=all_sales, method='credit'),
            'date').aggregate(t=Sum('amount'))['t'] or 0

    # ── Cash table = sealed days from the snapshot + today live (the only open day) ──
    # The unfrozen-tail maps (live_collected / live_paid / live_cash_exp / live_cash_pay /
    # live_sret_cash / live_pret_cash) were computed ABOVE, before the freeze, so a sealed
    # day stores real cash. Reuse them here for the display of the still-open day(s).
    cash_summary_list = []
    for c in frozen_window.values():          # sealed days — read straight from the snapshot
        spent = c.paid + c.cash_expense + c.cash_payroll
        cash_summary_list.append({
            'date': c.date, 'collected': c.collected, 'paid': c.paid,
            'expense': c.cash_expense, 'salary': c.cash_payroll,
            'spent': spent, 'net_cash': c.collected - spent,
        })
    live_cash_days = (set(live_collected) | set(live_paid) | set(live_cash_exp)
                      | set(live_cash_pay) | set(live_sret_cash) | set(live_pret_cash))
    for d in live_cash_days:
        if d in frozen_window or not _in_window(d):   # sealed already, or outside the window
            continue
        collected = (live_collected.get(d) or Decimal('0')) - (live_sret_cash.get(d) or Decimal('0'))
        paid      = (live_paid.get(d) or Decimal('0'))      - (live_pret_cash.get(d) or Decimal('0'))
        expense   = live_cash_exp.get(d) or Decimal('0')
        salary    = live_cash_pay.get(d) or Decimal('0')
        spent     = paid + expense + salary
        cash_summary_list.append({
            'date': d, 'collected': collected, 'paid': paid, 'expense': expense,
            'salary': salary, 'spent': spent, 'net_cash': collected - spent,
        })
    cash_summary_list.sort(key=lambda x: x['date'], reverse=True)

    # Cash totals = SUM of the rows on screen (same principle as the accrual grand totals),
    # so every card reconciles with the table exactly. All already net of cash refunds.
    cash_collected = sum((r['collected'] for r in cash_summary_list), Decimal('0'))
    cash_paid      = sum((r['paid']      for r in cash_summary_list), Decimal('0'))
    cash_salary    = sum((r['salary']    for r in cash_summary_list), Decimal('0'))
    cash_expense   = sum((r['expense']   for r in cash_summary_list), Decimal('0'))
    grand_spent    = sum((r['spent']     for r in cash_summary_list), Decimal('0'))
    cash_opex      = cash_salary + cash_expense
    # Gross = net + the refunds that were netted out — the cards show "gross − returned".
    cash_gross_collected = cash_collected + cash_sales_refunds
    cash_gross_paid      = cash_paid + cash_purch_refunds

    # Cash basis paginates too — override the accrual page_obj built above.
    if basis == 'cash':
        pagination = Paginator(cash_summary_list, 20)
        page_obj = pagination.get_page(request.GET.get('page'))

    # One querystring for every page link — carries all active filters (basis +
    # month/date/period) minus `page`, so pagination never drops a filter.
    _qd = request.GET.copy()
    _qd.pop('page', None)
    _qd['basis'] = basis        # basis defaults in-view, force it into the link
    querystring = _qd.urlencode()

    # Net cash = money in − money out, both by payment date (fully cash-scoped).
    grand_net_cash = (cash_collected or 0) - grand_spent

    # Cash margin (net cash / collected) — the cash-basis twin of profit_margin
    if cash_collected and cash_collected > 0:
        cash_margin = (grand_net_cash / cash_collected) * 100
    else:
        cash_margin = 0
        
    context = {
        'summary_list': sorted_list,
        'page_obj': page_obj,
        'querystring': querystring,
        'section': 'summary',
        'grand_material_total_cost': grand_material_total_cost,
        # What the goods SOLD cost us — the accrual table's cost column and what net profit
        # subtracts. Distinct from grand_material_total_cost (what we PAID suppliers), which
        # the Cash Flow lens still uses.
        'grand_total_cogs': grand_total_cogs,
        'grand_total_revenue': grand_total_revenue,
        'grand_total_waste_cost': grand_total_waste_cost,
        'grand_total_salary_cost': grand_total_salary_cost,
        'grand_total_expense_cost': grand_total_expense_cost,
        'grand_expense_cost': grand_expense_cost,
        'grand_net_profit': grand_net_profit,
        'current_year': current_year,

        # Returns: shown as a "− ₱x returned" line on the Revenue / Expense cards, with
        # the gross beside it so the net figure reads as derived rather than as a number
        # that mysteriously shrank. Not broken out per day — most days have none.
        'grand_sales_returns': grand_sales_returns,
        'grand_purchase_returns': grand_purchase_returns,
        'grand_gross_revenue': grand_gross_revenue,
        'grand_gross_material': grand_gross_material,
        
        'best_month_name': best_month_name,
        'best_month_profit': best_month_profit,
        
        'grand_collected': grand_collected,
        'grand_paid': grand_paid,
        'grand_receivables': grand_receivables,
        'grand_payables': grand_payables,
        'collected_by_method': collected_by_method,
        'paid_by_method': paid_by_method,
        'collected_by_method_acc': collected_by_method_acc,
        'paid_by_method_acc': paid_by_method_acc,
        'cash_collected': cash_collected,
        'cash_paid': cash_paid,
        'cash_store_credit': cash_store_credit,

        # Cash refunds — real money that moved back. Shown as "gross − returned" on the
        # cash Revenue / Material cards, same working as the accrual page.
        'cash_sales_refunds': cash_sales_refunds,
        'cash_purch_refunds': cash_purch_refunds,
        'cash_gross_collected': cash_gross_collected,
        'cash_gross_paid': cash_gross_paid,
        'cash_salary': cash_salary,
        'cash_expense': cash_expense,
        'cash_opex': cash_opex,
        
        'basis': basis,
        'cash_summary_list': cash_summary_list,
        'grand_spent': grand_spent,
        'grand_net_cash': grand_net_cash,
        'cash_margin': cash_margin,

        'days_recorded': days_recorded,
        'profit_margin': profit_margin,
        'days_profitable': days_profitable,
        'best_day': best_day,
        'worst_day': worst_day,
    }
    
    # ?basis= routes to the split templates — Cash Flow vs Accrual are now two
    # standalone pages (single-column + dashboard-style KPI card strip on top).
    template = ('DailySummary/view_summary_cash.html' if basis == 'cash'
                else 'DailySummary/view_summary_accrual.html')
    return render(request, template, context)


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def summary_method_breakdown(request, business_slug):
    """LAZY popover fragment — the Cash/GCash/Bank split for one KPI card, fetched by htmx
    only when the ▼ dropdown is first opened. This is the ONE remaining O(payments) scan
    (a GROUP BY method over every payment in the window); keeping it off the page load is
    what takes the Daily Summary from ~190ms to well under 100ms. The card TOTAL is already
    served from the snapshot, so the card is instant and only this detail is fetched on click.

        ?side=collected|paid   ?lens=cash|accrual
          cash    → by PAYMENT date within the window; store credit excluded from collected
          accrual → by TRANSACTION (payments on the window's sales/purchases); credit kept

    ⚠️ The date-window resolution below mirrors view_summary — it reads the SAME GET params
    (period / select_month / start_date+end_date). Keep the two in step.
    """
    business = get_business_for_user(request.user, business_slug)
    side = request.GET.get('side', 'collected')
    lens = request.GET.get('lens', 'cash')

    # ── window, resolved from the same GET params as the page (mirror of view_summary) ──
    date_filters = []
    period = request.GET.get('period', '')
    period = {'this_week': 'week', 'this_month': 'month'}.get(period, period)
    if period in ('week', 'last_week') and not getattr(business.plan, 'has_weekly_summary', lambda: False)():
        period = ''
    today = timezone.localdate()
    iso_year, iso_week, _ = today.isocalendar()
    form = SummaryFilterForm(request.GET or None)
    if form.is_valid():
        start_date = form.cleaned_data.get('start_date', '')
        end_date = form.cleaned_data.get('end_date', '')
        select_month = form.cleaned_data.get('select_month', '')
        if start_date and end_date:
            date_filters = [('range', (start_date, end_date))]
        if select_month:
            py, pm = map(int, select_month.split('-'))
            date_filters = [('month', pm), ('year', py)]
        if period == 'last_week':
            if iso_week == 1:
                ly = iso_year - 1
                date_filters = [('week', date(ly, 12, 28).isocalendar()[1]), ('year', ly)]
            else:
                date_filters = [('week', iso_week - 1), ('year', iso_year)]
        if period == 'week':
            date_filters = [('week', iso_week), ('year', iso_year)]
        if period == 'today':
            date_filters = [('', today)]
        if period == 'month':
            date_filters = [('month', today.month), ('year', today.year)]

    def in_period(qs, field):
        if not date_filters:
            return qs
        return qs.filter(**{(f'{field}__{suffix}' if suffix else field): value
                            for suffix, value in date_filters})

    all_sales = get_queryset_for_user(request.user, Sale.objects.active()).filter(business=business)
    all_purchases = get_queryset_for_user(request.user, Purchase.objects.active()).filter(business=business)

    if side == 'paid':
        names = dict(PurchasePayment.PAYMENT_METHOD_CHOICES)
        if lens == 'cash':
            qs = in_period(PurchasePayment.objects.filter(business=business, purchase__in=all_purchases), 'date')
        else:
            qs = PurchasePayment.objects.filter(purchase__in=in_period(all_purchases, 'purchase_date'))
    else:  # collected
        names = dict(SalesPayment.PAYMENT_METHOD_CHOICES)
        if lens == 'cash':
            # cash lens excludes store credit (it isn't real cash); accrual keeps it.
            qs = in_period(SalesPayment.objects.filter(business=business, sale__in=all_sales), 'date').exclude(method='credit')
        else:
            qs = SalesPayment.objects.filter(sale__in=in_period(all_sales, 'date'))

    methods = [
        {'label': names.get(r['method'], r['method']), 'amount': r['t']}
        for r in qs.values('method').annotate(t=Sum('amount')).order_by('-t')
    ]
    return render(request, 'DailySummary/partials/_method_rows.html', {'methods': methods})


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def view_summary_detail(request, business_slug, date):
    business = get_business_for_user(request.user, business_slug)

    # Show voided sales/purchases in the day breakdown (lined-out, like Sales Records)
    # rather than hiding them — the owner sees the void happened instead of a row silently
    # vanishing. The money TOTALS below still EXCLUDE voided (a void = cancelled / cash
    # returned; this is a management view, not a BIR X/Z ledger): display lists carry all,
    # the sums use .active().
    sales = Sale.objects.filter(business=business, date=date).prefetch_related('sale_items', 'payments').order_by('-date', '-id')
    sale_items  = SaleItem.objects.filter(sale__in=sales).select_related('product').order_by('product__is_service', 'id')
    sale_employees = SaleEmployee.objects.filter(sale__in=sales)

    # .active(), not .filter(is_void=False) — active() also drops UNCONFIRMED DRAFTS. The
    # old filter let a draft's revenue into the total while COGS (which uses active()) left
    # its cost out, so a parked GCash sale would have inflated this day's profit.
    posted = Sale.objects.active().filter(business=business, date=date)
    total_revenue = posted.aggregate(revenue=Sum('total_revenue'))['revenue'] or 0

    purchases = Purchase.objects.filter(business=business, purchase_date=date).prefetch_related('materials', 'payments').order_by('-purchase_date', '-id')
    purchase_items = PurchaseItem.objects.filter(purchase__in=purchases)
    total_material_cost = purchases.filter(is_void=False).aggregate(material_cost=Sum('total_cost'))['material_cost'] or 0

    wastes = Waste.objects.filter(business=business, date=date)
    waste_items = WasteItem.objects.filter(waste__in=wastes)
    total_waste_cost = wastes.aggregate(waste_cost=Sum('total_cost'))['waste_cost'] or 0

    expenses = Expense.objects.filter(business=business, date=date)
    total_expense_cost = expenses.aggregate(total_expense_cost=Sum('total_amount'))['total_expense_cost'] or 0

    shifts = Shift.objects.filter(business=business, date=date)
    shift_employees = ShiftEmployee.objects.filter(shift__in=shifts)
    total_salary_cost = shift_employees.aggregate(salary_cost=Sum(F('daily_rate')))['salary_cost'] or 0

    # This day's profit used the SAME shape as the old table formula AND silently ignored
    # returns entirely — so a day with a refund on it disagreed with both the Dashboard and
    # the summary table it was opened from. Now it goes through the one shared function, on
    # cost of goods SOLD.
    day_cogs      = cogs_in(business, date, date)
    day_refunds   = sales_returns_total(business, date, date)
    day_net_profit = net_profit(
        total_revenue, day_cogs, total_salary_cost,
        total_waste_cost, total_expense_cost, day_refunds,
    )

    basis = request.GET.get('basis', 'cash')
    # Cash TOTALS exclude voided (a void = cancelled / cash returned; matches view_summary
    # + the dashboard cash lens). The payment LISTS keep voided rows so the template can
    # line them out (like Sales Records) — voided is styled, not counted.
    collected = SalesPayment.objects.filter(business=business, date=date).exclude(sale__is_void=True).aggregate(t=Sum('amount'))['t'] or 0
    paid      = PurchasePayment.objects.filter(business=business, date=date).exclude(purchase__is_void=True).aggregate(t=Sum('amount'))['t'] or 0
    net_cash  = collected - paid - total_expense_cost
    sales_payments    = SalesPayment.objects.filter(business=business, date=date).select_related('sale').prefetch_related('sale__payments').order_by('-date', '-id')
    purchase_payments = PurchasePayment.objects.filter(business=business, date=date).select_related('purchase').prefetch_related('purchase__payments').order_by('-date', '-id')


    # Day-close (freeze) lookup — drives the "This day is closed" banner
    from activity.models import DailyClose
    day_close = DailyClose.objects.filter(business=business, date=date).first()

    # ── Settlement state AS OF this day (frozen-books accuracy) ──
    # A payment made on a LATER day belongs to that day's Cash Flow, not this
    # closed day's detail. So the chip/outstanding only count payments dated
    # ≤ this detail's date; a green "Settled" badge flags balances cleared later.
    detail_date = date if not isinstance(date, str) else datetime.strptime(date, '%Y-%m-%d').date()

    def _settlement_as_of(obj, total, as_of):
        pmts = [p for p in obj.payments.all() if p.date and p.date <= as_of]
        paid_amt = sum((p.amount for p in pmts), Decimal('0'))
        total = total or Decimal('0')
        if paid_amt <= 0:
            return Decimal('0'), total, 'unpaid', 'Debt'
        methods = {p.get_method_display() for p in pmts}
        label = next(iter(methods)) if len(methods) == 1 else 'Mixed'
        if paid_amt < total:
            return paid_amt, total - paid_amt, 'partial', f'Partial · {label}'
        return paid_amt, total - paid_amt, 'paid', label

    def _settled_on(obj, total):
        # date the running total first reached `total` (full settlement), else None
        total = total or Decimal('0')
        if total <= 0:
            return None
        running = Decimal('0')
        for p in sorted(obj.payments.all(), key=lambda x: (x.date or detail_date)):
            running += (p.amount or Decimal('0'))
            if running >= total:
                return p.date
        return None

    # "Now" = current live settlement (ALL payments, incl. those dated after this
    # closed day). The frozen *_asof figures above are never touched; this only drives
    # a read-only "Now:" annotation so an owner isn't confused when a closed-day row
    # still shows Debt even though the customer has since paid (payment posts forward).
    def _method_code_asof(obj, as_of):
        # Which method(s) settled this record AS OF the given day — matches the
        # payment_method_code vocabulary (cash/gcash/bank/credit/mixed) the
        # payment_method_badge tag expects. None when nothing's paid yet.
        methods = {p.method for p in obj.payments.all() if p.date and p.date <= as_of}
        if not methods:
            return None
        return next(iter(methods)) if len(methods) == 1 else 'mixed'

    _now_asof = datetime.max.date()
    for s in sales:
        s.paid_asof, s.outstanding_asof, s.status_asof, s.display_asof = _settlement_as_of(s, s.total_revenue, detail_date)
        s.settled_later = _settled_on(s, s.total_revenue) if s.status_asof != 'paid' else None
        s.paid_now, s.outstanding_now, s.status_now, s.display_now = _settlement_as_of(s, s.total_revenue, _now_asof)
        s.changed_since_close = s.status_now != s.status_asof or s.outstanding_now != s.outstanding_asof
        s.method_code_asof = _method_code_asof(s, detail_date)
        _later = [p.date for p in s.payments.all() if p.date and p.date > detail_date]
        s.last_pmt_date = max(_later) if _later else None
    for pu in purchases:
        pu.paid_asof, pu.outstanding_asof, pu.status_asof, pu.display_asof = _settlement_as_of(pu, pu.total_cost, detail_date)
        pu.settled_later = _settled_on(pu, pu.total_cost) if pu.status_asof != 'paid' else None
        pu.paid_now, pu.outstanding_now, pu.status_now, pu.display_now = _settlement_as_of(pu, pu.total_cost, _now_asof)
        pu.changed_since_close = pu.status_now != pu.status_asof or pu.outstanding_now != pu.outstanding_asof
        pu.method_code_asof = _method_code_asof(pu, detail_date)
        _later = [p.date for p in pu.payments.all() if p.date and p.date > detail_date]
        pu.last_pmt_date = max(_later) if _later else None

    # Cash Flow payment notes — running balance PER PAYMENT (orders same-day payments correctly)
    def _running_state(parent, total, pay, fallback_date):
        total = total or Decimal('0')
        pmts = sorted(parent.payments.all(), key=lambda x: (x.date or fallback_date, x.id))
        running = Decimal('0')
        crossed = None        # the payment that first reaches full
        after = Decimal('0')  # cumulative paid up to & including THIS payment
        before = Decimal('0') # cumulative paid BEFORE this payment (was it already utang?)
        for q in pmts:
            if q.id == pay.id:
                before = running
            running += (q.amount or Decimal('0'))
            if crossed is None and total > 0 and running >= total:
                crossed = q.id
            if q.id == pay.id:
                after = running
                break
        outstanding = total - after
        if after <= 0:
            status = 'unpaid'
        elif after < total:
            status = 'partial'
        else:
            status = 'paid'
        return outstanding, status, (crossed == pay.id), (before > 0)

    for p in sales_payments:
        if p.sale:
            p.pay_outstanding, p.pay_status, p.is_final, had_prior = _running_state(p.sale, p.sale.total_revenue, p, p.date)
            p.is_earlier = bool(p.sale.date and p.sale.date < p.date)
            p.is_settlement = p.is_final and (p.is_earlier or had_prior)
        else:
            p.pay_outstanding, p.pay_status, p.is_final, p.is_earlier, p.is_settlement = 0, 'paid', False, False, False
    for p in purchase_payments:
        if p.purchase:
            p.pay_outstanding, p.pay_status, p.is_final, had_prior = _running_state(p.purchase, p.purchase.total_cost, p, p.date)
            p.is_earlier = bool(p.purchase.purchase_date and p.purchase.purchase_date < p.date)
            p.is_settlement = p.is_final and (p.is_earlier or had_prior)
        else:
            p.pay_outstanding, p.pay_status, p.is_final, p.is_earlier, p.is_settlement = 0, 'paid', False, False, False




    context = {
        'sales': sales,
        'purchases': purchases,
        'sale_items': sale_items,
        'sale_employees': sale_employees,
        'purchase_items': purchase_items,
        'shifts': shifts,
        'shift_employees': shift_employees,
        'wastes': wastes,
        'waste_items': waste_items,
        'net_profit': day_net_profit,
        'total_cogs': day_cogs,
        'sales_returns': day_refunds,
        'total_salary_cost': total_salary_cost,
        'total_material_cost': total_material_cost,
        'total_waste_cost': total_waste_cost,
        'total_revenue': total_revenue,
        'total_expense_cost': total_expense_cost,
        'expenses': expenses,
        'section': 'summary',

        'basis': basis,
        'collected': collected,
        'paid': paid,
        'net_cash': net_cash,
        'sales_payments': sales_payments,
        'purchase_payments': purchase_payments,
        'day_close': day_close,
        'detail_date': detail_date,
    }

    return render(request, 'DailySummary/view_summary_detail.html', context)
