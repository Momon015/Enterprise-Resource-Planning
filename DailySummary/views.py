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

from Expense.models import Employee, Purchase, PurchaseItem, Waste, WasteItem
from Expense.forms import EmployeeForm

from core.models import StatusModel

from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from urllib.parse import urlencode
from django.views.decorators.http import require_POST

from django.core.paginator import Paginator

from django.db.models import Q
from datetime import date, datetime
import calendar
from django.db.models import Sum, Avg

from DailySummary.forms import SummaryFilterForm

from django.db.models import F

from decimal import Decimal
from operator import itemgetter

# logging
import logging

# Create your views here.

@login_required(login_url='login')
def view_summary(request):
    sales = Sale.objects.all()
    purchases = Purchase.objects.all()
    wastes = Waste.objects.all()

    grand_net_profit = 0
    grand_total_cost = 0
    grand_total_revenue = 0
    grand_total_salary_cost = 0
    grand_total_waste_cost = 0
    
    wastes_by_date = wastes \
        .values('date').annotate(total_waste_cost=Sum('total_cost')) \
        .order_by('-date')
    
    sales_by_date = sales \
        .values('date') \
        .annotate(
            total_revenue=Sum('total_revenue'), 
            total_salary_cost=Sum('total_salary_cost')) \
        .order_by('-date')
    
    print(sales_by_date)

    purchase_by_date = purchases \
        .values('purchase_date') \
        .annotate(total_cost=Sum('total_cost')) \
        .order_by('-purchase_date')
        
    form = SummaryFilterForm(request.GET or None)
    
    period = request.GET.get('period', '')
    now = timezone.now()
    iso_year, iso_week, iso_weekday = now.isocalendar()

    if form.is_valid():
        start_date = form.cleaned_data.get('start_date', '')
        end_date = form.cleaned_data.get('end_date', '')
        search = form.cleaned_data.get('search', '')
        select_month = form.cleaned_data.get('select_month', '')

        if start_date and end_date:
            sales = sales.filter(date__range=(start_date, end_date))
            purchases = purchases.filter(purchase_date__range=(start_date, end_date))
            wastes = wastes.filter(date__range=(start_date, end_date))
            
        if select_month:
            parsed_year, parsed_month = map(int, select_month.split('-'))
            sales = sales.filter(date__month=parsed_month)
            purchases = purchases.filter(purchase_date__month=parsed_month)
            wastes = wastes.filter(date__month=parsed_month)
        
        if period == 'last_week':
            if iso_week == 1:
                last_year = iso_year - 1
                last_year_of_last_week = date(last_year, 12, 28).isocalendar()[1]
                sales = sales.filter(date__week=last_year_of_last_week, date__year=last_year)
                purchases = purchases.filter(purchase_date__week=last_year_of_last_week, purchase_date__year=last_year)
                wastes = wastes.filter(date__week=last_year_of_last_week, date__year=last_year)
                
            else:
                
                sales = sales.filter(date__week=iso_week-1, date__year=iso_year)
                purchases = purchases.filter(purchase_date__week=iso_week-1, purchase_date__year=iso_year)
                wastes = wastes.filter(date__week=iso_week-1, date__year=iso_year)
                
        if period == 'today':
            sales = sales.filter(date__day=now.day)
            purchases = purchases.filter(purchase_date__day=now.day)
            wastes = wastes.filter(date__day=now.day)

        if period == 'month':
            sales = sales.filter(date__month=now.month)
            purchases = purchases.filter(purchase_date__month=now.month)
            wastes = wastes.filter(date__month=now.month)
        
        sales_by_date = sales.values('date').annotate(total_salary_cost=Sum('total_salary_cost'), total_revenue=Sum('total_revenue')).order_by('-date')
        purchase_by_date = purchases.values('purchase_date').annotate(total_cost=Sum('total_cost')).order_by('-purchase_date')
        wastes_by_date = wastes.filter(waste_items__material__isnull=False).values('date').annotate(total_waste_cost=Sum('total_cost')).order_by('-date')

        if search:
            sales_by_date = sales_by_date.filter(
                Q(total_revenue__iexact=search) |
                Q(total_salary_cost__iexact=search) 
                
            )
            purchase_by_date = purchase_by_date.filter(total_cost__iexact=search)
            wastes_by_date = wastes_by_date.filter(total_unsold_cost__iexact=search)
        
    summary = {}
    for s in sales_by_date:
        summary[s['date']] = {
            'total_revenue': s['total_revenue'],
            'total_salary_cost': s['total_salary_cost'],
            'total_waste_cost': 0,
            'total_cost': 0,
        }

    for p in purchase_by_date:
        if p['purchase_date'] in summary:
            summary[p['purchase_date']]['total_cost'] = p['total_cost']
        else:
            summary[p['purchase_date']] = {
                'total_revenue': 0,
                'total_salary_cost': 0,
                'total_waste_cost': 0,
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
                'total_waste_cost': w['total_waste_cost']
                
            }
            

    summary_list = []
    if summary:
        for date, value in summary.items():
            total_revenue = value['total_revenue']
            total_cost = value['total_cost']
            total_salary_cost = value['total_salary_cost']
            total_waste_cost = value['total_waste_cost']
            
            net_profit = total_revenue - total_cost - total_salary_cost - total_waste_cost
            
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
                'net_profit': net_profit
            })
    
    sorted_list=sorted(summary_list, key=lambda x: x['date'], reverse=True)
            
    pagination = Paginator(summary_list, 6)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)

    context = {
        'summary_list': sorted_list,
        'page_obj': page_obj,
        'section': 'summary',
        'grand_total_cost': grand_total_cost,
        'grand_total_revenue': grand_total_revenue,
        'grand_total_waste_cost': grand_total_waste_cost,
        'grand_total_salary_cost': grand_total_salary_cost,
        'grand_net_profit': grand_net_profit,
    }
    
    return render(request, 'DailySummary/view_summary.html', context)

@login_required(login_url='login')
def view_summary_detail(request, date):
    sales = Sale.objects.filter(date=date)
    sale_items  = SaleItem.objects.filter(sale__in=sales)
    sale_employees = SaleEmployee.objects.filter(sale__in=sales)
    
    purchases = Purchase.objects.filter(purchase_date=date)
    purchase_items = PurchaseItem.objects.filter(purchase__in=purchases)
    
    wastes = Waste.objects.filter(date=date)
    waste_items = WasteItem.objects.filter(waste__in=wastes)

    net_profit = 0
    total_salary_cost = 0
    total_material_cost = 0
    total_waste_cost = 0
    total_revenue = 0
    
    for waste in waste_items:
        waste_cost = waste.price * waste.quantity
        total_waste_cost += waste_cost
        
    
    for purchase in purchase_items:
        material_cost = purchase.total_item_discount
        total_material_cost += material_cost
        
    for emp in sale_employees:
        salary_cost = emp.daily_rate
        total_salary_cost += salary_cost
        
    for item in sale_items:
        revenue = (item.price_at_sale * item.quantity)
        total_revenue += revenue
        
  
    net_profit = total_revenue - total_material_cost - total_salary_cost - total_waste_cost
    
    context = {
        'sales': sales,
        'purchases': purchases,
        'sale_items': sale_items, 
        'sale_employees': sale_employees, 
        'purchase_items': purchase_items,
        'wastes': wastes,
        'waste_items': waste_items,
        'net_profit': net_profit,
        'total_salary_cost': total_salary_cost,
        'total_material_cost': total_material_cost,
        'total_waste_cost': total_waste_cost,
        'total_revenue': total_revenue,
        'section': 'summary'
        }
    
    return render(request, 'DailySummary/view_summary_detail.html', context)