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



@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only')
@feature_required('has_dashboard')
def dashboard(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    today = timezone.localdate()

    # Today's querysets
    sales           = Sale.objects.filter(business=business, date=today)
    sale_items      = SaleItem.objects.filter(sale__in=sales)
    
    shifts          = Shift.objects.filter(business=business,date=today)
    shift_employees = ShiftEmployee.objects.filter(shift__in=shifts)
    purchases       = Purchase.objects.filter(business=business, purchase_date=today)
    purchase_items  = PurchaseItem.objects.filter(purchase__in=purchases)
    wastes          = Waste.objects.filter(business=business, date=today)
    waste_items     = WasteItem.objects.filter(waste__in=wastes)
    expenses        = Expense.objects.filter(business=business, date=today)

    # Today's totals
    total_revenue       = sales.aggregate(t=Sum('total_revenue'))['t'] or Decimal(0)
    total_expense_cost  = expenses.aggregate(t=Sum('total_amount'))['t'] or Decimal(0)
    total_salary_cost   = shifts.aggregate(t=Sum('amount'))['t'] or Decimal(0)
    total_material_cost = purchases.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)
    total_waste_cost    = wastes.aggregate(t=Sum('total_cost'))['t'] or Decimal(0)

    # for item in waste_items:
    #     total_waste_cost += item.price * item.quantity
    # for item in purchase_items:
    #     total_material_cost += item.total_item_discount
    # for emp in shift_employees:
    #     total_salary_cost += emp.daily_rate

    net_profit = total_revenue - total_material_cost - total_salary_cost - total_waste_cost - total_expense_cost

    # Weekly comparison
    this_week_start = today - timedelta(days=today.weekday())
    last_week_end   = this_week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)

    tw_revenue = float(Sale.objects.filter(business=business, date__gte=this_week_start).aggregate(t=Sum('total_revenue'))['t'] or 0)
    tw_cost    = float(Purchase.objects.filter(business=business, purchase_date__gte=this_week_start).aggregate(t=Sum('total_cost'))['t'] or 0)
    tw_waste   = float(Waste.objects.filter(business=business, date__gte=this_week_start).aggregate(t=Sum('total_cost'))['t'] or 0)
    tw_expense = float(Expense.objects.filter(business=business, date__gte=this_week_start).aggregate(t=Sum('total_amount'))['t'] or 0)
    tw_salary = float(Shift.objects.filter(business=business, date__gte=this_week_start).aggregate(t=Sum('amount'))['t'] or 0)
    tw_net     = tw_revenue - tw_cost - tw_waste - tw_expense - tw_salary

    lw_revenue = float(Sale.objects.filter(business=business, date__range=(last_week_start, last_week_end)).aggregate(t=Sum('total_revenue'))['t'] or 0)
    lw_cost    = float(Purchase.objects.filter(business=business, purchase_date__range=(last_week_start, last_week_end)).aggregate(t=Sum('total_cost'))['t'] or 0)
    lw_waste   = float(Waste.objects.filter(business=business, date__range=(last_week_start, last_week_end)).aggregate(t=Sum('total_cost'))['t'] or 0)
    lw_expense = float(Expense.objects.filter(business=business, date__range=(last_week_start, last_week_end)).aggregate(t=Sum('total_amount'))['t'] or 0)
    lw_salary  = float(ShiftEmployee.objects.filter(shift__business=business, shift__date__range=(last_week_start, last_week_end)).aggregate(t=Sum('daily_rate'))['t'] or 0)
    lw_net     = lw_revenue - lw_cost - lw_waste - lw_expense - lw_salary

    # Monthly comparison 
    this_month_start = today.replace(day=1)
    last_month_end   = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    tm_revenue = float(Sale.objects.filter(business=business, date__gte=this_month_start).aggregate(t=Sum('total_revenue'))['t'] or 0)
    tm_cost    = float(Purchase.objects.filter(business=business, purchase_date__gte=this_month_start).aggregate(t=Sum('total_cost'))['t'] or 0)
    tm_waste   = float(Waste.objects.filter(business=business, date__gte=this_month_start).aggregate(t=Sum('total_cost'))['t'] or 0)
    tm_expense = float(Expense.objects.filter(business=business, date__gte=this_month_start).aggregate(t=Sum('total_amount'))['t'] or 0)
    tm_salary = float(Shift.objects.filter(business=business, date__gte=this_month_start).aggregate(t=Sum('amount'))['t'] or 0)
    tm_net     = tm_revenue - tm_cost - tm_waste - tm_expense - tm_salary

    lm_revenue = float(Sale.objects.filter(business=business, date__range=(last_month_start, last_month_end)).aggregate(t=Sum('total_revenue'))['t'] or 0)
    lm_cost    = float(Purchase.objects.filter(business=business, purchase_date__range=(last_month_start, last_month_end)).aggregate(t=Sum('total_cost'))['t'] or 0)
    lm_waste   = float(Waste.objects.filter(business=business, date__range=(last_month_start, last_month_end)).aggregate(t=Sum('total_cost'))['t'] or 0)
    lm_expense = float(Expense.objects.filter(business=business, date__range=(last_month_start, last_month_end)).aggregate(t=Sum('total_amount'))['t'] or 0)
    lm_salary  = float(Shift.objects.filter(business=business, date__range=(last_month_start, last_month_end)).aggregate(t=Sum('amount'))['t'] or 0)
    lm_net     = lm_revenue - lm_cost - lm_waste - lm_expense - lm_salary

    # 30-day revenue trend 
    thirty_days_ago = today - timedelta(days=29)
    daily_sales = (
        Sale.objects.filter(business=business, date__gte=thirty_days_ago)
        .values('date').annotate(rev=Sum('total_revenue')).order_by('date')
    )
    trend_labels = [(thirty_days_ago + timedelta(days=i)).strftime('%b %d') for i in range(30)]
    revenue_map  = {s['date'].strftime('%b %d'): float(s['rev']) for s in daily_sales}
    trend_data   = [revenue_map.get(label, 0) for label in trend_labels]

    context = {
        
        # today's querysets (for KPI row, doughnut chart, day summary counts)
        'sales': sales,
        'sale_items': sale_items,
        'shift_employees': shift_employees,
        'purchases': purchases,
        'purchase_items': purchase_items,
        'wastes': wastes,
        'waste_items': waste_items,
        'expenses': expenses,
        
        # today's totals
        'total_revenue': total_revenue,
        'total_material_cost': total_material_cost,
        'total_salary_cost': total_salary_cost,
        'total_waste_cost': total_waste_cost,
        'total_expense_cost': total_expense_cost,
        'net_profit': net_profit,
        'today': today,
        
        # weekly comparison
        'this_week_label': f"Wk {this_week_start.strftime('%b %d')}",
        'last_week_label': f"Wk {last_week_start.strftime('%b %d')}",
        'tw_revenue': tw_revenue, 'tw_cost': tw_cost,
        'tw_waste': tw_waste, 'tw_expense': tw_expense, 'tw_salary': tw_salary, 'tw_net': tw_net,
        'lw_revenue': lw_revenue, 'lw_cost': lw_cost,
        'lw_waste': lw_waste, 'lw_expense': lw_expense, 'lw_salary': lw_salary, 'lw_net': lw_net,
        
        # monthly comparison
        'this_month_label': today.strftime('%B'),
        'last_month_label': last_month_end.strftime('%B'),
        'tm_revenue': tm_revenue, 'tm_cost': tm_cost,
        'tm_waste': tm_waste, 'tm_expense': tm_expense, 'tm_salary': tm_salary, 'tm_net': tm_net,
        'lm_revenue': lm_revenue, 'lm_cost': lm_cost,
        'lm_waste': lm_waste, 'lm_expense': lm_expense, 'lm_salary': lm_salary, 'lm_net': lm_net,
        
        # trend chart
        'trend_labels': json.dumps(trend_labels),
        'trend_data': json.dumps(trend_data),
        'section': 'dashboard',
    }

    return render(request, 'Dashboard/dashboard.html', context)




