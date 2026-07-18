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
from django.db.models import Sum, Avg

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

    # ONE definition of each per-day figure. Salary is Shift.amount, the same column the
    # Dashboard and Expense Analytics use (kept in step with Σ daily_rate by the signal).
    sales_by_date     = sales.values('date').annotate(v=Sum('total_revenue'))
    purchase_by_date  = purchases.values('purchase_date').annotate(v=Sum('total_cost'))
    wastes_by_date    = wastes.values('date').annotate(v=Sum('total_cost'))
    expenses_by_date  = expenses.values('date').annotate(v=Sum('total_amount'))
    shifts_by_date    = shifts.values('date').annotate(v=Sum('amount'))
    sales_ret_by_date = sales_returns_qs.values('date').annotate(v=Sum('refund_total'))
    purch_ret_by_date = purchase_returns_qs.values('date').annotate(v=Sum('refund_total'))

    # COST OF GOODS SOLD (2026-07-13) — what profit actually subtracts now. Grouped by the
    # parent SALE's date (a line item has no date of its own), and relieved by the cost of
    # anything customers brought back that day. See core/utils/profit.py.
    cogs_by_date      = (SaleItem.objects.filter(sale__in=sales)
                         .values('sale__date').annotate(v=Sum(COGS_LINE)))
    ret_cogs_by_date  = (SalesReturnItem.objects
                         .filter(sales_return__in=sales_returns_qs,
                                 original_sale_item__isnull=False)
                         .values('sales_return__date').annotate(v=Sum(RETURNED_COGS_LINE)))

    # ── Fold the seven streams into one row per day ──────────────────────────────
    # This was five near-identical if/else blocks, each of which had to list every OTHER
    # field as 0 in its `else`. Adding a field meant editing all five — so adding the two
    # return streams that way was a drift trap waiting to happen. A zero-filled default
    # makes a missing day cost nothing to express, and a new stream is one line.
    #
    #   A day can now appear on the strength of a RETURN alone (a refund on a day with no
    #   sales is still a real day in the books). The old shape would have dropped it.
    STREAMS = (
        (sales_by_date,     'date',                'total_revenue'),
        (purchase_by_date,  'purchase_date',       'total_material_cost'),
        (wastes_by_date,    'date',                'total_waste_cost'),
        (expenses_by_date,  'date',                'total_expense_cost'),
        (shifts_by_date,    'date',                'total_salary_cost'),
        (sales_ret_by_date, 'date',                'sales_returns'),
        (purch_ret_by_date, 'date',                'purchase_returns'),
        (cogs_by_date,      'sale__date',          'total_cogs'),
        (ret_cogs_by_date,  'sales_return__date',  'returned_cogs'),
    )
    FIELDS = tuple(field for _rows, _date_key, field in STREAMS)

    summary = defaultdict(lambda: dict.fromkeys(FIELDS, Decimal('0')))
    for rows, date_key, field in STREAMS:
        for row in rows:
            summary[row[date_key]][field] = row['v'] or Decimal('0')

    summary_list = []
    for day, v in summary.items():
        # Every figure is shown NET of returns, so each row's own arithmetic
        # (revenue − costs = net profit) adds up on screen. The returns are NOT broken out
        # per day — most days have none, and two mostly-empty columns would be noise. The
        # window totals appear on the KPI cards above the table instead.
        net_revenue  = v['total_revenue']       - v['sales_returns']
        net_material = v['total_material_cost'] - v['purchase_returns']
        net_cogs     = v['total_cogs']          - v['returned_cogs']

        # COGS, not material cost. This is the ACCRUAL table — it answers "did we trade
        # profitably", so the cost of the goods that left the shelf is what belongs beside
        # the revenue that they earned. What we PAID suppliers that day is a cash question
        # and lives on the Cash Flow page (and Expense Analytics). Mixing them is what made
        # a delivery day look like a disaster.
        day_net = net_profit(
            v['total_revenue'], net_cogs, v['total_salary_cost'],
            v['total_waste_cost'], v['total_expense_cost'],
            v['sales_returns'],
        )

        summary_list.append({
            'date': day,
            'total_revenue':       net_revenue,
            'total_cogs':          net_cogs,
            # Still carried (the freeze stores it, and the Cash Flow lens wants it) — it is
            # simply no longer a column on the accrual table, nor part of `total_cost`.
            'total_material_cost': net_material,
            'total_salary_cost':   v['total_salary_cost'],
            'total_waste_cost':    v['total_waste_cost'],
            'total_expense_cost':  v['total_expense_cost'],
            # All non-revenue costs, summed in Python (template |add truncates Decimals to int).
            'total_cost': (net_cogs + v['total_salary_cost']
                           + v['total_waste_cost'] + v['total_expense_cost']),
            'net_profit': day_net,
        })
            
    from Sales.models import SalesPayment
    from Expense.models import PurchasePayment

    sorted_list = sorted(summary_list, key=lambda x: x['date'], reverse=True)

    # ── Freeze past days: lazy day-rollover accrual close (BIR "pen, not pencil") ──
    # Any day strictly before today is complete (no record can backdate) → safe to
    # snapshot. get_or_create = first close wins; today stays live & editable.
    #
    #   The rows handed to close_day are now NET of returns and carry the one true salary
    #   figure, so a frozen day is finally deterministic. It used to depend on which filter
    #   the first reader happened to have applied.
    from activity.utils import close_day
    for row in sorted_list:
        if row['date'] < today:
            snap, _ = close_day(business, row['date'], row)
            # Serve the FROZEN figures, never the live recompute (pen, not pencil) —
            # a later void/edit must not rewrite a closed day.
            row['total_revenue']       = snap.total_revenue
            row['total_cogs']          = snap.total_cogs
            row['total_material_cost'] = snap.total_material_cost
            row['total_salary_cost']   = snap.total_salary_cost
            row['total_waste_cost']    = snap.total_waste_cost
            row['total_expense_cost']  = snap.total_expense_cost
            row['net_profit']          = snap.net_profit
            row['total_cost'] = (snap.total_cogs + snap.total_salary_cost
                                 + snap.total_waste_cost + snap.total_expense_cost)
            row['is_closed'] = True
            row['closed_at'] = snap.closed_at
        else:
            row['is_closed'] = False
            row['closed_at'] = None

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

    grand_collected   = SalesPayment.objects.filter(sale__in=sales).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    grand_paid        = PurchasePayment.objects.filter(purchase__in=purchases).aggregate(t=Sum('amount'))['t'] or Decimal('0')

    # What's still owed. A CREDIT refund reduces the balance (the customer/supplier simply
    # owes less); a CASH refund doesn't (that money already changed hands). So this nets
    # off only the credit half — which is exactly what Sale.outstanding does per record.
    grand_sales_credit    = sales_returns_qs.aggregate(t=Sum('refund_credit'))['t'] or Decimal('0')
    grand_purchase_credit = purchase_returns_qs.aggregate(t=Sum('refund_credit'))['t'] or Decimal('0')
    grand_receivables = grand_gross_revenue  - grand_collected - grand_sales_credit
    grand_payables    = grand_gross_material - grand_paid      - grand_purchase_credit

    # Accrual Expense Cost card = payroll + other expenses + waste. Summed in Python so
    # it's exact (template |add truncates Decimals to int) and reconciles with Net Profit.
    grand_expense_cost = grand_total_salary_cost + grand_total_expense_cost + grand_total_waste_cost

    pagination = Paginator(sorted_list, 6)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    
    # so the user's filters above don't skew the "best month" result.
    # salary here read Sum('amount') too — the same empty column that zeroed payroll in
    #   the table. A month's "profit" was therefore computed with NO wages in it.
    year = {'date__year': today.year}
    year_sales = all_sales.filter(**year)
    rev_by_month     = {s['date__month']:          s['total'] for s in year_sales.values('date__month').annotate(total=Sum('total_revenue'))}
    waste_by_month   = {w['date__month']:          w['total'] for w in all_wastes.filter(**year).values('date__month').annotate(total=Sum('total_cost'))}
    expense_by_month = {e['date__month']:          e['total'] for e in all_expenses.filter(**year).values('date__month').annotate(total=Sum('total_amount'))}
    salary_by_month  = {s['date__month']:          s['total'] for s in all_shifts.filter(**year).values('date__month').annotate(total=Sum('amount'))}

    # COGS by month, not purchases — "best month" has to use the SAME formula as every row
    # in the table above it, or the badge could crown a month the table says lost money.
    cogs_by_month = {c['sale__date__month']: c['total'] for c in SaleItem.objects.filter(
        sale__in=year_sales).values('sale__date__month').annotate(total=Sum(COGS_LINE))}
    ret_cogs_by_month = {c['sales_return__date__month']: c['total'] for c in
        SalesReturnItem.objects.filter(
            sales_return__business=business, sales_return__date__year=today.year,
            original_sale_item__isnull=False,
        ).values('sales_return__date__month').annotate(total=Sum(RETURNED_COGS_LINE))}

    # Sales returns belong in "best month" too — a month that refunded half its takings was
    # not a good month, and without these it would still look like one. (Purchase returns no
    # longer enter profit at all — they're inventory movement. See core/utils/profit.py.)
    sret_by_month = {r['date__month']: r['total'] for r in SalesReturn.objects.filter(
        business=business, **year).values('date__month').annotate(total=Sum('refund_total'))}

    all_months = (set(rev_by_month) | set(cogs_by_month) | set(waste_by_month)
                  | set(expense_by_month) | set(salary_by_month)
                  | set(sret_by_month))

    best_month_name = 'N/A'
    best_month_profit = 0   # months with negative profit won't beat 0 — kept N/A
    for m in all_months:
        month_cogs = ((cogs_by_month.get(m) or Decimal('0'))
                      - (ret_cogs_by_month.get(m) or Decimal('0')))
        profit = net_profit(
            rev_by_month.get(m)     or Decimal('0'),
            month_cogs,
            salary_by_month.get(m)  or Decimal('0'),
            waste_by_month.get(m)   or Decimal('0'),
            expense_by_month.get(m) or Decimal('0'),
            sret_by_month.get(m)    or Decimal('0'),
        )
        if profit > best_month_profit:
            best_month_profit = profit
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
        
    # ── CASH FLOW data (by PAYMENT date) ──
    # Scope to ACTIVE sales/purchases only — a voided sale is a cancelled transaction
    # (money refunded), so its payment must not count as cash collected/paid. (This is a
    # management cash view, not a BIR X/Z grand-total ledger.) all_sales/all_purchases are
    # the active, business-scoped bases; payment-date filters below still apply.
    # Payments carry their own date, so they use the SAME resolved window as everything
    # else — this was a third hand-rolled copy of the filter branches.
    sales_pmts = in_period(
        SalesPayment.objects.filter(business=business, sale__in=all_sales), 'date')
    purch_pmts = in_period(
        PurchasePayment.objects.filter(business=business, purchase__in=all_purchases), 'date')

    # ── Cash refunds are CASH MOVEMENTS, and this lens was ignoring them ─────────────
    # Fixed 2026-07-13 (user caught the discrepancy). A ₱47 cash refund to a customer is
    # ₱47 that LEFT the drawer; a ₱180 cash refund from a supplier is ₱180 that CAME BACK.
    # Neither was counted, so Net Cash was overstated and the two lenses refused to
    # reconcile. Only the CASH half of a refund belongs here — a credit note moves no money,
    # it just reduces what's owed (which is why it shows up in receivables/payables instead).
    #
    # They net off the side they came from, mirroring the accrual page: a customer refund
    # reduces what we collected, a supplier refund reduces what we paid.
    sales_refund_by_date = {r['date']: r['t'] for r in
        sales_returns_qs.values('date').annotate(t=Sum('refund_cash'))}
    purch_refund_by_date = {r['date']: r['t'] for r in
        purchase_returns_qs.values('date').annotate(t=Sum('refund_cash'))}

    cash_sales_refunds = sales_returns_qs.aggregate(t=Sum('refund_cash'))['t'] or Decimal('0')
    cash_purch_refunds = purchase_returns_qs.aggregate(t=Sum('refund_cash'))['t'] or Decimal('0')

    # Cash lens = money that actually MOVED (by payment date). Store credit isn't
    # real cash, so it's excluded here (keeps this consistent with the method rows below).
    collected_by_date = {r['date']: r['t'] for r in sales_pmts.exclude(method='credit').values('date').annotate(t=Sum('amount'))}
    paid_by_date      = {r['date']: r['t'] for r in purch_pmts.values('date').annotate(t=Sum('amount'))}
    expense_by_date   = {r['date']: r['t'] for r in expenses.values('date').annotate(t=Sum('total_amount'))}

    # Collected-by-method for the Revenue popover (Cash / GCash / …), period-scoped
    # like the dashboard. Store credit isn't real cash, so it's excluded.
    method_names = dict(SalesPayment.PAYMENT_METHOD_CHOICES)
    collected_by_method = [
        {'label': method_names.get(r['method'], r['method']), 'amount': r['t']}
        for r in sales_pmts.exclude(method='credit')
                 .values('method').annotate(t=Sum('amount')).order_by('-t')
    ]

    # Same idea for the Material Cost popover — how supplier payments were made.
    purch_method_names = dict(PurchasePayment.PAYMENT_METHOD_CHOICES)
    paid_by_method = [
        {'label': purch_method_names.get(r['method'], r['method']), 'amount': r['t']}
        for r in purch_pmts.values('method').annotate(t=Sum('amount')).order_by('-t')
    ]

    # Cash-lens totals (by PAYMENT date) = exactly the method-row sums above, so the
    # Cash Flow cards reconcile with their breakdowns. These differ from grand_collected/
    # grand_paid, which are transaction-scoped (payments on THIS period's sales/purchases)
    # and stay the basis for the Accrual page's billed → collected → receivables chain.
    # Both NET of cash refunds — the money genuinely moved back.
    cash_gross_collected = sum((m['amount'] or Decimal('0')) for m in collected_by_method)
    cash_gross_paid      = sum((m['amount'] or Decimal('0')) for m in paid_by_method)
    cash_collected = cash_gross_collected - cash_sales_refunds
    cash_paid      = cash_gross_paid      - cash_purch_refunds

    # Store credit settled on this period's payments but excluded from cash_collected
    # (it isn't real cash). Shown as a footnote on the Money-in popover so the cash
    # figure visibly reconciles to the accrual "Collected" (which keeps store credit).
    cash_store_credit = sales_pmts.filter(method='credit').aggregate(t=Sum('amount'))['t'] or 0

    # Accrual-lens method breakdown — payments on THIS period's sales/purchases (by
    # transaction), so these sum to grand_collected / grand_paid and reconcile on the
    # Accrual page's billed → collected → receivables chain. (Credit kept: on the accrual
    # lens store credit is a valid way a receivable was settled.)
    collected_by_method_acc = [
        {'label': method_names.get(r['method'], r['method']), 'amount': r['t']}
        for r in SalesPayment.objects.filter(sale__in=sales)
                 .values('method').annotate(t=Sum('amount')).order_by('-t')
    ]
    paid_by_method_acc = [
        {'label': purch_method_names.get(r['method'], r['method']), 'amount': r['t']}
        for r in PurchasePayment.objects.filter(purchase__in=purchases)
                 .values('method').annotate(t=Sum('amount')).order_by('-t')
    ]

    # Payroll is cash out too, so the cash lens counts it (by work date) alongside
    # supplier payments + expenses — mirrors the dashboard's cash Expense Cost =
    # payroll + expenses. Waste stays OUT (it's never a cash event).
    salary_by_date = {s['date']: (s['v'] or 0) for s in shifts_by_date}

    cash_summary_list = []
    grand_spent = Decimal('0')

    # A refund-only day is still a day cash moved, so the return maps join the key set —
    # otherwise a day whose only event was a ₱180 supplier refund would vanish.
    all_cash_days = (set(collected_by_date) | set(paid_by_date) | set(expense_by_date)
                     | set(salary_by_date) | set(sales_refund_by_date) | set(purch_refund_by_date))

    for d in all_cash_days:
        collected = (collected_by_date.get(d) or Decimal('0')) - (sales_refund_by_date.get(d) or Decimal('0'))
        paid      = (paid_by_date.get(d) or Decimal('0'))      - (purch_refund_by_date.get(d) or Decimal('0'))
        expense   = expense_by_date.get(d) or Decimal('0')
        salary    = salary_by_date.get(d) or Decimal('0')
        spent     = paid + expense + salary
        cash_summary_list.append({
            'date': d,
            'collected': collected,
            'paid': paid,
            'expense': expense,
            'salary': salary,
            'spent': spent,
            'net_cash': collected - spent,
        })
        grand_spent += spent

    cash_summary_list.sort(key=lambda x: x['date'], reverse=True)

    # Cash Expense Cost card = operating expenses = payroll + other expenses (no waste).
    # Summed from the same per-day maps so it reconciles with the table + its dropdown.
    cash_salary  = sum((v or 0) for v in salary_by_date.values())
    cash_expense = sum((v or 0) for v in expense_by_date.values())
    cash_opex    = cash_salary + cash_expense

    # Cash basis paginates too — override the accrual page_obj built above.
    if basis == 'cash':
        pagination = Paginator(cash_summary_list, 6)
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
