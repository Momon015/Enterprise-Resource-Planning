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

from Sales.models import Sale, SaleItem, SaleEmployee
from Sales.forms import SaleForm, SaleFilterForm

from Product.models import Product
from Product.forms import ProductForm

from Expense.models import Employee, Purchase, PurchaseItem, Waste, WasteItem, Expense, MiscExpense, Shift, ShiftEmployee
from Expense.forms import EmployeeForm

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

# Create your views here.

from django.core.cache import cache
from django.utils import timezone

CACHE_TTL = 60 * 5   # 5 minutes — adjust as needed


def _compute_dashboard_metrics(business, today):
    """All the expensive aggregates. Cached separately so we don't recompute per request."""
    # Today's totals
    sales_today      = Sale.objects.filter(business=business, date=today)
    purchases_today  = Purchase.objects.filter(business=business, purchase_date=today)
    wastes_today     = Waste.objects.filter(business=business, date=today)
    expenses_today   = Expense.objects.filter(business=business, date=today)
    shifts_today     = Shift.objects.filter(business=business, date=today)

    total_revenue       = sales_today.aggregate(t=Sum('total_revenue'))['t'] or Decimal(0)
    total_expense_cost  = expenses_today.aggregate(t=Sum('total_amount'))['t'] or Decimal(0)
    total_salary_cost   = shifts_today.aggregate(t=Sum('amount'))['t'] or Decimal(0)
    total_material_cost = purchases_today.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)
    total_waste_cost    = wastes_today.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)
    net_profit = total_revenue - total_material_cost - total_salary_cost - total_waste_cost - total_expense_cost

    # Weekly comparison
    this_week_start = today - timedelta(days=today.weekday())
    last_week_end   = this_week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)

    def _bucket(qs, field, date_filter):
        return float(qs.filter(business=business, **date_filter).aggregate(t=Sum(field))['t'] or 0)

    tw_revenue = _bucket(Sale.objects, 'total_revenue', {'date__gte': this_week_start})
    tw_cost    = _bucket(Purchase.objects, 'total_cost', {'purchase_date__gte': this_week_start})
    tw_waste   = _bucket(Waste.objects, 'total_cost', {'date__gte': this_week_start})
    tw_expense = _bucket(Expense.objects, 'total_amount', {'date__gte': this_week_start})
    tw_salary  = _bucket(Shift.objects, 'amount', {'date__gte': this_week_start})
    tw_net     = tw_revenue - tw_cost - tw_waste - tw_expense - tw_salary

    lw_revenue = _bucket(Sale.objects, 'total_revenue', {'date__range': (last_week_start, last_week_end)})
    lw_cost    = _bucket(Purchase.objects, 'total_cost', {'purchase_date__range': (last_week_start, last_week_end)})
    lw_waste   = _bucket(Waste.objects, 'total_cost', {'date__range': (last_week_start, last_week_end)})
    lw_expense = _bucket(Expense.objects, 'total_amount', {'date__range': (last_week_start, last_week_end)})
    lw_salary  = _bucket(Shift.objects, 'amount', {'date__range': (last_week_start, last_week_end)})
    lw_net     = lw_revenue - lw_cost - lw_waste - lw_expense - lw_salary

    # Monthly comparison
    this_month_start = today.replace(day=1)
    last_month_end   = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    tm_revenue = _bucket(Sale.objects, 'total_revenue', {'date__gte': this_month_start})
    tm_cost    = _bucket(Purchase.objects, 'total_cost', {'purchase_date__gte': this_month_start})
    tm_waste   = _bucket(Waste.objects, 'total_cost', {'date__gte': this_month_start})
    tm_expense = _bucket(Expense.objects, 'total_amount', {'date__gte': this_month_start})
    tm_salary  = _bucket(Shift.objects, 'amount', {'date__gte': this_month_start})
    tm_net     = tm_revenue - tm_cost - tm_waste - tm_expense - tm_salary

    lm_revenue = _bucket(Sale.objects, 'total_revenue', {'date__range': (last_month_start, last_month_end)})
    lm_cost    = _bucket(Purchase.objects, 'total_cost', {'purchase_date__range': (last_month_start, last_month_end)})
    lm_waste   = _bucket(Waste.objects, 'total_cost', {'date__range': (last_month_start, last_month_end)})
    lm_expense = _bucket(Expense.objects, 'total_amount', {'date__range': (last_month_start, last_month_end)})
    lm_salary  = _bucket(Shift.objects, 'amount', {'date__range': (last_month_start, last_month_end)})
    lm_net     = lm_revenue - lm_cost - lm_waste - lm_expense - lm_salary

    # 30-day trend
    thirty_days_ago = today - timedelta(days=29)
    daily_sales = (
        Sale.objects.filter(business=business, date__gte=thirty_days_ago)
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
    }


def _get_cached_dashboard_metrics(business, today):
    cache_key = f'dashboard:metrics:{business.id}:{today.isoformat()}'
    metrics = cache.get(cache_key)
    if metrics is None:
        metrics = _compute_dashboard_metrics(business, today)
        cache.set(cache_key, metrics, timeout=CACHE_TTL)
    return metrics


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
    sales           = Sale.objects.filter(business=business, date=today)
    sale_items      = SaleItem.objects.filter(sale__in=sales)
    shifts          = Shift.objects.filter(business=business, date=today)
    shift_employees = ShiftEmployee.objects.filter(shift__in=shifts)
    purchases       = Purchase.objects.filter(business=business, purchase_date=today)
    purchase_items  = PurchaseItem.objects.filter(purchase__in=purchases)
    wastes          = Waste.objects.filter(business=business, date=today)
    waste_items     = WasteItem.objects.filter(waste__in=wastes)
    expenses        = Expense.objects.filter(business=business, date=today)

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
    }
    return render(request, 'Dashboard/dashboard.html', context)




