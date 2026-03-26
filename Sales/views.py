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

from Expense.models import Employee
from Expense.forms import EmployeeForm

from Inventory.models import Stock

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

from decimal import Decimal

# logging
import logging

# Create your views here.

@login_required(login_url='login')
def clear_sale(request):
    request.session['sale'] = {}
    request.session.modified = True
    messages.success(request, 'All items has been removed.')
    return redirect('view-sale')

@login_required(login_url='login')
def sale_list(request):
    sales = Sale.objects.all().order_by('-date')
    form = SaleFilterForm(request.GET or None)

    now = timezone.now()
    month = now.month
    today = now.day
    year = now.year
    iso_year, iso_week, iso_weekday = now.isocalendar()
    last_year = iso_year - 1
    print(last_year)
    period = request.GET.get('period')
    
    total_revenue = sales.total_revenue()
    average_total_revenue = sales.average_total_revenue()
    total_sales_count = sales.count()
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        select_month = form.cleaned_data.get('select_month')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')
        
        
        if start_date and end_date:
            sales = sales.filter(date__range=(start_date, end_date))
        
        if select_month:
            # Option 1: strptime — safer, validates format, better for untrusted input
            parsed = datetime.strptime(select_month, '%Y-%m')

            # Option 2: split — simpler, faster, fine when input is trusted (e.g. type="month")
            # year, month = map(int, select_month.split('-'))
            sales = sales.filter(date__month=parsed.month)
        
        if search:
            sales = sales.filter(
                Q(id__iexact=search) |
                Q(total_revenue__iexact=search) |
                Q(sale_items__quantity__iexact=search) |
                Q(line_count__iexact=search)

            )
            
        if period == 'last_week':
            if iso_week == 1:
                last_year_of_last_week = date(last_year, 12, 28).isocalendar()[1]
                sales = sales.filter(date__year=last_year, date__week=last_year_of_last_week)
            else:
                sales = sales.filter(date__year=iso_year, date__week=iso_week-1)
        
        period_map = {
            'month': {'date__month': month, 'date__year': year},
            'today': {'date__day': today},
            'week': {'date__week': iso_week, 'date__year': iso_year}

        }
        
        filter_kwargs = period_map.get(period)
        if filter_kwargs:
            sales = sales.filter(**filter_kwargs)
            
        total_revenue = sales.total_revenue()
        average_total_revenue = sales.average_total_revenue()
        total_sales_count = sales.count()
            
    paginator = Paginator(sales, 6)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'sales': page_obj.object_list, 
        'page_obj': page_obj,
        'total_revenue': total_revenue,
        'average_total_revenue': average_total_revenue,
        'total_sales_count': total_sales_count,
        'section': 'sale'
    }
        
    return render(request, 'Sales/sale_list.html', context)

@login_required(login_url='login')
def sale_detail(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    sale_items = sale.sale_items.select_related('product')
    sale_employees = sale.sale_employees.select_related('employee')
    
    total_salary_cost = sale_employees.aggregate(total_salary_cost=Sum('daily_rate'))['total_salary_cost'] or 0
    
    context = {'sale': sale, 'sale_items': sale_items, 'sale_employees': sale_employees, 'total_salary_cost': total_salary_cost}
    return render(request, 'Sales/sale_detail.html', context)

@login_required(login_url='login')
def add_to_sales(request, product_id):
    sale = request.session.get('sale', {})
    print('SALE TYPE:', type(sale))  # ← add this
    print('SALE VALUE:', sale) 
    product = get_object_or_404(Product, id=product_id)
    product_key = str(product.id)

    if product.prepared_quantity >= 1:
        if product_key in sale:
            sale[product_key]['quantity'] += 1
            messages.success(request, f"{product.name}'s quantity has increased.")
            
        else:
            sale[product_key] = {
                'id': product.id,
                'name': product.name,
                'quantity': 1,
                'cost_price': str(product.cost_price),
                'unsold_quantity' : 0,
                
            }
            messages.success(request, f"{product.name} added to the sale.")
    else:
        messages.warning(request, f"{product.name} - Insufficient stock.")
    
    request.session['sale'] = sale
    request.session.modified = True
    
    return redirect(f"{reverse('product-list')}?{request.META.get('QUERY_STRING', '')}")

@login_required(login_url='login')
def view_sale(request):
    sale = request.session.get('sale', {})
    total_revenue = 0
    total_cost_price = 0
    employees = Employee.objects.all()
    # after confirming who's in shift it will save the checkbox.
    selected_employee_ids = request.session.get('selected_employee_ids', [])
    print('session', selected_employee_ids)
    selected_employee_ids = [str(id) for id in selected_employee_ids]
    print('string', selected_employee_ids)
    total_salary_cost = Decimal(request.session.get('total_salary_cost', 0))
 
    items = []
    
    if sale:
        for product_id, data in sale.items():
            product = get_object_or_404(Product, id=product_id)
            quantity = data.get('quantity', 1)
            unsold_quantity = data.get('unsold_quantity', 0)
            cost_price = data.get('cost_price')
        
        
            # computations
            total_cost_price_per_line = Decimal(cost_price) * quantity         
            
            total_cost_price += total_cost_price_per_line
            
            line_total = product.selling_price * quantity
            total_revenue += line_total
            
            items.append({
                'id': product.id,
                'name': product.name,
                'selling_price': product.selling_price,
                'quantity': quantity,
                'cost_price': cost_price,
                'line_total': line_total,
                'unsold_quantity': unsold_quantity,
                'total_cost_price_per_line': total_cost_price_per_line,
                
            })
    
    line_count = len(items)
    request.session['sale'] = sale
    request.session['line_count'] = line_count
    request.session.modified = True
    
    context = {
        'items': items, 
        'total_revenue': total_revenue, 
        'total_cost_price': total_cost_price, 
        'employees': employees, 
        'selected_employee_ids': selected_employee_ids, 
        'total_salary_cost': total_salary_cost,
        'section': 'sale'
        
        }
    return render(request, 'Sales/view_sale.html', context)

@login_required(login_url='login')
def view_session_summary(request):
    sale = request.session.get('sale', {})
    total_revenue = 0
    total_cost_price = 0

    total_salary_cost = Decimal(request.session.get('total_salary_cost', 0))
    print('total_salary_cost', total_salary_cost)
    
    items = []
        
    if sale:
        for product_id, data in sale.items():
            product = get_object_or_404(Product, id=product_id)
            quantity = data.get('quantity', 1)
            cost_price = data.get('cost_price')
            unsold_quantity = data.get('unsold_quantity', 0)
            
            # computations

            
            total_cost_price_per_line = Decimal(cost_price) * quantity
            total_cost_price += total_cost_price_per_line
            
            line_total = product.selling_price * quantity
            total_revenue += line_total
   
            items.append({
                'id': product.id,
                'name': product.name,
                'selling_price': product.selling_price,
                'cost_price': cost_price,
                'quantity': quantity,
                'unsold_quantity': unsold_quantity,
                'total_cost_price_per_line': total_cost_price_per_line,
                'line_total': line_total,
                
            })
    request.session['sale'] = sale
    request.session.modified = True
    
    context = {
        'items': items, 
        'total_revenue': total_revenue, 
        'total_cost_price': total_cost_price, 
        'employees': 'employees', 
        'total_salary_cost': total_salary_cost, 
        'section': 'sale'
        }
    
    return render(request, 'Sales/view_session_summary.html', context)

@login_required(login_url='login')
def confirm_view_summary(request):
    sale = request.session.get('sale', {})
    line_count = request.session.get('line_count')
    total_salary_cost = request.session.get('total_salary_cost', 0)
    net_profit = 0
    total_revenue = 0
    total_cost_price = 0
    
    try:
        with transaction.atomic():
            sale_obj = Sale.objects.create(user=request.user, total_revenue=0, total_salary_cost=0)

            employee_ids = request.session.get('selected_employee_ids', [])
            print('employee_ids', employee_ids)
            employees = Employee.objects.filter(id__in=employee_ids)
            print('employees', employees)
            employee_id = employees.values_list('id', flat=True)
            print('employee_id', employee_id)
            
            for product_id, data in sale.items():
                product = get_object_or_404(Product, id=product_id)
                quantity = data.get('quantity', 1)
                cost_price = data.get('cost_price')
                unsold_quantity = data.get('unsold_quantity', 0)
                
                # computations 
                total_cost_price_per_line = Decimal(cost_price) * quantity
                total_cost_price += total_cost_price_per_line
                
                line_total = product.selling_price * quantity
                total_revenue += line_total
                
                try:
                    stock = Stock.objects.get(user=request.user, material=product.material)
                    stock.quantity -= quantity
                    stock.save()
                except:
                    pass
                
                SaleItem.objects.create(
                    sale=sale_obj,
                    product=product,
                    price_at_sale=product.selling_price,
                    cost_price=cost_price,
                    quantity=quantity,
                    unsold_quantity=unsold_quantity,
                )
                if product.prepared_quantity < quantity:
                    messages.warning(request, f"{product.name} - Insufficient stock.")
                
                product.prepared_quantity -= quantity
                product.save()
                
            for employee in employees:
                SaleEmployee.objects.create(
                    sale=sale_obj,
                    employee=employee,
                    daily_rate=employee.daily_rate,
                )
                
            # net profit 
            sale_obj.total_revenue = max(total_revenue, 0)
            sale_obj.total_salary_cost = total_salary_cost
            sale_obj.line_count = line_count
            sale_obj.save()

    except ValidationError:
        messages.error(request, f"Cannot complete the sale - Insufficient stock.")
        return redirect('view-sale')  # exits early if error occurs
    
    
    for key in ('total_salary_cost', 'line_count'):
        request.session.pop(key, 0)
    
    request.session['sale'] = {}
    request.session['selected_employee_ids'] = []
    request.session.modified = True
    
    return redirect('sale-summary', sale_obj.id)

@login_required(login_url='login')
def view_sale_summary(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    sale_items = sale.sale_items.select_related('product')
    total_salary_cost = sale.sale_employees.aggregate(total_salary_cost=Sum('daily_rate'))['total_salary_cost'] or 0
    print(total_salary_cost)
    
    total_revenue = 0
    total_cost_price = 0

    
    items = []

    for item in sale_items:
        cost_price = item.cost_price
        quantity = item.quantity
        unsold_quantity = item.unsold_quantity
        
        # computations
        total_cost_price_per_line = (cost_price * quantity)
        total_cost_price += total_cost_price_per_line
        
        line_total = item.product.selling_price * quantity
        total_revenue += line_total
        
        items.append({
            'id': item.product.id,
            'name': item.product.name,
            'quantity': item.quantity,
            'selling_price': item.product.selling_price,
            'unsold_quantity': unsold_quantity,
            'cost_price': cost_price,
            'total_cost_price_per_line': total_cost_price_per_line,
            'line_total': line_total,
            
        })

    context = {
        'items': items, 
        'sale': sale, 
        'total_cost_price': total_cost_price, 
        'total_revenue': total_revenue, 
        'total_salary_cost': total_salary_cost, 
        'section': 'sale'
        }
    
    return render(request, 'Sales/view_sale_summary.html', context)

@login_required(login_url='login')
def add_daily_rate_to_sale(request):
    total_salary_cost = 0
    if request.method == 'POST':
        employee_ids = request.POST.getlist('employees')
        
        employee = Employee.objects.filter(id__in=employee_ids)
        
        if employee_ids:
            total_salary_cost = employee.aggregate(daily_rate=Sum('daily_rate'))['daily_rate'] or 0
            
        if total_salary_cost:
            messages.success(request, f"{employee.count()} staff added to shift. ₱{total_salary_cost} labor cost will be logged to expenses.")
        else:
            messages.success(request, "Shift cleared. No labor cost will be recorded.")
            
        request.session['total_salary_cost'] = str(total_salary_cost)
        request.session['selected_employee_ids'] = employee_ids
        request.session.modified = True
            
    return redirect('view-sale')

@login_required(login_url='login')
def edit_view_sale_quantity(request, product_id):
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, id=product_id)
    product_key = str(product.id)
    if request.method == 'POST':
        raw_qty = request.POST.get(f"new_quantity", 1)
        new_quantity = int(raw_qty) if raw_qty else None
        
        if product.prepared_quantity >= new_quantity:
                
            if new_quantity < 1:
                new_quantity = 1
                
            sale[product_key]['quantity'] = new_quantity
                
        else:
            messages.warning(request, f"{product.name} - Insufficient stock.")
    
    request.session['sale'] = sale
    request.session.modified = True
    
    return redirect('view-sale')

@login_required(login_url='login')
def edit_total_cost_price(request, product_id):
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, id=product_id)
    product_key = str(product.id)
    
    if sale:
        data = sale[product_key]
        quantity = data.get('quantity', 0)
        cost_price = data.get('cost_price')
        raw_cost_price = request.POST.get('new_total_cost_price') 
        new_total_cost_price = Decimal(raw_cost_price) / quantity if raw_cost_price else None
        
        if new_total_cost_price and new_total_cost_price != Decimal(cost_price):
            sale[product_key]['cost_price'] = str(new_total_cost_price)
            messages.success(request, f"The total cost has been updated.")
    request.session['sale'] = sale
    request.session.modified = True
    
    return redirect('view-sale')

@login_required(login_url='login')
def edit_unsold_quantity(request, product_id):
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, id=product_id)
    product_key = str(product.id)
    if sale:
        new_unsold_quantity = int(request.POST.get(f"new_unsold_quantity"))
        quantity = sale[product_key]['quantity']
        
        if new_unsold_quantity <= quantity:
            sale[product_key]['unsold_quantity'] = new_unsold_quantity
            messages.success(request, f"The unsold quantity has been updated.")
        else:
            messages.warning(request, f"The unsold quantity can't exceed the quantity.")
        
    request.session['sale'] = sale
    request.session.modified = True
    
    return redirect('view-sale')

@login_required(login_url='login')
def delete_view_sale_quantity(request, product_id):
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, id=product_id)
    product_key = str(product.id)
    
    if product_key in sale:
        del sale[product_key]
        messages.success(request, f"{product.name} has been removed from the sale.")
        
    request.session['sale'] = sale
    request.session.modified = True
    
    return redirect('view-sale')



    
    
    
    


    
