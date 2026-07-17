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

from Product.models import Product, CRITICAL_BAND_Q, LOW_BAND_Q, with_stock_bands
from Product.forms import ProductForm

from Expense.models import Purchase, PurchaseItem, Waste, WasteItem, Expense, MiscExpense, PurchaseReturn, PurchasePayment
from Employee.models import Employee, Shift, ShiftEmployee, DrawerSession, CashPayout
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

from activity.utils import summarize_items, attention_items

from Dashboard.models import DashboardSeen
from user.models import BusinessProfile

# Create your views here.

from django.core.cache import cache
from django.utils import timezone

from core.constants import KPI_CACHE_TTL as CACHE_TTL

COMPUTE_LOCK_TTL = 30   # max seconds the compute should take
WAIT_TICK = 0.2         # poll interval
WAIT_MAX_TICKS = 5      # 5 × 0.2s = 1s max wait before giving up

# Moved to core.utils.metrics 2026-07-12 so Analytics (period vs previous period)
# and the Dashboard (today vs yesterday) share ONE delta function. Kept under the old
# private name — every call site below still reads _pct_delta.
from core.utils.metrics import pct_delta as _pct_delta

# Returns (2026-07-12). The profit formula lives in ONE place now — see the module docstring
# for why both return types had to be netted off at the same time.
from core.utils.returns import (
    purchase_returns_total,
    sales_returns_total,
)

# ★ PROFIT IS NOW COGS-BASED (2026-07-13) — see core/utils/profit.py. Net profit subtracts
# the cost of the goods actually SOLD, not the stock BOUGHT in the window. A big delivery no
# longer fakes a loss on the day it lands. `total_material_cost` is still computed and still
# shown (it's real money out, and the Cash Flow lens needs it) — it just isn't what profit
# subtracts any more.
from core.utils.profit import cogs_in, gross_margin, net_profit as net_profit_formula


def _compute_dashboard_metrics(business, today):
    """All the expensive aggregates. Cached separately so we don't recompute per request."""
    # Today's totals
    sales_today      = Sale.objects.active().filter(business=business, date=today)
    purchases_today  = Purchase.objects.active().filter(business=business, purchase_date=today)
    wastes_today     = Waste.objects.filter(business=business, date=today)
    expenses_today   = Expense.objects.filter(business=business, date=today)
    shifts_today     = Shift.objects.filter(business=business, date=today)

    # ★ RETURNS (2026-07-12). Both figures below are NET of refunds:
    #   Revenue      = sales      - customer refunds  (SalesReturn)
    #   Material cost= purchases  - supplier refunds  (PurchaseReturn)
    # They are dated by the RETURN's own date, so a refund today reduces TODAY — it never
    # reaches back and rewrites a sealed day. See core/utils/returns.py for why both sides
    # had to change at once (fixing only the cost side overstates profit).
    gross_revenue       = sales_today.aggregate(t=Sum('total_revenue'))['t'] or Decimal(0)
    gross_material      = purchases_today.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)
    sales_refunds       = sales_returns_total(business, today, today)
    purchase_refunds    = purchase_returns_total(business, today, today)

    total_revenue       = gross_revenue  - sales_refunds
    total_material_cost = gross_material - purchase_refunds
    total_expense_cost  = expenses_today.aggregate(t=Sum('total_amount'))['t'] or Decimal(0)
    total_salary_cost   = shifts_today.aggregate(t=Sum('amount'))['t'] or Decimal(0)
    total_waste_cost    = wastes_today.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)

    # Cost of the goods that actually left the shelf today, already relieved of anything a
    # customer brought back. This — not total_material_cost — is what profit subtracts.
    total_cogs = cogs_in(business, today, today)

    net_profit = net_profit_formula(
        revenue=gross_revenue, cogs=total_cogs,
        salary=total_salary_cost, waste=total_waste_cost, bills=total_expense_cost,
        sales_returns=sales_refunds,
    )
    margin = gross_margin(gross_revenue, total_cogs, sales_refunds)

    # Yesterday's totals (for KPI deltas)
    yesterday = today - timedelta(days=1)
    y_sales     = Sale.objects.active().filter(business=business, date=yesterday)
    y_purchases = Purchase.objects.active().filter(business=business, purchase_date=yesterday)
    y_wastes    = Waste.objects.filter(business=business, date=yesterday)
    y_expenses  = Expense.objects.filter(business=business, date=yesterday)
    y_shifts    = Shift.objects.filter(business=business, date=yesterday)

    y_gross_revenue  = y_sales.aggregate(t=Sum('total_revenue'))['t'] or Decimal(0)
    y_gross_material = y_purchases.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)
    y_sales_refunds    = sales_returns_total(business, yesterday, yesterday)
    y_purchase_refunds = purchase_returns_total(business, yesterday, yesterday)

    y_revenue  = y_gross_revenue  - y_sales_refunds
    y_material = y_gross_material - y_purchase_refunds
    y_waste    = y_wastes.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)
    y_expense  = y_expenses.aggregate(t=Sum('total_amount'))['t'] or Decimal(0)
    y_salary   = y_shifts.aggregate(t=Sum('amount'))['t'] or Decimal(0)
    y_opex     = y_salary + y_expense
    y_cogs     = cogs_in(business, yesterday, yesterday)
    y_net      = net_profit_formula(
        revenue=y_gross_revenue, cogs=y_cogs,
        salary=y_salary, waste=y_waste, bills=y_expense,
        sales_returns=y_sales_refunds,
    )

    # Combined operating expenses (labor/salary + overhead)
    total_opex = total_salary_cost + total_expense_cost

    # Deltas vs yesterday
    rev_dir,  rev_pct  = _pct_delta(total_revenue,       y_revenue)
    mat_dir,  mat_pct  = _pct_delta(total_material_cost, y_material)
    wst_dir,  wst_pct  = _pct_delta(total_waste_cost,    y_waste)
    opx_dir,  opx_pct  = _pct_delta(total_opex,          y_opex)
    net_dir,  net_pct  = _pct_delta(net_profit,          y_net)
    # Expense Cost card (accrual lens) = opex + waste, one number
    expcost_dir, expcost_pct = _pct_delta(total_opex + total_waste_cost, y_opex + y_waste)

    # Weekly comparison
    this_week_start = today - timedelta(days=today.weekday())
    last_week_end   = this_week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)

    def _bucket(qs, field, date_filter):
        return float(qs.filter(business=business, **date_filter).aggregate(t=Sum(field))['t'] or 0)

    def _window(start, end):
        """One period's accrual figures, returns already netted off.

        Every comparison bucket below (this week / last week / this month / last month)
        goes through here, so a change to the profit formula cannot be applied to three of
        them and forgotten on the fourth.

        ★ Everything here is a FLOAT (that's what _bucket returns), so cogs_in's Decimal is
        cast on the way in. Mixing the two raises TypeError, and it would only blow up on a
        window that actually had sales — i.e. never in an empty test, always in production.
        """
        gross_rev  = _bucket(Sale.objects.active(),     'total_revenue', {'date__range': (start, end)})
        gross_cost = _bucket(Purchase.objects.active(), 'total_cost',    {'purchase_date__range': (start, end)})
        waste      = _bucket(Waste.objects,   'total_cost',   {'date__range': (start, end)})
        expense    = _bucket(Expense.objects, 'total_amount', {'date__range': (start, end)})
        salary     = _bucket(Shift.objects,   'amount',       {'date__range': (start, end)})

        s_ret = float(sales_returns_total(business, start, end))
        p_ret = float(purchase_returns_total(business, start, end))
        cogs  = float(cogs_in(business, start, end))

        return {
            'revenue': gross_rev  - s_ret,
            # 'cost' stays STOCK PURCHASED (money out to suppliers) — the weekly/monthly
            # comparison cards report spending, not cost of sales. Profit below uses cogs.
            'cost':    gross_cost - p_ret,
            'cogs':    cogs,
            'waste':   waste,
            'expense': expense,
            'salary':  salary,
            'net': net_profit_formula(
                revenue=gross_rev, cogs=cogs,
                salary=salary, waste=waste, bills=expense,
                sales_returns=s_ret,
            ),
        }

    tw = _window(this_week_start, today)
    tw_revenue, tw_cost, tw_waste, tw_expense, tw_salary, tw_net = (
        tw['revenue'], tw['cost'], tw['waste'], tw['expense'], tw['salary'], tw['net'])

    lw = _window(last_week_start, last_week_end)
    lw_revenue, lw_cost, lw_waste, lw_expense, lw_salary, lw_net = (
        lw['revenue'], lw['cost'], lw['waste'], lw['expense'], lw['salary'], lw['net'])

    # Monthly comparison
    this_month_start = today.replace(day=1)
    last_month_end   = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    tm = _window(this_month_start, today)
    tm_revenue, tm_cost, tm_waste, tm_expense, tm_salary, tm_net = (
        tm['revenue'], tm['cost'], tm['waste'], tm['expense'], tm['salary'], tm['net'])

    lm = _window(last_month_start, last_month_end)
    lm_revenue, lm_cost, lm_waste, lm_expense, lm_salary, lm_net = (
        lm['revenue'], lm['cost'], lm['waste'], lm['expense'], lm['salary'], lm['net'])

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
        # total_revenue / total_material_cost below are NET of refunds. The cards show the
        # working underneath ("₱755.00 − ₱47.00 returned") rather than a bare "− ₱47",
        # because a bare minus under an ALREADY-NET figure invites the reader to subtract
        # it a second time. Showing the gross makes the net obviously derived — and a
        # revenue figure that silently dropped from ₱755 to ₱708 otherwise reads as a lost
        # sale rather than a refund.
        'gross_revenue': gross_revenue,
        'gross_material': gross_material,
        'sales_refunds': sales_refunds,
        'purchase_refunds': purchase_refunds,

        'total_revenue': total_revenue,
        'total_material_cost': total_material_cost,
        'total_salary_cost': total_salary_cost,
        'total_waste_cost': total_waste_cost,
        'total_expense_cost': total_expense_cost,

        # ★ COGS is what net_profit subtracts; total_material_cost is what you PAID
        # suppliers. They are different questions and both are shown — the first on the
        # profit card's breakdown, the second on the Cash Flow lens and Expense Analytics.
        'total_cogs': total_cogs,
        'gross_margin': margin,
        'net_profit': net_profit,
        
        # yesterday
        'total_opex': total_opex,
        'total_expcost': total_opex + total_waste_cost,
        'y_opex': y_opex,

        'rev_dir': rev_dir, 'rev_pct': rev_pct,
        'mat_dir': mat_dir, 'mat_pct': mat_pct,
        'wst_dir': wst_dir, 'wst_pct': wst_pct,
        'opx_dir': opx_dir, 'opx_pct': opx_pct,
        'net_dir': net_dir, 'net_pct': net_pct,
        'expcost_dir': expcost_dir, 'expcost_pct': expcost_pct,

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
        """Build 'Free' / 'via Cash' / 'via GCash (partial ₱X)' / 'Debt' from a Sale or Purchase."""
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
        return "Debt"


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
            'title': f"Business Expense - {label}",
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
    
    # ── Today's cash lens ────────────────────────────────────────────────
    # Cash IN/OUT = payments made TODAY (collecting an old debt today still counts).
    # Store-credit redemptions settle debt but move NO cash — excluded from the cash lens.
    # Voided sales/purchases are cancelled (cash handed back) — excluded too, so this
    # management cash view matches the Daily Summary. This is NOT a BIR X/Z ledger.
    collected = SalesPayment.objects.filter(business=business, date=today).exclude(method='credit').exclude(sale__is_void=True).aggregate(t=Sum('amount'))['t'] or Decimal(0)
    paid      = PurchasePayment.objects.filter(business=business, date=today).exclude(purchase__is_void=True).aggregate(t=Sum('amount'))['t'] or Decimal(0)

    # New utang created TODAY = unpaid portion of today's OWN sales/purchases (not all-time).
    receivables = sum((s.outstanding for s in sales), Decimal('0'))
    payables    = sum((p.outstanding for p in purchases), Decimal('0'))
    
    # ── Accounting mode (display-only): ?basis= is a transient glimpse, else the saved default ──
    basis = request.GET.get('basis')
    if basis not in ('accrual', 'cash'):
        basis = business.dashboard_basis or 'accrual'
    net_cash = collected - paid - (metrics.get('total_opex') or Decimal('0'))

    # Cash deltas vs yesterday (drive the arrows in Cash Flow mode)
    yesterday   = today - timedelta(days=1)
    y_collected = SalesPayment.objects.filter(business=business, date=yesterday).exclude(method='credit').exclude(sale__is_void=True).aggregate(t=Sum('amount'))['t'] or Decimal(0)
    y_paid      = PurchasePayment.objects.filter(business=business, date=yesterday).exclude(purchase__is_void=True).aggregate(t=Sum('amount'))['t'] or Decimal(0)
    y_net_cash  = y_collected - y_paid - (metrics.get('y_opex') or Decimal('0'))

    col_dir,   col_pct   = _pct_delta(collected, y_collected)
    paid_dir,  paid_pct  = _pct_delta(paid,      y_paid)
    ncash_dir, ncash_pct = _pct_delta(net_cash,  y_net_cash)
    
    # ── KPI status hues (green = money in, red = money out, neutral = nothing) ──
    if basis == 'cash':
        _rev = collected
        _exp = metrics.get('total_opex') or Decimal('0')
        _mat = paid
        _np  = net_cash
    else:
        _rev = metrics.get('total_revenue') or Decimal('0')
        _exp = metrics.get('total_expcost') or Decimal('0')
        _mat = metrics.get('total_material_cost') or Decimal('0')
        _np  = metrics.get('net_profit') or Decimal('0')

    hue_revenue   = 'success' if _rev > 0 else ''
    hue_expense   = 'danger'  if _exp > 0 else ''
    hue_material  = 'danger'  if _mat > 0 else ''
    hue_netprofit = 'success' if _np > 0 else ('danger' if _np < 0 else '')

    # ▼ on the Expense Cost card only when there's something inside it — the rule every
    # OTHER card on this strip already follows (Revenue gates on having payments/receivables,
    # Cash Drawer on a session existing, Critically Low on there being critical rows). Expense
    # Cost was the one card carrying its chevron unconditionally, so on a quiet day it offered
    # a dropdown that opened to four zeros.
    #
    # ★ Gated on _exp, which is ALREADY the lens-correct figure the card prints (cash = payroll
    #   + expenses; accrual also folds in waste). Deliberately NOT reusing `hue_expense` even
    #   though it's true under the same condition today — that's a COLOUR rule, and tying the
    #   dropdown's existence to it would break the day someone changes how the card is tinted.
    has_expense_breakdown = _exp > 0

    # Same fix for Stock Purchases. Its ▼ was gated on the LENS only (`basis != 'cash'`), never
    # on the value — so in Accrual mode a day with no deliveries still offered a dropdown that
    # opened to ₱0.00 four times over. The cash lens has no dropdown here at all (by design:
    # gross/returns/payables are accrual concepts), so both conditions have to hold.
    has_material_breakdown = basis != 'cash' and _mat > 0


    # Collected-by-method (cash-lens Revenue popover)
    method_names = dict(SalesPayment.PAYMENT_METHOD_CHOICES)
    collected_by_method = [
        {'label': method_names.get(r['method'], r['method']), 'amount': r['t']}
        for r in SalesPayment.objects.filter(business=business, date=today)
                 .exclude(method='credit').exclude(sale__is_void=True)
                 .values('method').annotate(t=Sum('amount')).order_by('-t')
    ]
    credit_used_today = SalesPayment.objects.filter(
        business=business, date=today, method='credit'
    ).exclude(sale__is_void=True).aggregate(t=Sum('amount'))['t'] or Decimal(0)

    # # Time-of-day greeting (Manila local time)
    # hour = timezone.localtime().hour
    # if 5 <= hour < 12:
    #     greeting = 'Good morning'
    # elif hour < 18:
    #     greeting = 'Good afternoon'
    # else:
    #     greeting = 'Good evening'
        
    greeting = 'Welcome back'

    # ── Row-2 KPI cards: counts, drawer, stock alerts ──────────────────
    txn_count = sales.count()
    y_txn     = Sale.objects.active().filter(business=business, date=yesterday).count()
    txn_diff  = txn_count - y_txn

    pur_count = purchases.count()
    y_pur     = Purchase.objects.active().filter(business=business, purchase_date=yesterday).count()
    pur_diff  = pur_count - y_pur

    cash_sales_today = SalesPayment.objects.filter(
        business=business, date=today, method='cash'
    ).exclude(sale__is_void=True).aggregate(t=Sum('amount'))['t'] or Decimal(0)
    
    cash_purchases_today = PurchasePayment.objects.filter(
        business=business, date=today, method__in=['cash', 'cod']
    ).exclude(purchase__is_void=True).aggregate(t=Sum('amount'))['t'] or Decimal(0)

    cash_refunds_today = SalesReturn.objects.filter(
        business=business, date=today, refund_method='cash'
    ).aggregate(t=Sum('refund_total'))['t'] or Decimal(0)

    # Only CASH bills leave the physical drawer — GCash/bank expenses don't touch it.
    cash_expenses_today = Expense.objects.filter(
        business=business, date=today, payment_method='cash'
    ).aggregate(t=Sum('total_amount'))['t'] or Decimal(0)

    # CashPayout.shift is a ShiftEmployee (not a Shift) — business/date live on ShiftEmployee.shift
    payouts_today = CashPayout.objects.filter(
        shift__shift__business=business, shift__shift__date=today
    ).aggregate(t=Sum('amount'))['t'] or Decimal(0)
    returns_today = PurchaseReturn.objects.filter(
        business=business, date=today
    ).aggregate(t=Sum('refund_total'))['t'] or Decimal(0)

    drawer = None
    if business.enable_cash_reconciliation:
        drawer = DrawerSession.objects.filter(business=business, date=today).order_by('-opened_at').first()
    
    drawer_balance = (
        drawer.opening_cash + cash_sales_today
        - payouts_today - cash_purchases_today - cash_refunds_today - cash_expenses_today
    ) if drawer else None


    # ── Stock bands — DISJOINT (Product/models.py): out | critical | low ──────────
    # `low_only` is now LOW ONLY: it no longer swallows the criticals. That keeps this card
    # equal to the Products page's Low Stock card and the bell's "Running Low" row.
    out_of_stock_qs   = Product.goods.filter(business=business, prepared_quantity=0)
    stock_alert_qs    = Product.goods.filter(business=business, prepared_quantity__lte=F('low_stock_threshold'))
    stock_alert_count = stock_alert_qs.count()
    out_of_stock      = out_of_stock_qs.count()

    _banded        = with_stock_bands(Product.goods.filter(business=business))
    low_only       = _banded.filter(LOW_BAND_Q).count()        # low, EXCLUDING critical
    critical_count = _banded.filter(CRITICAL_BAND_Q).count()   # 1 .. 20% of threshold

    # Popover peeks — 3 rows max, the card's big number stays the UNCAPPED count.
    # Critical/low sort by what's closest to running out; out-of-stock are all at 0, so there
    # is nothing to rank them by — alphabetical is the only honest order.
    low_stock_qs   = stock_alert_qs.filter(prepared_quantity__gt=0)   # low + critical (list)
    low_stock_top  = list(low_stock_qs.order_by('prepared_quantity')[:3])
    critical_top   = list(_banded.filter(CRITICAL_BAND_Q).order_by('prepared_quantity')[:3])
    out_of_stock_top = list(out_of_stock_qs.order_by('name')[:3])

    # ── Out of Stock — red when anything's out, emerald when the shelf is clear ──
    hue_outofstock = 'danger' if out_of_stock > 0 else 'success'

    # ── Critically Low — the dashboard's 2nd stock card (it replaced Low Stock 2026-07-12:
    #    the strip has 2 slots and critical + out are the ones that cost you sales TODAY;
    #    merely-low still shows in Needs Attention and the bell). Amber when anything is
    #    critical; emerald only when NOTHING is critical, low, or out; gray otherwise. ──
    if critical_count > 0:
        hue_critical = 'warning'
    elif out_of_stock == 0 and low_only == 0:
        hue_critical = 'success'                       # everything healthy → emerald
    else:
        hue_critical = ''                              # nothing critical, but not all clear

    # kept for the old Low Stock card (commented out in the template) — uncomment both
    if low_only > 0 or critical_count > 0:
        hue_lowstock = 'orange' if critical_count else 'warning'
    elif out_of_stock == 0:
        hue_lowstock = 'success'
    else:
        hue_lowstock = ''


    # ── Needs Attention — current state requiring action (bell = event stream) ──
    # SHARED with the bell's pinned block: attention_items() owns pending sales +
    # stock, so the two surfaces can never disagree. The two items below stay LOCAL
    # to the dashboard on purpose — `Purchase.outstanding` is a property firing 2
    # aggregate queries PER purchase, and the bell re-runs on a 30s poll on every
    # page. Move them into the shared helper only after outstanding becomes a DB
    # annotation (1 query) instead of an N+1 loop.
    attention = attention_items(business)

    due_soon_count = sum(
        1 for p_ in Purchase.objects.active().filter(
            business=business, due_date__isnull=False,
            due_date__range=(today, today + timedelta(days=3)))
        if p_.outstanding > 0
    )

    if due_soon_count:
        attention.append({'tone': 'info', 'icon': 'bi-credit-card-2-back',
            'text': f"{due_soon_count} supplier payment{'s' if due_soon_count != 1 else ''} due within 3 days",
            'url': reverse('purchase-payables', kwargs={'business_slug': business.slug})})
    if drawer and drawer.is_open:
        attention.append({'tone': 'info', 'icon': 'bi-cash-coin',
            'text': "Cash drawer is open — remember to close it at end of day",
            'url': reverse('shift-dashboard', kwargs={'business_slug': business.slug})})

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
        
        'basis': basis,
        'net_cash': net_cash,
        
        'col_dir': col_dir, 'col_pct': col_pct,
        'paid_dir': paid_dir, 'paid_pct': paid_pct,
        'ncash_dir': ncash_dir, 'ncash_pct': ncash_pct,

        'away': _away_summary(request, business),
        'greeting': greeting,
        'collected_by_method': collected_by_method,
        'credit_used_today': credit_used_today,

        'txn_count': txn_count, 'txn_diff': txn_diff,
        'pur_count': pur_count, 'pur_diff': pur_diff,
        'drawer': drawer, 'drawer_balance': drawer_balance,
        'cash_sales_today': cash_sales_today,
        'cash_expenses_today': cash_expenses_today,
        'payouts_today': payouts_today, 'returns_today': returns_today,
        'stock_alert_count': stock_alert_count,
        'out_of_stock': out_of_stock, 'low_only': low_only, 'low_stock_top': low_stock_top,
        'out_of_stock_top': out_of_stock_top,
        'critical_count': critical_count, 'critical_top': critical_top,
        'attention': attention,
        
        'hue_revenue': hue_revenue,
        'hue_expense': hue_expense,
        'has_expense_breakdown': has_expense_breakdown,
        'has_material_breakdown': has_material_breakdown,
        'hue_material': hue_material,
        'hue_netprofit': hue_netprofit,
        'hue_lowstock': hue_lowstock,
        'hue_critical': hue_critical,
        'hue_outofstock': hue_outofstock,


    }
    return render(request, 'Dashboard/dashboard.html', context)

@login_required(login_url='login')
@require_POST
@feature_required('has_dashboard')
def set_dashboard_basis(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    if request.user != business.user:
        messages.error(request, "Only the owner can change the dashboard default.")
        return redirect('dashboard', business_slug=business.slug)

    basis = request.POST.get('basis')
    if basis not in ('accrual', 'cash'):
        basis = 'accrual'
    business.dashboard_basis = basis
    business.save(update_fields=['dashboard_basis'])

    label = 'Cash Flow' if basis == 'cash' else 'Business Performance'
    messages.success(request, f"Dashboard now opens in {label} by default.")
    return redirect('dashboard', business_slug=business.slug)

AWAY_GAP_MINUTES = 30

def _away_summary(request, business):
    """'While you were away' — owner recap of what happened between dashboard
    visits. Window opens when the gap exceeds AWAY_GAP_MINUTES and lives in the
    session (survives refreshes) until dismissed with the ✕."""
    if request.user.role != 'owner':
        return None

    now = timezone.now()
    seen, created = DashboardSeen.objects.get_or_create(
        user=request.user, business=business, defaults={'seen_at': now})

    sess_key = f'away_banner_{business.id}'
    stored = request.session.get(sess_key)
    if isinstance(stored, list):        # migrate old [start, end] session format
        stored = stored[0]

    if not created and (now - seen.seen_at) >= timedelta(minutes=AWAY_GAP_MINUTES):
        stored = seen.seen_at.isoformat()   # a fresh gap restarts the window
        request.session[sess_key] = stored

    seen.seen_at = now
    seen.save(update_fields=['seen_at'])

    if not stored:
        return None

    # Live window: everything since the gap started, up to RIGHT NOW —
    # the end doesn't freeze at the moment the banner first appeared.
    start = datetime.fromisoformat(stored)
    end   = now

    sales       = Sale.objects.active().filter(business=business, created_at__range=(start, end))
    sales_count = sales.count()
    revenue     = sales.aggregate(t=Sum('total_revenue'))['t'] or 0
    # sale__in=sales, not a hand-rolled is_void filter: `sales` above is already
    # Sale.objects.active(), so the item count can't drift from the sale count and
    # revenue beside it (it used to omit status='completed' and counted drafts' items).
    items_sold  = (SaleItem.objects
                   .filter(sale__in=sales)
                   .aggregate(t=Sum('quantity'))['t'] or 0)
    purchases_count = Purchase.objects.active().filter(business=business, created_at__range=(start, end)).count()
    expenses_count  = Expense.objects.filter(business=business, created_at__range=(start, end)).count()
    waste_count     = Waste.objects.filter(business=business, created_at__range=(start, end)).count()

    if not any([sales_count, purchases_count, expenses_count, waste_count]):
        request.session.pop(sess_key, None)   # nothing happened — stay silent
        return None

    secs = int((end - start).total_seconds())
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    duration = f"{days}d {hours}h" if days else (f"{hours}h {minutes}m" if hours else f"{minutes}m")

    return {
        'start': start, 'end': end, 'duration': duration,
        'sales_count': sales_count, 'revenue': revenue, 'items_sold': items_sold,
        'purchases_count': purchases_count, 'expenses_count': expenses_count,
        'waste_count': waste_count,
    }

@login_required(login_url='login')
@require_POST
def dismiss_away_banner(request, business_slug):
    business = get_object_or_404(BusinessProfile, slug=business_slug)
    request.session.pop(f'away_banner_{business.id}', None)
    return redirect('dashboard', business_slug=business_slug)





