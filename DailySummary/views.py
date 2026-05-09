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

    sales = get_queryset_for_user(request.user, Sale.objects.all()).filter(business=business)
    purchases = get_queryset_for_user(request.user, Purchase.objects.all()).filter(business=business)
    wastes = get_queryset_for_user(request.user, Waste.objects.all()).filter(business=business)
    expenses = get_queryset_for_user(request.user, Expense.objects.all()).filter(business=business)
    shifts = get_queryset_for_user(request.user, Shift.objects.all()).filter(business=business)
    
    grand_net_profit = 0
    grand_total_cost = 0
    grand_total_revenue = 0
    grand_total_salary_cost = 0
    grand_total_waste_cost = 0
    grand_total_expense_cost = 0
    
    expenses_by_date = expenses.values('date').annotate(total_expense_cost=Sum('total_amount')).order_by('-date')
    wastes_by_date = wastes.values('date').annotate(total_waste_cost=Sum('total_cost')).order_by('-date')
    sales_by_date = sales.values('date').annotate(total_revenue=Sum('total_revenue')).order_by('-date')
    shifts_by_date = shifts.values('date').annotate(total_salary_cost=Sum('amount')).order_by('-date')
    purchase_by_date = purchases.values('purchase_date').annotate(total_cost=Sum('total_cost')).order_by('-purchase_date')
         
    print(sales_by_date)

    form = SummaryFilterForm(request.GET or None)
    
    period = request.GET.get('period', '')
    now = timezone.now()
    iso_year, iso_week, iso_weekday = now.isocalendar()

    
    current_year = f"{now.year}-01"
    
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
            sales = sales.filter(date__month=parsed_month)
            purchases = purchases.filter(purchase_date__month=parsed_month)
            wastes = wastes.filter(date__month=parsed_month)
            shifts = shifts.filter(date__range=(start_date, end_date))
        
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
                
        if period == 'today':
            sales = sales.filter(date__day=now.day)
            purchases = purchases.filter(purchase_date__day=now.day)
            wastes = wastes.filter(date__day=now.day)
            expenses = expenses.filter(date__day=now.day)
            shifts = shifts.filter(date__day=now.day)


        if period == 'month':
            sales = sales.filter(date__month=now.month)
            purchases = purchases.filter(purchase_date__month=now.month)
            wastes = wastes.filter(date__month=now.month)
            expenses = expenses.filter(date__month=now.month)
            shifts = shifts.filter(date__month=now.month)
        
        sales_by_date = sales.values('date').annotate(total_salary_cost=Sum('total_salary_cost'), total_revenue=Sum('total_revenue')).order_by('-date')
        purchase_by_date = purchases.values('purchase_date').annotate(total_cost=Sum('total_cost')).order_by('-purchase_date')
        wastes_by_date = wastes.values('date').annotate(total_waste_cost=Sum('total_cost')).order_by('-date')
        expenses_by_date = expenses.values('date').annotate(total_expense_cost=Sum('total_amount')).order_by('-date')
        shifts_by_date = shifts.values('date').annotate(total_salary_cost=Sum('amount')).order_by('-date')
        
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
            total_cost = value['total_cost']
            total_salary_cost = value['total_salary_cost']
            total_waste_cost = value['total_waste_cost']
            total_expense_cost = value['total_expense_cost']
            
            net_profit = total_revenue - total_cost - total_salary_cost - total_waste_cost - total_expense_cost
            
            grand_total_expense_cost += total_expense_cost
            grand_total_waste_cost += total_waste_cost
            grand_total_revenue += total_revenue
            grand_total_salary_cost += total_salary_cost
            grand_total_cost += total_cost
            grand_net_profit += net_profit
            
            summary_list.append({
                'date': date,
                'total_salary_cost': total_salary_cost,
                'total_cost': total_cost,
                'total_revenue': total_revenue,
                'total_waste_cost': total_waste_cost,
                'total_expense_cost': total_expense_cost,
                'net_profit': net_profit
            })
    
    sorted_list=sorted(summary_list, key=lambda x: x['date'], reverse=True)
            
    pagination = Paginator(sorted_list, 11)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    # most profitable of the month
    rev_by_month = {s['date__month']: s['total'] for s in sales.filter(date__year=now.year).values('date__month').annotate(total=Sum('total_revenue'))}
    cost_by_month = {p['purchase_date__month']: p['total'] for p in purchases.filter(purchase_date__year=now.year).values('purchase_date__month').annotate(total=Sum('total_cost'))}
    waste_by_cost = {w['date__month']: w['total'] for w in wastes.filter(date__year=now.year).values('date__month').annotate(total=Sum('total_cost'))}
    expense_by_cost = {e['date__month']: e['total'] for e in expenses.filter(date__year=now.year).values('date__month').annotate(total=Sum('total_amount'))}
    salary_by_month = {s['date__month']: s['total'] for s in sales.filter(date__year=now.year).values('date__month').annotate(total=Sum('total_salary_cost'))}
    
    all_months = set(list(rev_by_month) + list(cost_by_month) + list(waste_by_cost) + list(expense_by_cost) + list(salary_by_month))
    best_month_name = 'N/A'
    best_month_profit = 0
    for m in all_months:
        profit = (rev_by_month.get(m) or 0) - (cost_by_month.get(m) or 0) - (waste_by_cost.get(m) or 0) - (expense_by_cost.get(m) or 0) - (salary_by_month.get(m) or 0)
        if profit > best_month_profit:
            best_month_profit = profit
            best_month_name = calendar.month_name[m]

    context = {
        'summary_list': sorted_list,
        'page_obj': page_obj,
        'section': 'summary',
        'grand_total_cost': grand_total_cost,
        'grand_total_revenue': grand_total_revenue,
        'grand_total_waste_cost': grand_total_waste_cost,
        'grand_total_salary_cost': grand_total_salary_cost,
        'grand_total_expense_cost': grand_total_expense_cost,
        'grand_net_profit': grand_net_profit,
        'current_year': current_year,
        
        'best_month_name': best_month_name,
        'best_month_profit': best_month_profit,
    }
    
    return render(request, 'DailySummary/view_summary.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def view_summary_detail(request, business_slug, date):
    business = get_business_for_user(request.user, business_slug)
    net_profit = 0
    
    sales = Sale.objects.filter(business=business, date=date)
    sale_items  = SaleItem.objects.filter(sale__in=sales)
    sale_employees = SaleEmployee.objects.filter(sale__in=sales)
    total_revenue = sales.aggregate(revenue=Sum('total_revenue'))['revenue'] or 0
    
    purchases = Purchase.objects.filter(business=business, purchase_date=date)
    purchase_items = PurchaseItem.objects.filter(purchase__in=purchases)
    total_material_cost = purchases.aggregate(material_cost=Sum('total_cost'))['material_cost'] or 0
    
    wastes = Waste.objects.filter(business=business, date=date)
    waste_items = WasteItem.objects.filter(waste__in=wastes)
    total_waste_cost = wastes.aggregate(waste_cost=Sum('total_cost'))['waste_cost'] or 0
    
    expenses = Expense.objects.filter(business=business, date=date)
    total_expense_cost = expenses.aggregate(total_expense_cost=Sum('total_amount'))['total_expense_cost'] or 0
    
    shifts = Shift.objects.filter(business=business, date=date)
    shift_employees = ShiftEmployee.objects.filter(shift__in=shifts)
    total_salary_cost = shifts.aggregate(salary_cost=Sum('amount'))['salary_cost'] or 0
    
    # for waste in waste_items:
    #     waste_cost = waste.price * waste.quantity
    #     total_waste_cost += waste_cost
        
    
    # for purchase in purchase_items:
    #     material_cost = purchase.total_item_discount
    #     total_material_cost += material_cost
        
    # for emp in sale_employees:
    #     salary_cost = emp.daily_rate
    #     total_salary_cost += salary_cost
        
    # for item in sale_items:
    #     revenue = (item.price_at_sale * item.quantity)
    #     total_revenue += revenue
        
  
    net_profit = total_revenue - total_material_cost - total_salary_cost - total_waste_cost - total_expense_cost
    
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
        'section': 'summary'
    }
    
    return render(request, 'DailySummary/view_summary_detail.html', context)