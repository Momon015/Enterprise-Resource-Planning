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
import time
import random

from django.views.decorators.http import require_POST
from django.urls import reverse

from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth import update_session_auth_hash

from Sales.models import Sale, SaleItem, SalesReturn, SalesPayment
from Sales.forms import SaleForm, SaleFilterForm

from Product.models import Product
from Product.forms import ProductForm

from Expense.models import Purchase, PurchaseItem, Waste, WasteItem, Expense, MiscExpense, PurchaseReturn, PurchasePayment
from Employee.models import Employee, Shift, ShiftEmployee
from Employee.forms import EmployeeForm

from core.models import StatusModel

from DailySummary.forms import SummaryFilterForm

from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from urllib.parse import urlencode
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from datetime import date, datetime
from django.db.models import Sum, Avg, Max, Count, Q, F

from operator import itemgetter

from core.utils.owner import  get_owner, permission_required, get_queryset_for_user, get_business_for_user

import json
import calendar
# logging
import logging

from subscription.decorators import feature_required

from activity.utils import summarize_items

# Create your views here.

from django.core.cache import cache
from django.utils import timezone

CACHE_TTL = 60 * 15 # 15 mins - /* was 5 mins */
COMPUTE_LOCK_TTL = 30   # max seconds the compute should take
WAIT_TICK = 0.2         # poll interval
WAIT_MAX_TICKS = 5      # 5 × 0.2s = 1s max wait before giving up

def _pct_delta(today_val, yesterday_val):
    """Return ('up'|'down'|'flat', pct_string) or (None, None) if no comparison."""
    today_val = float(today_val or 0)
    yest_val  = float(yesterday_val or 0)
    if yest_val == 0:
        return (None, None)  # template hides the delta row
    pct = ((today_val - yest_val) / yest_val) * 100
    if abs(pct) < 0.05:
        return ('flat', '0.0%')
    direction = 'up' if pct > 0 else 'down'
    return (direction, f"{abs(pct):.1f}%")


def _compute_dashboard_metrics(business, today):
    """All the expensive aggregates. Cached separately so we don't recompute per request."""
    # Today's totals
    sales_today      = Sale.objects.active().filter(business=business, date=today)
    purchases_today  = Purchase.objects.active().filter(business=business, purchase_date=today)
    wastes_today     = Waste.objects.filter(business=business, date=today)
    expenses_today   = Expense.objects.filter(business=business, date=today)
    shifts_today     = Shift.objects.filter(business=business, date=today)

    total_revenue       = sales_today.aggregate(t=Sum('total_revenue'))['t'] or Decimal(0)
    total_expense_cost  = expenses_today.aggregate(t=Sum('total_amount'))['t'] or Decimal(0)
    total_salary_cost   = shifts_today.aggregate(t=Sum('shift_employees__daily_rate'))['t'] or Decimal(0)
    total_material_cost = purchases_today.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)
    total_waste_cost    = wastes_today.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)
    net_profit = total_revenue - total_material_cost - total_salary_cost - total_waste_cost - total_expense_cost

    # Yesterday's totals (for KPI deltas)
    yesterday = today - timedelta(days=1)
    y_sales     = Sale.objects.active().filter(business=business, date=yesterday)
    y_purchases = Purchase.objects.active().filter(business=business, purchase_date=yesterday)
    y_wastes    = Waste.objects.filter(business=business, date=yesterday)
    y_expenses  = Expense.objects.filter(business=business, date=yesterday)
    y_shifts    = Shift.objects.filter(business=business, date=yesterday)

    y_revenue  = y_sales.aggregate(t=Sum('total_revenue'))['t'] or Decimal(0)
    y_material = y_purchases.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)
    y_waste    = y_wastes.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)
    y_expense  = y_expenses.aggregate(t=Sum('total_amount'))['t'] or Decimal(0)
    y_salary   = y_shifts.aggregate(t=Sum('shift_employees__daily_rate'))['t'] or Decimal(0)
    y_opex     = y_salary + y_expense
    y_net      = y_revenue - y_material - y_waste - y_expense - y_salary

    # Combined operating expenses (labor/salary + overhead)
    total_opex = total_salary_cost + total_expense_cost

    # Deltas vs yesterday
    rev_dir,  rev_pct  = _pct_delta(total_revenue,       y_revenue)
    mat_dir,  mat_pct  = _pct_delta(total_material_cost, y_material)
    wst_dir,  wst_pct  = _pct_delta(total_waste_cost,    y_waste)
    opx_dir,  opx_pct  = _pct_delta(total_opex,          y_opex)
    net_dir,  net_pct  = _pct_delta(net_profit,          y_net)

    # Weekly comparison
    this_week_start = today - timedelta(days=today.weekday())
    last_week_end   = this_week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)

    def _bucket(qs, field, date_filter):
        return float(qs.filter(business=business, **date_filter).aggregate(t=Sum(field))['t'] or 0)

    tw_revenue = _bucket(Sale.objects.active(), 'total_revenue', {'date__gte': this_week_start})
    tw_cost    = _bucket(Purchase.objects.active(), 'total_cost', {'purchase_date__gte': this_week_start})
    tw_waste   = _bucket(Waste.objects, 'total_cost', {'date__gte': this_week_start})
    tw_expense = _bucket(Expense.objects, 'total_amount', {'date__gte': this_week_start})
    tw_salary  = _bucket(Shift.objects, 'shift_employees__daily_rate', {'date__gte': this_week_start})
    tw_net     = tw_revenue - tw_cost - tw_waste - tw_expense - tw_salary

    lw_revenue = _bucket(Sale.objects.active(), 'total_revenue', {'date__range': (last_week_start, last_week_end)})
    lw_cost    = _bucket(Purchase.objects.active(), 'total_cost', {'purchase_date__range': (last_week_start, last_week_end)})
    lw_waste   = _bucket(Waste.objects, 'total_cost', {'date__range': (last_week_start, last_week_end)})
    lw_expense = _bucket(Expense.objects, 'total_amount', {'date__range': (last_week_start, last_week_end)})
    lw_salary  = _bucket(Shift.objects, 'shift_employees__daily_rate', {'date__range': (last_week_start, last_week_end)})
    lw_net     = lw_revenue - lw_cost - lw_waste - lw_expense - lw_salary

    # Monthly comparison
    this_month_start = today.replace(day=1)
    last_month_end   = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    tm_revenue = _bucket(Sale.objects.active(), 'total_revenue', {'date__gte': this_month_start})
    tm_cost    = _bucket(Purchase.objects.active(), 'total_cost', {'purchase_date__gte': this_month_start})
    tm_waste   = _bucket(Waste.objects, 'total_cost', {'date__gte': this_month_start})
    tm_expense = _bucket(Expense.objects, 'total_amount', {'date__gte': this_month_start})
    tm_salary  = _bucket(Shift.objects, 'shift_employees__daily_rate', {'date__gte': this_month_start})
    tm_net     = tm_revenue - tm_cost - tm_waste - tm_expense - tm_salary

    lm_revenue = _bucket(Sale.objects.active(), 'total_revenue', {'date__range': (last_month_start, last_month_end)})
    lm_cost    = _bucket(Purchase.objects.active(), 'total_cost', {'purchase_date__range': (last_month_start, last_month_end)})
    lm_waste   = _bucket(Waste.objects, 'total_cost', {'date__range': (last_month_start, last_month_end)})
    lm_expense = _bucket(Expense.objects, 'total_amount', {'date__range': (last_month_start, last_month_end)})
    lm_salary  = _bucket(Shift.objects, 'shift_employees__daily_rate', {'date__range': (last_month_start, last_month_end)})
    lm_net     = lm_revenue - lm_cost - lm_waste - lm_expense - lm_salary

    # 30-day trend
    thirty_days_ago = today - timedelta(days=29)
    daily_sales = (
        Sale.objects.active().filter(business=business, date__gte=thirty_days_ago)
        .values('date').annotate(rev=Sum('total_revenue')).order_by('date')
    )
    trend_labels = [(thirty_days_ago + timedelta(days=i)).strftime('%b %d') for i in range(30)]
    revenue_map  = {s['date'].strftime('%b %d'): float(s['rev']) for s in daily_sales}
    trend_data   = [revenue_map.get(label, 0) for label in trend_labels]

    return {
        'total_revenue': total_revenue,
        'total_material_cost': total_material_cost,
        'total_salary_cost': total_salary_cost,
        'total_waste_cost': total_waste_cost,
        'total_expense_cost': total_expense_cost,
        'net_profit': net_profit,
        
        # yesterday
        'total_opex': total_opex,

        'rev_dir': rev_dir, 'rev_pct': rev_pct,
        'mat_dir': mat_dir, 'mat_pct': mat_pct,
        'wst_dir': wst_dir, 'wst_pct': wst_pct,
        'opx_dir': opx_dir, 'opx_pct': opx_pct,
        'net_dir': net_dir, 'net_pct': net_pct,

        'this_week_label': f"Wk {this_week_start.strftime('%b %d')}",
        'last_week_label': f"Wk {last_week_start.strftime('%b %d')}",
        'tw_revenue': tw_revenue, 'tw_cost': tw_cost,
        'tw_waste': tw_waste, 'tw_expense': tw_expense, 'tw_salary': tw_salary, 'tw_net': tw_net,
        'lw_revenue': lw_revenue, 'lw_cost': lw_cost,
        'lw_waste': lw_waste, 'lw_expense': lw_expense, 'lw_salary': lw_salary, 'lw_net': lw_net,

        'this_month_label': today.strftime('%B'),
        'last_month_label': last_month_end.strftime('%B'),
        'tm_revenue': tm_revenue, 'tm_cost': tm_cost,
        'tm_waste': tm_waste, 'tm_expense': tm_expense, 'tm_salary': tm_salary, 'tm_net': tm_net,
        'lm_revenue': lm_revenue, 'lm_cost': lm_cost,
        'lm_waste': lm_waste, 'lm_expense': lm_expense, 'lm_salary': lm_salary, 'lm_net': lm_net,

        'trend_labels': json.dumps(trend_labels),
        'trend_data': json.dumps(trend_data),
        'computed_at': timezone.now(), 
    }


def _get_cached_dashboard_metrics(business, today):
    cache_key = f'dashboard:metrics:{business.id}:{today.isoformat()}'
    metrics = cache.get(cache_key)
    if metrics is not None:
        return metrics

    # Cache miss — try to be the one who computes.
    lock_key = f'{cache_key}:lock'
    got_lock = cache.add(lock_key, True, timeout=COMPUTE_LOCK_TTL)

    if got_lock:
        try:
            metrics = _compute_dashboard_metrics(business, today)
            cache.set(cache_key, metrics, timeout=CACHE_TTL)
            return metrics
        finally:
            cache.delete(lock_key)

    # Someone else is already computing — wait briefly for them to finish.
    for _ in range(WAIT_MAX_TICKS):
        time.sleep(WAIT_TICK)
        metrics = cache.get(cache_key)
        if metrics is not None:
            return metrics

    # Lock holder is stuck or compute is genuinely slow — fall back so the
    # user doesn't see a blank page. Worst case: we do the work twice.
    return _compute_dashboard_metrics(business, today)


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only')
@feature_required('has_dashboard')
def dashboard(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    today = timezone.localdate()

    # Heavy aggregates — cached (5 min)
    metrics = _get_cached_dashboard_metrics(business, today)

    # Live querysets — small lookups, used by template for "today's items" lists.
    # NOT cached because they're cheap and we want freshness for "what just happened today".
    sales           = Sale.objects.active().filter(business=business, date=today)
    sale_items      = SaleItem.objects.filter(sale__in=sales)
    shifts          = Shift.objects.filter(business=business, date=today)
    shift_employees = ShiftEmployee.objects.filter(shift__in=shifts)
    purchases       = Purchase.objects.active().filter(business=business, purchase_date=today)
    purchase_items  = PurchaseItem.objects.filter(purchase__in=purchases)
    wastes          = Waste.objects.filter(business=business, date=today)
    waste_items     = WasteItem.objects.filter(waste__in=wastes)
    expenses        = Expense.objects.filter(business=business, date=today)
    
    # ── On-Shift Now (active timecards: clocked in, not clocked out) ──
    active_shifts = ShiftEmployee.objects.filter(
        shift__business=business,
        shift__date=today,
        clock_in__isnull=False,
        clock_out__isnull=True,
    ).select_related('employee', 'shift').order_by('clock_in')

    # ── Recent Activities feed (last 10 events across modules) ──
    from itertools import chain
    from operator import attrgetter

    def _ts(obj, *candidates):
        """Pick the first non-null timestamp from the given attr names."""
        for attr in candidates:
            v = getattr(obj, attr, None)
            if v:
                return v
        return None

    raw_sales              =  list(Sale.objects.active().filter(business=business).order_by('-id')[:10])
    raw_purchases          =  list(Purchase.objects.active().filter(business=business).order_by('-id')[:10])
    raw_wastes             =  list(Waste.objects.filter(business=business).order_by('-id')[:10])
    raw_expenses           =  list(Expense.objects.filter(business=business).prefetch_related('expense_items').order_by('-id')[:10])
    raw_sales_returns      =  list(SalesReturn.objects.filter(business=business).select_related('original_sale').order_by('-id')[:10])
    raw_purchase_returns   =  list(PurchaseReturn.objects.filter(business=business).select_related('original_purchase').order_by('-id')[:10])
    raw_sales_payments     =  list(SalesPayment.objects.filter(business=business).select_related('sale').order_by('-id')[:10])
    raw_purchase_payments  =  list(PurchasePayment.objects.filter(business=business).select_related('purchase').order_by('-id')[:10])
    
    def _payment_text(obj):
        """Build 'Free' / 'via Cash' / 'via GCash (partial ₱X)' / 'Utang' from a Sale or Purchase."""
        total = getattr(obj, 'total_revenue', None)
        if total is None:
            total = getattr(obj, 'total_cost', 0)
        if not total:
            return "Free"
        first_payment = obj.payments.order_by('id').first() if hasattr(obj, 'payments') else None
        if obj.is_fully_paid and first_payment:
            return f"via {first_payment.get_method_display()}"
        if first_payment:
            return f"via {first_payment.get_method_display()} (partial ₱{first_payment.amount:.2f})"
        return "Utang"


    activities = []
    for s in raw_sales:
        activities.append({
            'kind': 'sale', 'icon': 'bi-cash-coin', 'tint': 'success',
            'title': f"Sale {s.reference or '#'+str(s.id)}",
            'description': f"{_payment_text(s)} · {summarize_items(s.sale_items.all(), prefix='-')}",
            'amount': s.total_revenue,
            'ts': _ts(s, 'created_at', 'date'),
            'url': reverse('sale-detail', kwargs={'business_slug': business.slug, 'sale_id': s.id}),
        })
    for p in raw_purchases:
        activities.append({
            'kind': 'purchase', 'icon': 'bi-box-seam', 'tint': 'purple',
            'title': f"Purchase {p.reference or '#'+str(p.id)}",
            'description': f"{_payment_text(p)} · {summarize_items(p.materials.all(), prefix='+')}",
            'amount': p.total_cost,
            'ts': _ts(p, 'created_at', 'purchase_date'),
            'url': reverse('purchase-detail', kwargs={'business_slug': business.slug, 'purchase_id': p.id}),
        })
    for w in raw_wastes:
        activities.append({
            'kind': 'waste', 'icon': 'bi-trash3', 'tint': 'danger',
            'title': "Waste recorded",  # swap to f"Waste {w.reference}" after the WST migration
            'description': f"{w.get_reason_display()} · {summarize_items(w.waste_items.all(), prefix='-')}",
            'amount': w.total_cost,
            'ts': _ts(w, 'created_at', 'date'),
            'url': reverse('material-waste-detail', kwargs={'business_slug': business.slug, 'waste_id': w.id}),
        })
    for e in raw_expenses:
        top_item = e.expense_items.first()
        label = top_item.name if top_item else 'Other'
        more = e.expense_items.count() - 1
        activities.append({
            'kind': 'expense', 'icon': 'bi-receipt', 'tint': 'warning',
            'title': f"Other Expense - {label}",
            'description': f"+{more} more" if more > 0 else "",
            'amount': e.total_amount,
            'ts': _ts(e, 'created_at', 'date'),
            'url': reverse('expense-detail', kwargs={'business_slug': business.slug, 'date': e.date.isoformat() if e.date else ''}),
        })
    for r in raw_sales_returns:
        activities.append({
            'kind': 'sale-return', 'icon': 'bi-arrow-return-left', 'tint': 'danger',
            'title': f"Sale refunded · {r.reference}",
            'description': summarize_items(
                r.items.all(),
                sign_for=lambda it: '+' if it.resellable else '-',
            ),
            'amount': r.refund_total,
            'ts': _ts(r, 'created_at', 'date'),
            'url': reverse('sales-return-detail', kwargs={'business_slug': business.slug, 'return_id': r.id}),
        })
    for r in raw_purchase_returns:
        activities.append({
            'kind': 'purchase-return', 'icon': 'bi-arrow-return-left', 'tint': 'danger',
            'title': f"Purchase refunded · {r.reference}",
            'description': summarize_items(r.items.all(), prefix='-'),
            'amount': r.refund_total,
            'ts': _ts(r, 'created_at', 'date'),
            'url': reverse('purchase-return-detail', kwargs={'business_slug': business.slug, 'return_id': r.id}),
        })
    for pay in raw_sales_payments:
        method = pay.get_method_display() if hasattr(pay, 'get_method_display') else ''
        outstanding = pay.sale.outstanding if pay.sale else 0
        desc = f"via {method}"
        if outstanding > 0:
            desc += f" (partial) · ₱{outstanding:.2f} outstanding"
        activities.append({
            'kind': 'sale-payment', 'icon': 'bi-cash-stack', 'tint': 'info',
            'title': f"Payment received · {pay.sale.reference}",
            'description': desc,
            'amount': pay.amount,
            'ts': _ts(pay, 'created_at', 'date'),
            'url': reverse('sale-detail', kwargs={'business_slug': business.slug, 'sale_id': pay.sale_id}),
        })
    for pay in raw_purchase_payments:
        method = pay.get_method_display() if hasattr(pay, 'get_method_display') else ''
        outstanding = pay.purchase.outstanding if pay.purchase else 0
        desc = f"via {method}"
        if outstanding > 0:
            desc += f" (partial) · ₱{outstanding:.2f} outstanding"
        activities.append({
            'kind': 'purchase-payment', 'icon': 'bi-cash-stack', 'tint': 'info',
            'title': f"Payment sent · {pay.purchase.reference}",
            'description': desc,
            'amount': pay.amount,
            'ts': _ts(pay, 'created_at', 'date'),
            'url': reverse('purchase-detail', kwargs={'business_slug': business.slug, 'purchase_id': pay.purchase_id}),
        })
        
    # Sort newest first, take top 10
    activities = [a for a in activities if a['ts'] is not None]
    activities.sort(key=lambda a: a['ts'], reverse=True)
    activities = activities[:7]
    
    # Today's cash lens — live, payments of today's records (same attribution as list/reports)
    collected   = SalesPayment.objects.filter(sale__in=sales).aggregate(t=Sum('amount'))['t'] or Decimal(0)
    paid        = PurchasePayment.objects.filter(purchase__in=purchases).aggregate(t=Sum('amount'))['t'] or Decimal(0)
    receivables = (sales.aggregate(t=Sum('total_revenue'))['t'] or Decimal(0)) - collected
    payables    = (purchases.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)) - paid


    context = {
        **metrics,
        'sales': sales,
        'sale_items': sale_items,
        'shift_employees': shift_employees,
        'purchases': purchases,
        'purchase_items': purchase_items,
        'wastes': wastes,
        'waste_items': waste_items,
        'expenses': expenses,
        'today': today,
        'section': 'dashboard',
        
        'collected': collected,
        'paid': paid,
        'receivables': receivables,
        'payables': payables,

        
        'active_shifts': active_shifts,
        'activities': activities,
    }
    return render(request, 'Dashboard/dashboard.html', context)




