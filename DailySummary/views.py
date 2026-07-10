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

from Sales.models import Sale, SaleItem, SaleEmployee, SalesPayment
from Sales.forms import SaleForm, SaleFilterForm

from Product.models import Product
from Product.forms import ProductForm

from Expense.models import Purchase, PurchaseItem, Waste, WasteItem, Expense, PurchasePayment
from Employee.models import Employee, Shift, ShiftEmployee
from Employee.forms import EmployeeForm

from core.models import StatusModel

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

    grand_net_profit = 0
    grand_material_total_cost = 0
    grand_total_revenue = 0
    grand_total_salary_cost = 0
    grand_total_waste_cost = 0
    grand_total_expense_cost = 0
    
    expenses_by_date = expenses.values('date').annotate(total_expense_cost=Sum('total_amount')).order_by('-date')
    wastes_by_date = wastes.values('date').annotate(total_waste_cost=Sum('total_cost')).order_by('-date')
    sales_by_date = sales.values('date').annotate(total_revenue=Sum('total_revenue')).order_by('-date')
    shifts_by_date = shifts.values('date').annotate(total_salary_cost=Sum('amount')).order_by('-date')
    purchase_by_date = purchases.values('purchase_date').annotate(total_cost=Sum('total_cost')).order_by('-purchase_date')
         
    form = SummaryFilterForm(request.GET or None)
    
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
            sales = sales.filter(date__range=(start_date, end_date))
            purchases = purchases.filter(purchase_date__range=(start_date, end_date))
            wastes = wastes.filter(date__range=(start_date, end_date))
            expenses = expenses.filter(date__range=(start_date, end_date))
            shifts = shifts.filter(date__range=(start_date, end_date))
            
        if select_month:
            parsed_year, parsed_month = map(int, select_month.split('-'))
            sales = sales.filter(date__month=parsed_month, date__year=parsed_year)
            purchases = purchases.filter(purchase_date__month=parsed_month, purchase_date__year=parsed_year)
            wastes = wastes.filter(date__month=parsed_month, date__year=parsed_year)
            expenses = expenses.filter(date__month=parsed_month, date__year=parsed_year)
            shifts = shifts.filter(date__month=parsed_month, date__year=parsed_year)

            
        if period == 'last_week':
            if iso_week == 1:
                last_year = iso_year - 1
                last_year_of_last_week = date(last_year, 12, 28).isocalendar()[1]
                sales = sales.filter(date__week=last_year_of_last_week, date__year=last_year)
                purchases = purchases.filter(purchase_date__week=last_year_of_last_week, purchase_date__year=last_year)
                wastes = wastes.filter(date__week=last_year_of_last_week, date__year=last_year)
                expenses = expenses.filter(date__week=last_year_of_last_week, date__year=last_year)
                shifts = shifts.filter(date__week=last_year_of_last_week, date__year=last_year)
                
            else:
                sales = sales.filter(date__week=iso_week-1, date__year=iso_year)
                purchases = purchases.filter(purchase_date__week=iso_week-1, purchase_date__year=iso_year)
                wastes = wastes.filter(date__week=iso_week-1, date__year=iso_year)
                expenses = expenses.filter(date__week=iso_week-1, date__year=iso_year)
                shifts = shifts.filter(date__week=iso_week-1, date__year=iso_year)
                
        if period == 'week':
            sales = sales.filter(date__week=iso_week, date__year=iso_year)
            purchases = purchases.filter(purchase_date__week=iso_week, purchase_date__year=iso_year)
            wastes = wastes.filter(date__week=iso_week, date__year=iso_year)
            expenses = expenses.filter(date__week=iso_week, date__year=iso_year)
            shifts = shifts.filter(date__week=iso_week, date__year=iso_year)

        if period == 'today':
            sales = sales.filter(date=today)
            purchases = purchases.filter(purchase_date=today)
            wastes = wastes.filter(date=today)
            expenses = expenses.filter(date=today)
            shifts = shifts.filter(date=today)

        if period == 'month':
            sales = sales.filter(date__month=today.month, date__year=today.year)
            purchases = purchases.filter(purchase_date__month=today.month, purchase_date__year=today.year)
            wastes = wastes.filter(date__month=today.month, date__year=today.year)
            expenses = expenses.filter(date__month=today.month, date__year=today.year)
            shifts = shifts.filter(date__month=today.month, date__year=today.year)

        
        sales_by_date = sales.values('date').annotate(total_revenue=Sum('total_revenue')).order_by('-date')
        purchase_by_date = purchases.values('purchase_date').annotate(total_cost=Sum('total_cost')).order_by('-purchase_date')
        wastes_by_date = wastes.values('date').annotate(total_waste_cost=Sum('total_cost')).order_by('-date')
        expenses_by_date = expenses.values('date').annotate(total_expense_cost=Sum('total_amount')).order_by('-date')
        shifts_by_date = shifts.values('date').annotate(total_salary_cost=Sum('shift_employees__daily_rate')).order_by('-date')
        
        """
        I removed search filter for summary because
        when you search something like the revenue 
        other aggregated values became 0 it got  
        excluded whensearch filter is active. To
        make the filter accurate. I decided to 
        remove it completely in this view summary.
        """
        
    summary = {}
    for s in sales_by_date:
        summary[s['date']] = {
            'total_revenue': s['total_revenue'],
            'total_salary_cost': 0,
            'total_waste_cost': 0,
            'total_cost': 0,
            'total_expense_cost': 0,
        }

    for p in purchase_by_date:
        if p['purchase_date'] in summary:
            summary[p['purchase_date']]['total_cost'] = p['total_cost']
        else:
            summary[p['purchase_date']] = {
                'total_revenue': 0,
                'total_salary_cost': 0,
                'total_waste_cost': 0,
                'total_expense_cost': 0,
                'total_cost': p['total_cost']
            }
            
    for w in wastes_by_date:
        if w['date'] in summary:
            summary[w['date']]['total_waste_cost'] = w['total_waste_cost']
            
        else:
            summary[w['date']] = {
                'total_revenue': 0,
                'total_salary_cost': 0,
                'total_cost': 0,
                'total_expense_cost': 0,
                'total_waste_cost': w['total_waste_cost']
                
            }
            
    for e in expenses_by_date:
        if e['date'] in summary:
            summary[e['date']]['total_expense_cost'] = e['total_expense_cost']
        else:
            summary[e['date']] = {
                'total_revenue': 0,
                'total_salary_cost': 0,
                'total_cost': 0,
                'total_waste_cost': 0,
                'total_expense_cost': e['total_expense_cost']
            }
            
    for s in shifts_by_date:
        if s['date'] in summary:
            summary[s['date']]['total_salary_cost'] = s['total_salary_cost']
        else:
            summary[s['date']] = {
                'total_salary_cost': s['total_salary_cost'],
                'total_revenue': 0,
                'total_cost': 0,
                'total_waste_cost': 0,
                'total_expense_cost': 0,
                
            }
            

    summary_list = []
    if summary:
        for date, value in summary.items():
            total_revenue = value['total_revenue']
            total_material_cost = value['total_cost']
            total_salary_cost = value['total_salary_cost']
            total_waste_cost = value['total_waste_cost']
            total_expense_cost = value['total_expense_cost']
            
            net_profit = total_revenue - total_material_cost - total_salary_cost - total_waste_cost - total_expense_cost
            
            grand_total_expense_cost += total_expense_cost
            grand_total_waste_cost += total_waste_cost
            grand_total_revenue += total_revenue
            grand_total_salary_cost += total_salary_cost
            grand_material_total_cost += total_material_cost
            grand_net_profit += net_profit
            
            summary_list.append({
                'date': date,
                'total_salary_cost': total_salary_cost,
                'total_material_cost': total_material_cost,
                'total_revenue': total_revenue,
                'total_waste_cost': total_waste_cost,
                'total_expense_cost': total_expense_cost,
                # All non-revenue costs, summed in Python (template |add truncates Decimals to int).
                'total_cost': total_material_cost + total_salary_cost + total_waste_cost + total_expense_cost,
                'net_profit': net_profit
            })
            
    from Sales.models import SalesPayment
    from Expense.models import PurchasePayment

    grand_collected   = SalesPayment.objects.filter(sale__in=sales).aggregate(t=Sum('amount'))['t'] or 0
    grand_paid        = PurchasePayment.objects.filter(purchase__in=purchases).aggregate(t=Sum('amount'))['t'] or 0
    grand_receivables = grand_total_revenue - grand_collected
    grand_payables    = grand_material_total_cost - grand_paid

    # Accrual Expense Cost card = payroll + other expenses + waste. Summed in Python so
    # it's exact (template |add truncates Decimals to int) and reconciles with Net Profit.
    grand_expense_cost = grand_total_salary_cost + grand_total_expense_cost + grand_total_waste_cost

    
    sorted_list=sorted(summary_list, key=lambda x: x['date'], reverse=True)
    
    # ── Freeze past days: lazy day-rollover accrual close (BIR "pen, not pencil") ──
    # Any day strictly before today is complete (no record can backdate) → safe to
    # snapshot. get_or_create = first close wins; today stays live & editable.
    from activity.utils import close_day
    for row in sorted_list:
        if row['date'] < today:
            snap, _ = close_day(business, row['date'], row)
            # Serve the FROZEN figures, never the live recompute (pen, not pencil) —
            # a later void/edit must not rewrite a closed day.
            row['total_revenue']       = snap.total_revenue
            row['total_material_cost'] = snap.total_material_cost
            row['total_salary_cost']   = snap.total_salary_cost
            row['total_waste_cost']    = snap.total_waste_cost
            row['total_expense_cost']  = snap.total_expense_cost
            row['net_profit']          = snap.net_profit
            row['total_cost'] = (snap.total_material_cost + snap.total_salary_cost
                                 + snap.total_waste_cost + snap.total_expense_cost)
            row['is_closed'] = True
            row['closed_at'] = snap.closed_at
        else:
            row['is_closed'] = False
            row['closed_at'] = None

    pagination = Paginator(sorted_list, 6)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    
    # so the user's filters above don't skew the "best month" result.
    rev_by_month     = {s['date__month']:          s['total'] for s in all_sales.filter(date__year=today.year).values('date__month').annotate(total=Sum('total_revenue'))}
    cost_by_month    = {p['purchase_date__month']: p['total'] for p in all_purchases.filter(purchase_date__year=today.year).values('purchase_date__month').annotate(total=Sum('total_cost'))}
    waste_by_month   = {w['date__month']:          w['total'] for w in all_wastes.filter(date__year=today.year).values('date__month').annotate(total=Sum('total_cost'))}
    expense_by_month = {e['date__month']:          e['total'] for e in all_expenses.filter(date__year=today.year).values('date__month').annotate(total=Sum('total_amount'))}
    salary_by_month  = {s['date__month']:          s['total'] for s in all_shifts.filter(date__year=today.year).values('date__month').annotate(total=Sum('amount'))}

    all_months = set(rev_by_month) | set(cost_by_month) | set(waste_by_month) | set(expense_by_month) | set(salary_by_month)

    best_month_name = 'N/A'
    best_month_profit = 0   # months with negative profit won't beat 0 — kept N/A
    for m in all_months:
        profit = (
            (rev_by_month.get(m)     or 0)
            - (cost_by_month.get(m)    or 0)
            - (waste_by_month.get(m)   or 0)
            - (expense_by_month.get(m) or 0)
            - (salary_by_month.get(m)  or 0)
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
    sales_pmts = SalesPayment.objects.filter(business=business, sale__in=all_sales)
    purch_pmts = PurchasePayment.objects.filter(business=business, purchase__in=all_purchases)

    # mirror the same filters onto payments (by their payment date)
    if form.is_valid():
        _sd = form.cleaned_data.get('start_date'); _ed = form.cleaned_data.get('end_date')
        _sm = form.cleaned_data.get('select_month')
        if _sd and _ed:
            sales_pmts = sales_pmts.filter(date__range=(_sd, _ed)); purch_pmts = purch_pmts.filter(date__range=(_sd, _ed))
        if _sm:
            _pm = datetime.strptime(_sm, '%Y-%m')
            sales_pmts = sales_pmts.filter(date__month=_pm.month, date__year=_pm.year)
            purch_pmts = purch_pmts.filter(date__month=_pm.month, date__year=_pm.year)
    if period == 'today':
        sales_pmts = sales_pmts.filter(date=today); purch_pmts = purch_pmts.filter(date=today)
    elif period == 'month':
        sales_pmts = sales_pmts.filter(date__month=today.month, date__year=today.year)
        purch_pmts = purch_pmts.filter(date__month=today.month, date__year=today.year)
    elif period in ('week', 'last_week'):
        _wk = iso_week if period == 'week' else iso_week - 1
        sales_pmts = sales_pmts.filter(date__week=_wk, date__year=iso_year)
        purch_pmts = purch_pmts.filter(date__week=_wk, date__year=iso_year)

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
    cash_collected = sum((m['amount'] or 0) for m in collected_by_method)
    cash_paid      = sum((m['amount'] or 0) for m in paid_by_method)

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
    salary_by_date = {s['date']: (s['total_salary_cost'] or 0) for s in shifts_by_date}

    cash_summary_list = []
    grand_spent = 0

    for d in (set(collected_by_date) | set(paid_by_date) | set(expense_by_date) | set(salary_by_date)):
        collected = collected_by_date.get(d, 0) or 0
        paid      = paid_by_date.get(d, 0) or 0
        expense   = expense_by_date.get(d, 0) or 0
        salary    = salary_by_date.get(d, 0) or 0
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
        'grand_total_revenue': grand_total_revenue,
        'grand_total_waste_cost': grand_total_waste_cost,
        'grand_total_salary_cost': grand_total_salary_cost,
        'grand_total_expense_cost': grand_total_expense_cost,
        'grand_expense_cost': grand_expense_cost,
        'grand_net_profit': grand_net_profit,
        'current_year': current_year,
        
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
    net_profit = 0

    # Show voided sales/purchases in the day breakdown (lined-out, like Sales Records)
    # rather than hiding them — the owner sees the void happened instead of a row silently
    # vanishing. The money TOTALS below still EXCLUDE voided (a void = cancelled / cash
    # returned; this is a management view, not a BIR X/Z ledger): display lists carry all,
    # the sums use .filter(is_void=False).
    sales = Sale.objects.filter(business=business, date=date).prefetch_related('sale_items', 'payments').order_by('-date', '-id')
    sale_items  = SaleItem.objects.filter(sale__in=sales).select_related('product').order_by('product__is_service', 'id')
    sale_employees = SaleEmployee.objects.filter(sale__in=sales)
    total_revenue = sales.filter(is_void=False).aggregate(revenue=Sum('total_revenue'))['revenue'] or 0

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

    net_profit = total_revenue - total_material_cost - total_salary_cost - total_waste_cost - total_expense_cost

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
        'net_profit': net_profit,
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
