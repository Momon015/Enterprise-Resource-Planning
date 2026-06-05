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

from Sales.models import (
    Sale, SaleItem, SaleEmployee, SalesPayment, SalesReturn, SalesReturnItem)
from Sales.forms import SaleForm, SaleFilterForm

from Product.models import Product
from Product.forms import ProductForm

from Expense.models import Employee
from Expense.forms import EmployeeForm

from Inventory.models import Stock
from Expense.models import Waste, WasteItem
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

# Map SalesReturn.reason → Waste.reason for damaged items
RETURN_TO_WASTE_REASON = {
    'defective': 'defective',
    'expired':   'expired',
    # everything else → 'damage' fallback
}

@login_required(login_url='login')
@permission_required('add')
def sales_return_create(request, business_slug, sale_id):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)
    sale_items = sale.sale_items.select_related('product').all()

    if request.method == 'POST':
        reason = request.POST.get('reason', 'other')
        reason_note = request.POST.get('reason_note', '').strip()
        refund_method = request.POST.get('refund_method', 'cash')

        items_to_return = []
        total_refund = Decimal('0')

        for si in sale_items:
            qty_str = request.POST.get(f'qty_{si.id}', '0')
            try:
                qty = int(qty_str)
            except ValueError:
                qty = 0
            if qty <= 0:
                continue
            
            if qty > si.returnable_quantity:
                messages.error(request,
                    f"{si.name}: only {si.returnable_quantity} returnable "
                    f"(already returned {si.total_returned_quantity} of {si.quantity}).")
                return redirect('purchase-return-create',
                                business_slug=business_slug, sale_id=sale_id)


            unit_refund_str = request.POST.get(f'unit_refund_{si.id}', str(si.price_at_sale))
            try:
                unit_refund = Decimal(unit_refund_str)
            except (ValueError, ArithmeticError):
                unit_refund = si.price_at_sale

            resellable = request.POST.get(f'resellable_{si.id}', 'true') == 'true'

            items_to_return.append({
                'sale_item': si,
                'qty': qty,
                'unit_refund': unit_refund,
                'resellable': resellable,
            })
            total_refund += unit_refund * qty

        if not items_to_return:
            messages.warning(request, "Pick at least one item to return.")
            return redirect('sales-return-create',
                            business_slug=business_slug, sale_id=sale_id)

        # In sales_return_create, after collecting items, before atomic block:
        already_refunded = sale.amount_refunded  # already a property on Sale
        max_refund = (sale.total_revenue or Decimal('0')) - already_refunded

        if total_refund > max_refund:
            messages.error(request,
                f"Refund ₱{total_refund:.2f} exceeds remaining refundable ₱{max_refund:.2f}.")
            return redirect('sales-return-create',
                            business_slug=business_slug, sale_id=sale_id)
        
        
        with transaction.atomic():
            return_obj = SalesReturn.objects.create(
                original_sale=sale,
                business=business,
                reason=reason,
                reason_note=reason_note,
                refund_total=total_refund,
                refund_method=refund_method,
                created_by=request.user,
            )

            damaged_items = []   # collect for single Waste record

            for item in items_to_return:
                si = item['sale_item']
                qty = item['qty']

                SalesReturnItem.objects.create(
                    sales_return=return_obj,
                    original_sale_item=si,
                    name=si.name,
                    quantity=qty,
                    unit_refund=item['unit_refund'],
                    resellable=item['resellable'],
                )

                if item['resellable']:
                    # Back to inventory: bump Stock + Product.prepared_quantity
                    if si.product and si.product.material:
                        try:
                            stock = Stock.objects.get(
                                business=business, material=si.product.material
                            )
                            stock.quantity += qty
                            stock.save()
                        except Stock.DoesNotExist:
                            pass

                    if si.product:
                        si.product.prepared_quantity += qty
                        si.product.save(update_fields=['prepared_quantity'])
                else:
                    # Mark for Waste
                    damaged_items.append({
                        'sale_item': si,
                        'qty': qty,
                    })

            # If any damaged items, create a Waste record bundling them
            if damaged_items:
                waste_reason = RETURN_TO_WASTE_REASON.get(reason, 'damage')
                total_waste_cost = sum(
                    (d['sale_item'].cost_price or Decimal('0')) * d['qty']
                    for d in damaged_items
                )

                waste = Waste.objects.create(
                    business=business,
                    user=business.user,
                    total_cost=total_waste_cost,
                    reason=waste_reason,
                    created_by=request.user,
                )

                for d in damaged_items:
                    si = d['sale_item']
                    WasteItem.objects.create(
                        waste=waste,
                        product=si.product,
                        price=si.cost_price or Decimal('0'),
                        quantity=d['qty'],
                        name=si.name,
                    )

                log_activity(
                    business, request.user, 'waste.recorded',
                    target=waste,
                    description=f"₱{total_waste_cost:.2f} — {waste.get_reason_display()} (from return {return_obj.reference})",
                    metadata={'reason': waste_reason, 'total': str(total_waste_cost),
                              'sales_return_id': return_obj.id},
                )

            log_activity(
                business, request.user, 'sale.refunded',
                target=return_obj,
                description=f"{return_obj.reference} — ₱{total_refund:.2f} refunded ({return_obj.get_refund_method_display()})",
                metadata={'reference': return_obj.reference,
                          'total': str(total_refund),
                          'reason': reason,
                          'refund_method': refund_method},
            )

        messages.success(request, f"Return {return_obj.reference} recorded.")
        return redirect('sale-summary', business_slug=business_slug, sale_id=sale_id)

    context = {
        'sale': sale,
        'sale_items': sale_items,
        'reason_choices': SalesReturn.REASON_CHOICES,
        'refund_method_choices': SalesReturn.REFUND_METHOD_CHOICES,
        'section': 'sale',
    }
    return render(request, 'Sales/sales_return_create.html', context)


@login_required(login_url='login')
def sales_return_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    returns = SalesReturn.objects.filter(business=business).select_related(
        'original_sale', 'created_by'
    ).order_by('-date', '-created_at')

    paginator = Paginator(returns, 10)
    page = request.GET.get('page')
    page_obj = paginator.get_page(page)

    return render(request, 'Sales/sales_return_list.html', {
        'page_obj': page_obj,
        'section': 'sale',
    })


@login_required(login_url='login')
def sales_return_detail(request, business_slug, return_id):
    business = get_business_for_user(request.user, business_slug)
    return_obj = get_object_or_404(SalesReturn, business=business, id=return_id)
    items = return_obj.items.select_related('original_sale_item').all()

    return render(request, 'Sales/sales_return_detail.html', {
        'return_obj': return_obj,
        'items': items,
        'section': 'sale',
    })
                
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
            
            # ── Payment capture ─────────────────────────────────
            payment_status = request.POST.get('payment_status', 'full')
            payment_method = request.POST.get('payment_method', 'cash')
            payment_note = request.POST.get('payment_note', '').strip()
            
            if payment_status == 'full':
                payment_amount = sale_obj.total_revenue
            elif payment_status == 'partial':
                amount_str = request.POST.get('amount_paid', '0').strip()
                try:
                    payment_amount = Decimal(amount_str)
                except (ValueError, ArithmeticError):
                    payment_amount = Decimal('0')
                    
                if payment_amount <= 0:
                    messages.warning(request, "Partial amount was invalid - recorded as utang/debt instead.")
                    payment_amount = Decimal('0')
                elif payment_amount >= sale_obj.total_revenue:
                    payment_amount = sale_obj.total_revenue
                    messages.info(request, "Amount matched total - recorded as paid in full")
            # utang / debt
            else:
                payment_amount = Decimal('0')
                
            if payment_amount > 0:
                SalesPayment.objects.create(
                    sale=sale_obj,
                    business=business,
                    amount=payment_amount,
                    method=payment_method,
                    note=payment_note,
                    created_by=request.user,
                )
                
            payment_label = (
                "fully paid" if payment_status == 'full'
                else f"partial ₱{payment_amount:.2f}" if payment_status == 'partial'
                else "utang"
            )
            log_activity(
                business, request.user, 'sale.completed',
                target=sale_obj,
                description=f"{sale_obj.reference}: {sale_obj.quantity_item()} item(s) — ₱{sale_obj.total_revenue:.2f} ({payment_label})",
                metadata={
                    'reference': sale_obj.reference,
                    'total': str(sale_obj.total_revenue),
                    'line_count': sale_obj.line_count,
                    'payment_status': payment_status,
                    'payment_method': payment_method if payment_status != 'utang' else None,
                },
            )

    except ValidationError:
        messages.error(request, f"Cannot complete the sale - Insufficient stock.")
        return redirect('view-sale', business_slug=business.slug)  # exits early if error occurs
    
    
    for key in ('total_salary_cost', 'line_count'):
        request.session.pop(key, 0)
    
    request.session['sale'] = {}
    request.session.modified = True
    # request.session['selected_employee_ids'] = []
    
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

@login_required(login_url='login')
@permission_required('add')
def add_sales_payment(request, business_slug, sale_id):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)

    if request.method == 'POST':
        amount_str = request.POST.get('amount', '').strip()
        method = request.POST.get('method', 'cash')
        note = request.POST.get('note', '').strip()

        try:
            amount = Decimal(amount_str)
        except (ValueError, ArithmeticError):
            messages.error(request, "Enter a valid amount.")
            return redirect('add-sales-payment',
                            business_slug=business_slug, sale_id=sale_id)

        if amount <= 0:
            messages.error(request, "Payment amount must be greater than ₱0.")
            return redirect('add-sales-payment',
                            business_slug=business_slug, sale_id=sale_id)

        outstanding_before = sale.outstanding
        if amount > outstanding_before:
            messages.warning(
                request,
                f"Payment ₱{amount:.2f} exceeds outstanding ₱{outstanding_before:.2f}. "
                f"Outstanding will go negative (store credit to customer)."
            )

        with transaction.atomic():
            payment = SalesPayment.objects.create(
                sale=sale,
                business=business,
                amount=amount,
                method=method,
                note=note,
                created_by=request.user,
            )

            log_activity(
                business, request.user, 'sale.paid',
                target=payment,
                description=f"₱{amount:.2f} paid toward {sale.reference} ({payment.get_method_display()})",
                metadata={
                    'sale_reference': sale.reference,
                    'amount': str(amount),
                    'method': method,
                    'note': note,
                },
            )

        messages.success(request, f"Payment of ₱{amount:.2f} recorded.")
        return redirect('sale-summary', business_slug=business_slug, sale_id=sale_id)

    context = {
        'sale': sale,
        'outstanding': sale.outstanding,
        'method_choices': SalesPayment.PAYMENT_METHOD_CHOICES,
        'section': 'sale',
    }
    return render(request, 'Sales/add_sales_payment.html', context)

@login_required(login_url='login')
@permission_required('staff_view')   # owner-only
def sales_receivables(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    sales = (
        Sale.objects.filter(business=business)
        .prefetch_related('payments', 'returns')
        .order_by('-date')
    )

    outstanding_sales = [s for s in sales if s.outstanding > 0]
    total_outstanding = sum((s.outstanding for s in outstanding_sales), Decimal('0'))

    paginator = Paginator(outstanding_sales, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'total_outstanding': total_outstanding,
        'section': 'sale',
    }
    return render(request, 'Sales/sales_receivables.html', context)

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



    
    
    
    


    
