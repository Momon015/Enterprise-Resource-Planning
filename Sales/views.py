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

from datetime import date, datetime
import calendar
from django.db.models import Sum, Avg, Max, Q, F

from decimal import Decimal

from core.utils.owner import get_owner, permission_required, get_queryset_for_user, get_business_for_user, filter_to_own_if_staff

from user.models import User
from django.contrib.messages import get_messages

from subscription.decorators import capacity_required

from activity.models import ActivityEvent
from activity.utils import log_activity, scope_events_for_user
# logging
import logging

# Create your views here.

@login_required(login_url='login')
def clear_sale(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    request.session['sale'] = {}
    request.session.modified = True
    messages.success(request, 'All items has been removed.')
    
    # HTMX
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'core/partials/_cart_response.html', {
            'cart_count':     0,
            'cart_items':     0,
            'total':          Decimal('0'),
            'cart_url':       'view-sale',
            'icon':           'bi-file-text',
            'label':          'Sales Record',
            'clear_sessions': 'clear-sale',
            'name':           'Products',
            'total_name':     'sales',
            'type':           'sales',
            'messages':       get_messages(request),
        })
    
    return redirect('product-list', business_slug=business.slug)

@login_required(login_url='login')
# @permission_required('staff_view')
@permission_required('read_only') # dev
def sale_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sales = get_queryset_for_user(request.user, Sale.objects.all()).filter(business=business).order_by('-reference')
    # for staff to see their own records
    sales = filter_to_own_if_staff(request.user, sales) 
    form = SaleFilterForm(request.GET or None)
    
    today = timezone.localdate()
    month = today.month
    year = today.year
    iso_year, iso_week, iso_weekday = today.isocalendar()
    last_year = iso_year - 1

    current_year = f"{year}-{month}"

    period = request.GET.get('period')
    
    total_revenue = sales.total_revenue()
    average_total_revenue = sales.average_total_revenue()
    total_sales_count = sales.count()
    
    if form.is_valid():
        # search = form.cleaned_data.get('search')
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
        
        # if search:
        #     select_month = ''
        #     sales = sales.filter(
        #         Q(id__iexact=search) |
        #         Q(total_revenue__iexact=search) |
        #         Q(sale_items__quantity__iexact=search) |
        #         Q(line_count__iexact=search) 

        #     ).distinct() # <--- this allows to not have multiple rows when u filter 
            
        if period == 'last_week':
            if iso_week == 1:
                last_year_of_last_week = date(last_year, 12, 28).isocalendar()[1]
                sales = sales.filter(date__year=last_year, date__week=last_year_of_last_week)
            else:
                sales = sales.filter(date__year=iso_year, date__week=iso_week-1)
        
        period_map = {
            'month': {'date__month': month, 'date__year': year},
            'today': {'date': today},
            'week': {'date__week': iso_week, 'date__year': iso_year}

        }
        
        filter_kwargs = period_map.get(period)
        if filter_kwargs:
            sales = sales.filter(**filter_kwargs)
            
        total_revenue = sales.total_revenue()
        average_total_revenue = sales.average_total_revenue()
        total_sales_count = sales.count()
        
    max_revenue = sales.aggregate(max=Max('total_revenue'))['max'] or 0
    
            
    paginator = Paginator(sales, 6)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    recent_events = ActivityEvent.objects.filter(
        verb__startswith='sale.', business=business,
    )
    recent_events = scope_events_for_user(recent_events, request.user)[:4]
    
    from core.utils.kpis import get_sale_kpis
    kpis = get_sale_kpis(business)
    
    context = {
        'page_obj': page_obj,
        'total_revenue': total_revenue,
        'average_total_revenue': average_total_revenue,
        'total_sales_count': total_sales_count,
        'max_revenue': max_revenue,
        'current_year': current_year, # this is for dynamic year for select month
        'section': 'sale',
        'recent_events': recent_events,
        'kpis': kpis,
        
    }
        
    return render(request, 'Sales/sale_list.html', context)

@login_required(login_url='login')
# @permission_required('staff_view')
@permission_required('read_only') # dev
def sale_detail(request, sale_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)
    sale_items = sale.sale_items.select_related('product')
    sale_employees = sale.sale_employees.select_related('employee')
    total_salary_cost = sale_employees.aggregate(total_salary_cost=Sum('daily_rate'))['total_salary_cost'] or 0
    
    context = {'sale': sale, 'sale_items': sale_items, 'sale_employees': sale_employees, 'total_salary_cost': total_salary_cost}
    return render(request, 'Sales/sale_detail.html', context)

@login_required(login_url='login')
@permission_required('add') # dev
def add_to_sales(request, product_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=product_id)
    product_key = str(product.id)

    if product.prepared_quantity >= 1:
        if product_key in sale:
            if sale[product_key]['quantity'] < product.prepared_quantity:
                sale[product_key]['quantity'] += 1
                messages.success(request, f"{product.name}'s quantity has increased.")
            else:
                messages.warning(request, f"{product.name} - Insufficient stock.")
        else:
            sale[product_key] = {
                'id': product.id,
                'name': product.name,
                'quantity': 1,
                'cost_price': str(product.cost_price),
                'selling_price': str(product.selling_price),
                
            }
            messages.success(request, f"{product.name} added to the sale.")
    else:
        messages.warning(request, f"{product.name} - Insufficient stock.")
    
    request.session['sale'] = sale
    request.session.modified = True
    
    # HTMX
    if request.headers.get('HX-Request') == 'true':
        total = sum(Decimal(str(item['selling_price'])) * item['quantity']
                for item in sale.values()        
        )

        return render(request, 'core/partials/_cart_response.html', {
            'label': 'Sales Record',
            'icon': 'bi-file-text',
            'total': total,
            'messages': get_messages(request),
            'cart_items': len(sale),
            'cart_count': sum(item['quantity'] for item in sale.values()),
            'cart_url': 'view-sale',
            'clear_sessions': 'clear-sale',
            'name': 'Products',
            'total_name': 'sales',
            'type': 'sales',
            
        })
    
    query_string = request.META.get('QUERY_STRING', '')
    url = reverse('product-list', kwargs={'business_slug': business.slug})
    return redirect(f"{url}?{query_string}" if query_string else url)

@login_required(login_url='login')
@permission_required('view') # dev
def view_sale(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})
    total_revenue = 0
    total_cost_price = 0
    # employees = Employee.objects.filter(user=owner)
    
    # # after confirming who's in shift it will save the checkbox.
    # selected_employee_ids = request.session.get('selected_employee_ids', [])
    # print('session', selected_employee_ids)
    # selected_employee_ids = [str(id) for id in selected_employee_ids]
    # print('string', selected_employee_ids)
    # total_salary_cost = Decimal(request.session.get('total_salary_cost', 0))
 
    items = []
    
    if sale:
        for product_id, data in sale.items():
            product = get_object_or_404(Product, business=business, id=product_id)
            quantity = data.get('quantity', 1)
            selling_price = data.get('selling_price')
            cost_price = data.get('cost_price')
        
        
            # computations
            total_cost_price_per_line = Decimal(cost_price) * quantity         
            
            total_cost_price += total_cost_price_per_line
            
            total_selling_price = Decimal(selling_price) * quantity
            total_revenue += total_selling_price
            

            
            # preset = product.product_preset_items.first()
            # if preset:
            #     supplier_name = preset.supplier_name
            # else:
            #     supplier_name = 'No supplier'

            items.append({
                'supplier': product.material.supplier.name if product.material else '',
                'id': product.id,
                'name': product.name,
                'selling_price': selling_price,
                'quantity': quantity,
                'cost_price': cost_price,
                'total_selling_price': str(total_selling_price),
                'total_cost_price_per_line': total_cost_price_per_line,
                
            })
    
    line_count = len(items)
    request.session['sale'] = sale
    request.session['line_count'] = line_count
    request.session.modified = True
    
    paginator = Paginator(items, 7)
    page = request.GET.get('page')
    page_obj =  paginator.get_page(page)
    
    # How many filler rows to reach a full page
    blank_rows = range(paginator.per_page - len(page_obj.object_list))
    
    context = {
        'items': items, 
        'total_revenue': total_revenue, 
        'total_cost_price': total_cost_price,
        'page_obj': page_obj,
        'blank_rows': blank_rows,
        'section': 'sale'
        # 'employees': employees, 
        # 'selected_employee_ids': selected_employee_ids, 
        # 'total_salary_cost': total_salary_cost,

        }
    
    return render(request, 'Sales/view_sale.html', context)

@login_required(login_url='login')
@permission_required('view') # dev
def view_session_summary(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})
    total_revenue = 0
    total_cost_price = 0

    total_salary_cost = Decimal(request.session.get('total_salary_cost', 0))
    print('total_salary_cost', total_salary_cost)
    
    items = []
        
    if sale:
        for product_id, data in sale.items():
            product = get_object_or_404(Product, business=business, id=product_id)
            quantity = data.get('quantity', 1)
            cost_price = data.get('cost_price', 0)
            selling_price = data.get('selling_price', 0)
            
            # computations
            total_cost_price_per_line = Decimal(cost_price) * quantity
            total_cost_price += total_cost_price_per_line
            
            total_selling_price = Decimal(selling_price) * quantity
            total_revenue += total_selling_price
   
            items.append({
                'supplier_name': product.material.supplier.name if product.material else '',
                'id': product.id,
                'name': product.name,
                'selling_price': selling_price,
                'cost_price': cost_price,
                'quantity': quantity,
                'total_selling_price': str(total_selling_price),
                'total_cost_price_per_line': total_cost_price_per_line,

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
@capacity_required('sale')
@permission_required('update') # dev
def confirm_view_summary(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    sale = request.session.get('sale', {})
    line_count = request.session.get('line_count', 0)
    total_salary_cost = request.session.get('total_salary_cost', 0)
    total_revenue = 0
    total_cost_price = 0
    
    try:
        with transaction.atomic():
            sale_obj = Sale.objects.create(
                user=business.user,
                business=business, 
                total_revenue=0, 
                total_salary_cost=0, 
                created_by=request.user)

            # employee_ids = request.session.get('selected_employee_ids', [])
            # print('employee_ids', employee_ids)
            # employees = Employee.objects.filter(id__in=employee_ids, user=owner)
            # print('employees', employees)
            # employee_id = employees.values_list('id', flat=True)
            # print('employee_id', employee_id)
            
            for product_id, data in sale.items():
                product = get_object_or_404(Product, business=business, id=product_id)
                quantity = data.get('quantity', 1)
                cost_price = data.get('cost_price', 0)
                selling_price = data.get('selling_price', 0)
                
                # computations 
                total_cost_price_per_line = Decimal(cost_price) * quantity
                total_cost_price += total_cost_price_per_line
                
                total_selling_price = Decimal(selling_price) * quantity
                total_revenue += total_selling_price
                
                try:
                    stock = Stock.objects.get(business=business, material=product.material) # I removed the created_by=request.user because it's using filter/get
                    if stock.quantity >= quantity:
                        stock.quantity -= quantity
                        stock.save()
                    else:
                        messages.error(request, f"{stock.name}'s quantity - Insufficient stock. ")
                except:
                    pass
                
                SaleItem.objects.create(
                    sale=sale_obj,
                    product=product,
                    price_at_sale=selling_price,
                    cost_price=cost_price,
                    quantity=quantity,
                )
                if product.prepared_quantity < quantity:
                    messages.warning(request, f"{product.name} - Insufficient stock.")
                
                product.prepared_quantity -= quantity
                product.save()
                
            # for employee in employees:
            #     SaleEmployee.objects.create(
            #         sale=sale_obj,
            #         employee=employee,
            #         daily_rate=employee.daily_rate,
            #     )
                
            # net profit 
            sale_obj.total_revenue = max(total_revenue, 0)
            sale_obj.total_salary_cost = total_salary_cost
            sale_obj.line_count = line_count
            sale_obj.save()
            
            log_activity(business, request.user, 'sale.completed',
                target=sale_obj,
                description=f"{sale_obj.reference}: {sale_obj.quantity_item()} item(s) — ₱{sale_obj.total_revenue:.2f}",
                metadata={
                    'reference': sale_obj.reference,
                    'total': str(sale_obj.total_revenue),
                    'line_count': sale_obj.line_count,
                })

    except ValidationError:
        messages.error(request, f"Cannot complete the sale - Insufficient stock.")
        return redirect('view-sale', business_slug=business.slug)  # exits early if error occurs
    
    
    for key in ('total_salary_cost', 'line_count'):
        request.session.pop(key, 0)
    
    request.session['sale'] = {}
    # request.session['selected_employee_ids'] = []
    request.session.modified = True
    
    return redirect('sale-summary', business_slug=sale_obj.business.slug, sale_id=sale_obj.id)

@login_required(login_url='login')
@permission_required('view') # dev
def view_sale_summary(request, sale_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)
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

        total_cost_price_per_line = (cost_price * quantity)
        total_cost_price += total_cost_price_per_line

        total_selling_price = item.price_at_sale * quantity
        total_revenue += total_selling_price

        # Safely walk product → material → supplier 
        product  = item.product
        material = product.material if product else None
        supplier = material.supplier if material else None

        items.append({
            'supplier_name':              supplier.name if supplier else '',
            'id':                         product.id if product else None,
            'name':                       item.name,
            'quantity':                   item.quantity,
            'selling_price':              item.price_at_sale,
            'unsold_quantity':            unsold_quantity,
            'cost_price':                 cost_price,
            'total_cost_price_per_line':  total_cost_price_per_line,
            'total_selling_price':        total_selling_price,
        })

        
    paginator = Paginator(items, 6)
    page = request.GET.get('page')
    page_obj =  paginator.get_page(page)
    
    # How many filler rows to reach a full page
    blank_rows = range(paginator.per_page - len(page_obj.object_list))

    context = {
        'items': items, 
        'sale': sale,
        'page_obj': page_obj,
        'blank_rows': blank_rows,
        'total_cost_price': total_cost_price, 
        'total_revenue': total_revenue, 
        'total_salary_cost': total_salary_cost, 
        'section': 'sale'
        }
    
    return render(request, 'Sales/view_sale_summary.html', context)

# @login_required(login_url='login')
# @permission_required('staff_add')
# def add_daily_rate_to_sale(request):
#     business = get_business_for_user(request.user, business_slug)
#     total_salary_cost = 0
#     if request.method == 'POST':
#         employee_ids = request.POST.getlist('employees')
        
#         employee = Employee.objects.filter(id__in=employee_ids, user=owner)
        
#         if employee_ids:
#             total_salary_cost = employee.aggregate(daily_rate=Sum('daily_rate'))['daily_rate'] or 0
            
#         if total_salary_cost:
#             messages.success(request, f"{employee.count()} staff added to shift. ₱{total_salary_cost} labor cost will be logged in summary.")
#         else:
#             messages.success(request, "Shift cleared. No labor cost will be recorded.")
            
#         request.session['total_salary_cost'] = str(total_salary_cost)
#         request.session['selected_employee_ids'] = employee_ids
#         request.session.modified = True
            
#     return redirect('view-sale')

@login_required(login_url='login')
def edit_view_sale_quantity(request, product_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=product_id)
    product_key = str(product.id)
    if request.method == 'POST':
        raw_qty = request.POST.get(f"new_quantity", 1)
        new_quantity = int(raw_qty) if raw_qty else None
        
        if product.prepared_quantity >= new_quantity:
                
            if new_quantity < 1:
                new_quantity = 1
                
            sale[product_key]['quantity'] = new_quantity
            messages.success(request, f"{product.name}'s quantity has been updated.")
        else:
            messages.warning(request, f"{product.name} - Insufficient stock.")
    
    request.session['sale'] = sale
    request.session.modified = True
    
    page = request.GET.get('page', '')
    url = reverse('view-sale', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)
    
@login_required(login_url='login')
def edit_total_selling_price(request, product_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=product_id)
    product_key = str(product.id)
    
    if sale:
        data = sale[product_key]
        quantity = data.get('quantity', 0)
        selling_price = data.get('selling_price')
        raw_selling_price = request.POST.get('new_total_selling_price') 
        new_total_selling_price = Decimal(raw_selling_price) / quantity if raw_selling_price else None
        
        if new_total_selling_price and new_total_selling_price != Decimal(selling_price):
            sale[product_key]['selling_price'] = str(new_total_selling_price)
            messages.success(request, f"The revenue for {product.name} has been updated.")
    request.session['sale'] = sale
    request.session.modified = True
    
    page = request.GET.get('page', '')
    url = reverse('view-sale', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)

@login_required(login_url='login')
def edit_unsold_quantity(request, product_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=product_id)
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
    
    page = request.GET.get('page', '')
    url = reverse('view-sale', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)

@login_required(login_url='login')
@permission_required('delete') # dev
def delete_view_sale_quantity(request, product_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=product_id)
    product_key = str(product.id)
    
    if product_key in sale:
        del sale[product_key]
        messages.success(request, f"{product.name} has been removed from the sale.")
        
    request.session['sale'] = sale
    request.session.modified = True
    
    page = request.GET.get('page', '')
    url = reverse('view-sale', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)



    
    
    
    


    
