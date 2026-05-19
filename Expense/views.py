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

from Expense.models import PurchaseItem, Purchase, Employee, Waste, WasteItem, Expense, ExpenseItem, MiscExpense, Shift, ShiftEmployee
from Expense.forms import PurchaseForm, PurchaseItemForm, PurchaseFilterForm, EmployeeForm, ProductWasteForm, MaterialWasteForm, WasteItemFilterForm, ExpenseForm, ExpenseFilterForm, MiscExpenseForm, EmployeeFilterForm

from Supplier.models import Material
from Supplier.forms import MaterialForm

from Inventory.models import Stock
from Product.models import Product
from core.models import StatusModel

from Sales.models import Sale, SaleEmployee

from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from urllib.parse import urlencode
from django.views.decorators.http import require_POST

from django.core.paginator import Paginator

from django.db.models import Q, F, Value, CharField
from datetime import date, datetime
import calendar
from django.db.models import Sum, Avg, Max, OuterRef, Subquery

from user.models import User

from core.utils.owner import get_owner, permission_required, get_queryset_for_user, get_business_for_user

from django.contrib.messages import get_messages

# logging
import logging

# Create your views here.

logger = logging.getLogger('Expense')

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def purchase_history(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    purchases = get_queryset_for_user(request.user, Purchase.objects.all()).filter(business=business).order_by('-created_at')
    
    # forms
    form = PurchaseFilterForm(request.GET or None)
    
    # count, sum and purchased total cost.
    total_count = purchases.count()
    total_cost = purchases.purchase_total_cost()
    average_cost = purchases.average_total_cost()
    
    now = timezone.now()
    iso_year, iso_week, iso_weekday = now.isocalendar()
    today = now.day
    year = now.year
    month = now.month
    
    current_year = f"{year}-01"
    
    if form.is_valid():
        # search = form.cleaned_data.get('search')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')
        select_month = form.cleaned_data.get('select_month')
        period = form.cleaned_data.get('period')
        
        # if search:
        #     purchases = purchases.filter(
        #         Q(line_count__iexact=search) |
        #         Q(id__iexact=search) | 
        #         Q(materials__quantity__iexact=search) |
        #         Q(total_cost__icontains=search)
        #     ).distinct() # allows not to have duplicates after using search

        if select_month:
            parsed_year, parsed_month = map(int, select_month.split("-"))
            purchases = purchases.filter(purchase_date__month=parsed_month, purchase_date__year=parsed_year)
            
        """
        if you are using request.GET.get for getting the
        url DATE strings you need to convert it using
        strptime/strftime before you can extract the year,
        month, and day. The isocalendar() needs to be unpack 
        to get the year, number of weeks, and weekday. I
        intentionally made it to use form.cleaned_data and 
        request.GET.get() for learning purposes only.
        """
            
        if start_date and end_date:
            purchases = purchases.filter(purchase_date__range=(start_date, end_date))
            
        """
        This is for quick filter for today, this week, this month, and
        this year using the timezone.now().
        """
        
        last_year = iso_year - 1
        
        if period == 'last_week':
            """ Get the last week for last year using date. """
            
            last_week_of_last_year = date(last_year, 12, 28).isocalendar()[1]

            if iso_week == 1:
                purchases = purchases.filter(purchase_date__week=last_week_of_last_year, purchase_date__year=last_year)
            else:
                purchases = purchases.filter(purchase_date__week=iso_week - 1, purchase_date__year=year)
        
        else:
            # for mapping period
            period_map = {
                "today": {'purchase_date__day': today},
                "week": {"purchase_date__year": year, "purchase_date__week": iso_week},
                "month": {"purchase_date__month": month, "purchase_date__year": year},
            }
            filter_kwargs = period_map.get(period)
            if filter_kwargs:
                purchases = purchases.filter(**filter_kwargs)
            
        total_count = purchases.count()
        total_cost = purchases.purchase_total_cost()
        average_cost = purchases.average_total_cost()
        
    paginator = Paginator(purchases, 6)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    ytd_start = timezone.localdate().replace(month=1, day=1)
    ytd_spend = purchases.filter(purchase_date__gte=ytd_start).aggregate(total_cost=Sum('total_cost'))['total_cost'] or 0
    
    
    
    context = {
        'page_obj': page_obj,      
        'total_count': total_count, 
        'total_cost': total_cost, 
        'average_cost': average_cost, 
        'ytd_spend': ytd_spend,
        'current_year': current_year,
        'section': 'purchase'
        }
    return render(request, 'Expense/purchase_history.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def purchase_detail(request, business_slug, purchase_id):
    business = get_business_for_user(request.user, business_slug)
    
    purchase = get_object_or_404(Purchase, business=business, id=purchase_id)
    purchase_items = purchase.materials.select_related('material')
    line_count = purchase_items.count()
    
    context = {'purchase': purchase, 'purchase_items': purchase_items, 'line_count': line_count}
    return render(request, 'Expense/purchase_detail.html', context)

"""clearing cart just in case there's a bug """
@login_required(login_url='login')
def clear_cart(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    request.session['cart'] = {}
    request.session.modified = True
    messages.success(request, "All items has been removed.")
    
    
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'core/partials/_cart_response.html', {
            'cart_count':     0,
            'cart_items':     0,
            'total':          Decimal('0'),
            'cart_url':       'view-cart',
            'icon':           'bi-file-text',
            'label':          'Purchase Record',
            'clear_sessions': 'clear-cart',
            'name':           'Materials',
            'total_name':     'cost',
            'type':           'purchase',
            'messages':       get_messages(request),
        })
    
    
    
    
    return redirect('material-list', business_slug=business.slug)

"""clearing cart just in case there's a bug """

@login_required(login_url='login')
@permission_required('add')
def add_to_cart(request, business_slug, id):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, business=business, id=id) 
    material_slug = material.slug
    
    material_key = str(material.id) 
    
    if material.quantity >= 1:
        
        if material_key in cart:
            if cart[material_key]['quantity'] < material.quantity:
                cart[material_key]['quantity'] += 1
                messages.success(request, f"{material.name}'s quantity has increased.")

            else:
                messages.warning(request, f"{material.name} - quantity limit reached.")
                
        else:
            # first time adding to the cart.
            cart[material_key] = {
                'supplier': material.supplier.name if material.supplier else 'No supplier',
                'id': material.id,
                'slug': material_slug,
                'name': material.name,
                'price': float(material.price),
                'quantity': 1,
                'discount': str(0),
            }
            messages.success(request, f"{material.name} added to purchase.")
    else:
         messages.warning(request, f"{material.name} -  quantity limit reached.")

    # save the session
    request.session['cart'] = cart
    request.session.modified = True
    
    # htmx 
    if request.headers.get('HX-Request') == 'true':
        # compute total for the overview partial
        total = sum(Decimal(str(item['price'])) * item['quantity']
                for item in cart.values()        
        )
        
        return render(request, 'core/partials/_cart_response.html', {
            'cart_count': sum(item['quantity'] for item in cart.values()),
            'cart_items': len(cart),
            'messages':   get_messages(request),
            'total': total,
            'cart_url': 'view-cart',
            'icon': 'bi-file-text',
            'label': 'Purchase Record',
            'clear_sessions': 'clear-cart',
            'name': 'Materials',
            'total_name': 'cost',
            'type': 'purchase',
        }) 
        
    # fallback if htmx didn't work
    """
    Query with parameters, this allows to add items in the
    purchase without resetting the pagination page.
    """
    query_params = {}
    if request.GET.get('page'):
        query_params['page'] = request.GET.get('page')
    if request.GET.get('search'):
        query_params['search'] = request.GET.get('search')
    if request.GET.get('category'):
        query_params['category'] = request.GET.get('category')
    
    url = reverse('material-list', kwargs={'business_slug': business.slug})
    if query_params:
        url += "?" + urlencode(query_params)
    
    # LOGGING: add to cart
    logger.debug(f"Current Session Cart: {request.session.get('cart')}")
        
    # return redirect('material-list')
    """
    request.META['QUERY_STRING'] is the raw query string sent by the browser.
    It is already URL-encoded (same format as urllib.parse.urlencode output),
    so it can be safely appended to redirects to preserve pagination and filters.
    """
    # return redirect(f"{reverse('material-list')}?{request.META.get('QUERY_STRING', '')}")

    return redirect(url)

@login_required(login_url='login')
@permission_required('view') # dev
def view_cart(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    subtotal = 0
    total_discount = 0
    cart_items = []

    for material_id, data in cart.items():
        material = get_object_or_404(Material, business=business, id=material_id)
        material_slug = material.slug
        str_discount = data.get('discount', 0)
        discount = Decimal(str_discount)
        quantity = data.get('quantity', 1)
        price = data.get('price')
        # computations

        item_total = Decimal(price) * quantity
        item_discount = item_total - discount
        total_discount += discount
        subtotal += item_total
        
        
        cart_items.append({
            'supplier': material.supplier.name if material.supplier else 'No supplier',
            'id': material_id,
            'slug': material_slug,
            'material': material.name,
            'quantity': quantity,
            'subtotal': subtotal,
            'price': price,
            'item_total': item_total,
            'discount': discount,
            'item_discount': item_discount,
        })
        
    total_after_discount = max(subtotal - total_discount, 0)
    
    # LOGGING: View Cart 
    logger.debug(f" View Cart Sessions: {request.session.get('cart')}")
    
    context = {'total_after_discount': total_after_discount, 'cart_items': cart_items, 'subtotal': subtotal, 'total_discount': total_discount, 'section': 'supplier'}
    return render(request, 'Expense/view_cart.html', context)


@login_required(login_url='login')
@permission_required('view') # dev
def view_cart_summary(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    subtotal = 0
    total_discount = 0
    cart_items = []
    
    for material_id, data in cart.items():
        material = get_object_or_404(Material, business=business, id=material_id)
        material_slug = material.slug
        str_discount = data.get('discount', 0)
        discount = Decimal(str_discount)
        quantity = data['quantity']
        price = data.get('price')
        
        # computations
        item_total = Decimal(price) * quantity
        item_discount = item_total - discount
        total_discount += discount
        subtotal += item_total
        
        cart_items.append({
            'supplier': material.supplier.name if material.supplier else 'No supplier',
            'id': material.id,
            'name': material.name,
            'slug': material_slug,
            'price': price,
            'item_total': item_total,
            'quantity': quantity,
            'discount': discount,
            'item_discount': item_discount,
        })
        
    # save the cart length in session
    request.session['lines'] = len(cart_items)
    request.session.modified = True
    
    total_after_discount = max(subtotal - total_discount, 0)
    
    # LOGGING: View Cart Summary
    logger.debug(f"View Summary Cart Sessions: {request.session.get('cart')}")
    
    context = {'subtotal': subtotal, 'total_after_discount': total_after_discount, 'cart_items': cart_items, 'total_discount': total_discount}
    return render(request, 'Expense/view_cart_summary.html', context)

@login_required(login_url='login')
@permission_required('update')
def confirm_purchase_summary(request, business_slug):
    cart = request.session.get('cart', {})
    lines = request.session.get('lines', 0)
    subtotal = 0
    total_discount = 0
    
    business = get_business_for_user(request.user, business_slug)
    
    try: 
        with transaction.atomic():
            status, created = StatusModel.objects.get_or_create(name='paid') # cash payment directly so automatically paid
            purchase = Purchase.objects.create(
                user=business.user, 
                business=business, 
                total_cost=0, 
                status=status, 
                created_by=request.user
            )

            for material_id, data in cart.items():
                material = get_object_or_404(Material, business=business, id=material_id)
                str_discount = data.get('discount', 0)
                discount = Decimal(str_discount)
                quantity = data['quantity']
                price = data.get('price')
                
                # computations
                item_total = Decimal(price) * quantity

                total_discount += discount
                subtotal += item_total
                
                if material.quantity < quantity:
                    messages.warning(request, f"{material.name} - quantity limit reached.")
                
                PurchaseItem.objects.create(
                    purchase=purchase,
                    material=material,
                    discount=discount,
                    quantity=quantity,
                    price=price,
                )
                
                """
                1st purchase
                coke 5 qty
                formula = PHP 5.00 * 5 qty - 5.00(discount) = PHP 20.00
                
                previous quantity = 5 qty
                previous price = PHP 20.00
                
                2nd purchase
                coke 20 qty
                formula = PHP 5.00 * 20 qty = PHP 100.00 - 5.00(discount) = PHP 95.00
                
                total_quantity = 20(previous quantity) + 5 qty = 25 qty
                
                new stock price = PHP 20(previous price) + PHP 95 = PHP 115 / 25 qty = PHP 4.60
                
                3rd purchase 
                coke 15 qty
                formula PHP 5.00 * 15 qty = PHP 75.00
                
                total_quantity = 25.00(previous quantity) + 15 qty = 40 qty
                
                new_stock_price 115.00(previous price) + PHP 75.00 = PHP 180.00 / 40 qty = PHP 4.50
                """
                
                actual_unit_cost = (Decimal(price) * quantity) - discount
                
                MULTI_UNIT_TYPES = ('pack', 'box', 'tray', 'dozen', 'bundle', 'carton', 'sachet')
                
                if material.unit in MULTI_UNIT_TYPES:
                    actual_unit_cost = Decimal(price * quantity) - discount
                    quantity = quantity * material.piece_per_unit
                
                
                stock, created = Stock.objects.get_or_create(
                    user=business.user,
                    business=business,
                    material=material,
                    defaults={
                        'quantity': quantity,
                        'price': actual_unit_cost / quantity,
                        'created_by': request.user,
                    }         
                )
                
                if not created:
                    old_price = stock.price
                    old_quantity = stock.quantity
                    total_quantity = old_quantity + quantity
                    stock.quantity = total_quantity
                    stock.price = ((old_price * old_quantity) + actual_unit_cost) / total_quantity
                    stock.save()
                

                product, created = Product.objects.get_or_create(
                    user=business.user,
                    business=business,
                    name=material.name,
                    material=material,
                    defaults={
                        'cost_price': actual_unit_cost / quantity,
                        'selling_price': 0.00,
                        'prepared_quantity': quantity,
                        'created_by': request.user,
                    }
                )
                
                if not created:
                    previous_qty = product.prepared_quantity
                    previous_price = product.cost_price
                    total_quantity = previous_qty + quantity
                    
                    product.prepared_quantity = total_quantity
                    product.cost_price = ((previous_price * previous_qty) + actual_unit_cost) / total_quantity
                    product.save()
                    
            # check if there's a discount
            total_after_discount = max(subtotal - total_discount, 0)

            # save purchase lines - cart length
            purchase.line_count = lines

            # save the purchase object
            purchase.total_cost = total_after_discount
            purchase.save()

                
    except ValidationError:
        messages.error(request, f"Cannot complete the purchase - Insufficient stock.")
        return redirect('material-list')
        
    # save the purchase ID for ref
    request.session['purchase_id'] = purchase.id
    
    # clear the session
    request.session['cart'] = {}
    request.session.modified = True

    return redirect('view-purchase-summary', business_slug=business.slug, purchase_id=purchase.id)

@login_required(login_url='login')
def view_purchase_summary(request, business_slug, purchase_id):
    business = get_business_for_user(request.user, business_slug)
    purchase = get_object_or_404(Purchase, business=business, id=purchase_id)
    purchase_items = purchase.materials.select_related('material')
    
    total_discount = 0
    subtotal = 0
    cart_items = []
    for item in purchase_items:
        item_total = item.price * item.quantity
        quantity = item.quantity
        
        # handling discount items
        discount = item.discount
        item_discount = item_total - discount
        total_discount += discount
        
        subtotal += item_total
        
        cart_items.append({
            'supplier': item.supplier,
            'name': item.name,
            'price': item.price,
            'quantity': quantity,
            'item_total': item_total,
            'discount': discount,
            'item_discount': item_discount,

        })

    context = {'cart_items': cart_items, 'subtotal': subtotal, 'total_cost': purchase.total_cost, 'total_discount': total_discount, 'purchase': purchase}
    return render(request, 'Expense/view_purchase_summary.html', context)

@login_required(login_url='login')
def cart_remove_materials(request, business_slug, id):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, business=business, id=id)
    
    material_key = str(material.id)
    
    if material_key in cart:
        del cart[material_key]
        messages.success(request, f"{material.name} removed from the purchase record.")
         
    request.session.modified = True
    return redirect('view-cart', business_slug=business.slug)

@login_required(login_url='login')
def edit_total_price(request, business_slug, material_id):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, business=business, id=material_id)
    material_key = str(material.id)
    
    if cart:
        data = cart[material_key]
        quantity = data.get('quantity', 0)
        price = data.get('price')
        raw_price = request.POST.get('new_total_price') 
        new_total_price = Decimal(raw_price) / quantity if raw_price else None
        
        if new_total_price and new_total_price != Decimal(price):
            cart[material_key]['price'] = str(new_total_price)
            messages.success(request, f"The unit cost has been updated.")
            
    request.session['cart'] = cart
    request.session.modified = True
    
    return redirect('view-cart', business_slug=business.slug)

@login_required(login_url='login')
def cart_edit_material(request, business_slug, id):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, business=business, id=id)

    material_key = str(material.id)
    
    if request.method == 'POST':
        raw_qty = request.POST.get('quantity')
        quantity = int(raw_qty) if raw_qty else 0
        
        if material.quantity >= quantity:
        
            if quantity < 1:
                quantity = 1
            
            cart[material_key]['quantity'] = quantity
            messages.success(request, f"{material.name}'s quantity has been updated.")
            request.session.modified = True
        else:
             messages.warning(request, f"{material.name} - quantity limit reached.")
    
    return redirect('view-cart', business_slug=business.slug)

@login_required(login_url='login')
def cart_discount_material(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    
    for material_id, data in cart.items():
        raw_discount = request.POST.get(f"discount_{material_id}")
        discount_input = Decimal(raw_discount) if raw_discount else 0
        cart[material_id]['discount'] = str(discount_input)
    
    request.session['cart'] = cart
    request.session.modified = True
    
    return redirect('view-cart', business_slug=business.slug)

# @login_required(login_url='login')  
# @permission_required('owner_only')
# def employee_create(request):
#     page = 'employee'
#     business = get_business_for_user(request.user, business_slug)
#     if request.method == 'POST':
#         form = EmployeeForm(request.POST, business=business)
#         if form.is_valid():
#             obj = form.save(commit=False)
#             obj.user = request.user
#             obj.save()
            
#             messages.success(request, f"{obj.name}'s details has successfully created.")
#             return redirect('employee-list')
#         else:
#             print(form.errors)
#     else:
#         form = EmployeeForm(business=business)

#     context = {'form': form, 'section': 'employee'}
#     return render(request, 'Expense/employee_create.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def employee_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    employees = get_queryset_for_user(request.user, Employee.objects.all()).filter(business=business).order_by('name')
    
    form = EmployeeFilterForm(request.GET or None)
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        
        if search:
            employees = employees.filter(name__icontains=search)
            
    avg_daily_rate = employees.average_daily_rate()
    total_daily_rate = employees.total_daily_rate()
    monthly_payroll_est = total_daily_rate * 30
    
    pagination = Paginator(employees, 5)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)

    context = {
        'page_obj': page_obj, 
        'total_daily_rate': total_daily_rate,
        'avg_daily_rate': avg_daily_rate,
        'monthly_payroll_est': monthly_payroll_est,
        'section': 'employee'
        }
    return render(request, 'Expense/employee_list.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def employee_detail(request, business_slug, employee_id, slug):
    business = get_business_for_user(request.user, business_slug)
    employee = get_object_or_404(Employee, business=business, id=employee_id, slug=slug)
    
    monthly_rate = employee.daily_rate * 30
    
    context = {'employee': employee, 'monthly_rate': monthly_rate, 'section': 'employee'}
    
    return render(request, 'Expense/employee_detail.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def employee_update(request, business_slug, employee_id, slug):
    business = get_business_for_user(request.user, business_slug)
    employee = get_object_or_404(Employee, business=business, id=employee_id, slug=slug)
    
    if request.method == 'POST':
        form = EmployeeForm(request.POST, instance=employee)
        
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save()
            messages.success(request, f"{obj.name}'s daily rate has been updated.")
            return redirect('employee-list', business_slug=business.slug)
        else:
            print(form.errors)
    else:
        form = EmployeeForm(instance=employee)
        
    context = {'form': form, 'employee': employee, 'section': 'employee'}
    return render(request, 'Expense/employee_update.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
@permission_required('read_only') # dev
def employee_delete(request, business_slug, employee_id, slug):
    business = get_business_for_user(request.user, business_slug)
    employee = get_object_or_404(Employee, business=business, id=employee_id, slug=slug)
    
    print(employee.user)
    print(employee.staff_user)
    
    if request.method == 'POST':
        staff = employee.staff_user # save reference FIRST
        staff.is_active = False
        staff.save()

        employee.delete() # then delete the employee record
        
        messages.success(request, f"{employee.name} - has been deleted from employee record.")
        return redirect('employee-list', business_slug=business.slug)
    context = {'employee': employee, 'section': 'employee'}
    return render(request, 'Expense/employee_delete.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def shift_log_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    amount = 0
    if request.method == 'POST':
        selected_employee_ids = request.POST.getlist('selected_ids', [])
        date = request.POST.get('date')
        
        if not selected_employee_ids:
            messages.warning(request, 'Please select atleast one employee.')
        else:
            
            try:
                shift = Shift.objects.create(
                    user=business.user,
                    business=business,
                    amount=0,
                    date=date,
                    created_by=request.user,
                )
                
                from datetime import date as date_type
                emp_date = date_type.fromisoformat(date)
                
                if emp_date > date_type.today():
                    messages.error(request, 'Expense date cannot be in the future.')
                    employees = Employee.objects.filter(business=business)
                    return render(request, 'Expense/shift_log_create.html',{
                        'employees': employees,
                        'section': 'expense',
                    })
            except (ValueError, TypeError):
                messages.error(request, 'Invalid date. Please select a valid date.')
                employees = Employee.objects.filter(business=business)
                return render(request, 'Expense/shift_log_create.html', {
                    'employees': employees,
                    'section': 'expense',
                })
                
            for employee_id in selected_employee_ids:
                employee = get_object_or_404(Employee, business=business, id=employee_id)
                daily_rate = request.POST.get(f"daily_rate_{employee.id}")
                
                if not daily_rate:
                    daily_rate = employee.daily_rate
                
                amount += Decimal(daily_rate)
                
                ShiftEmployee.objects.create(
                    employee=employee,
                    shift=shift,
                    name=employee.name,
                    daily_rate=Decimal(daily_rate),
                    
                )
                
            shift.amount = amount
            shift.save()
            messages.success(request, f"Today's shift has been recorded. Please check the expense record.")
            return redirect('expense-list', business_slug=business.slug)
                
    employees = Employee.objects.filter(business=business)
    
    context = {'employees': employees, 'section': 'employees'}
    return render(request, 'Expense/shift_log_create.html', context)
            


@login_required(login_url='login')
@permission_required('read_only') # dev
def waste_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    stocks = get_queryset_for_user(request.user, Stock.objects.all()).filter(business=business).order_by('-created_at')
    wastes = get_queryset_for_user(request.user, Waste.objects.all()).filter(business=business).order_by('-date')
    
    total_waste_cost = wastes.aggregate(waste_cost=Sum(F('waste_items__price') * F('waste_items__quantity')))['waste_cost'] or 0
    max_waste = wastes.aggregate(max=Max('total_cost'))['max'] or 0 
    
    form = WasteItemFilterForm(request.GET or None)
    period = request.GET.get('period')
    
    now = timezone.now()
    current_year = f"{now.year}-01"
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        select_month = form.cleaned_data.get('select_month')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')
        
        if search:
            wastes = wastes.filter(
                Q(waste_items__material__name__iexact=search) |
                Q(total_cost__iexact=search) |
                Q(waste_items__quantity__iexact=search)
            )
        
        if select_month:
            parsed_date = datetime.strptime(select_month, '%Y-%m')
            wastes = wastes.filter(date__month=parsed_date.month)
        
        if start_date and end_date:
            wastes = wastes.filter(date__range=(start_date, end_date))
            
        
        if period == 'last_week':
            last_year = now.year - 1
            last_year_of_last_week = date(last_year, 12, 28).isocalendar()[1]
            
            if now.isocalendar()[1] == 1:
                wastes = wastes.filter(date__week=last_year_of_last_week, date__year=last_year)
            else:
                wastes = wastes.filter(date__week=now.isocalendar()[1]-1, date__year=now.year)
                
        if period == 'month':
            wastes = wastes.filter(date__month=now.month, date__year=now.year)
            
        if period == 'today':
            wastes = wastes.filter(date__day=now.day, date__year=now.year)
        
        total_waste_cost = wastes.total_waste_cost()
        
    pagination = Paginator(wastes, 6)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {
        'page_obj': page_obj, 
        'total_waste_cost': total_waste_cost, 
        'stocks': stocks, 
        'max_waste': max_waste,
        'current_year': current_year,
        'section': 'waste'
        }
    return render(request, 'Expense/waste_list.html', context)

@login_required(login_url='login')
@permission_required('add') # dev
def waste_product_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    page = 'waste_product' 
    if request.method == 'POST':
        form = ProductWasteForm(request.POST, business=business)

        if form.is_valid():
            item = form.save(commit=False)
            item.user = business.user
            item.business = business
            item.product.prepared_quantity -= item.quantity
            item.save()
            messages.success(request, f"{item.product.name} - has been added to expense.")
            return redirect('expense-waste-list', business_slug=business.slug)         
    else:
        form = ProductWasteForm(business=business)

    context = {'form': form, 'page': page, 'section': 'waste'}
    return render(request, 'Expense/waste_create.html', context)

@login_required(login_url='login')
@permission_required('add') # dev
def waste_material_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    page = 'waste_material'
    total_cost = 0
    
    if request.method == 'POST':
        selected_ids = request.POST.getlist('waste_expense', [])
        print(selected_ids)
        
        if not selected_ids:
            messages.warning(request, f"You forgot to check the checkbox.")   
        
        else:  
            waste = Waste.objects.create(
                user=business.user,
                business=business,
                total_cost=0,
                created_by=request.user,
                
            )
            invalid_items = []
            stocks = Stock.objects.filter(id__in=selected_ids, business=business)
            for stock in stocks:
                price = stock.price
                raw_quantity = request.POST.get(f"quantity_{stock.id}")
                quantity = int(raw_quantity)
                
                if quantity == 0:
                    pass
                
                # deduct from the stock
                if stock:
                    if stock.quantity >= quantity:
                        stock.quantity -= quantity
                        stock.save()
                    else:
                        invalid_items.append(f"{stock.name} - {stock.quantity} left.")
                        continue
                        
                # deduct as well for the product
                product = Product.objects.filter(business=business, material=stock.material).first()
                if product:
                    if product.prepared_quantity >= quantity:
                        product.prepared_quantity -= quantity
                        product.save()
                    else:
                        pass
                
                waste_items = WasteItem.objects.create(
                    waste=waste,
                    material=stock.material,
                    price=price,
                    quantity=quantity,
                )
                total_cost += Decimal(price) * quantity
                
            waste.total_cost = total_cost
            waste.save()
            
            # delete the waste ID if total cost is 0
            if total_cost == 0:
                waste.delete()
                
                if invalid_items:
                    messages.error(request, f"All items were invalid: {', '.join(invalid_items)}")
                else:
                
                    messages.error(request, "No valid items were processed.")
                return redirect('expense-waste-list', business_slug=business.slug)
            
            if invalid_items:
                messages.warning(request, f"Some items were skipped: {', '.join(invalid_items)}")
            
            else:
                messages.success(request, f"Waste has been created.")
            return redirect('expense-waste-list', business_slug=business.slug)         

    stocks = Stock.objects.filter(business=business)

    context = {'page': page, 'section': 'waste', 'stocks': stocks}
    return render(request, 'Expense/waste_create.html', context)

@login_required(login_url='login')
@permission_required('view') # dev
def waste_material_detail(request, business_slug, waste_id):
    business = get_business_for_user(request.user, business_slug)
        
    waste = get_object_or_404(Waste, business=business, id=waste_id)
    waste_items = waste.waste_items.select_related('material')
    
    context = {'waste': waste, 'section': 'waste', 'waste_items': waste_items}
    return render(request, 'Expense/waste_detail.html', context)


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def expense_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    total_amount = 0
    
    if request.method == 'POST':
        selected_ids = request.POST.getlist('misc_expense', [])
        
        if not selected_ids:
            messages.warning(request, f"You forgot to check the checkbox.")
        else:
            date_str = request.POST.get('date')
            
            # validate date
            try:
                from datetime import date as date_type
                expense_date = date_type.fromisoformat(date_str)
                
                expense = Expense.objects.create(
                    total_amount=0,
                    user=business.user,
                    business=business,
                    created_by=request.user,
                    date=timezone.now().date(),
                )
                
                if expense_date > date_type.today():
                    messages.error(request, 'Expense date cannot be in the future.')
                    misc_expense = MiscExpense.objects.filter(business=business)
                    return render(request, 'Expense/misc_and_expense_create.html',{
                        'section': 'expense',
                        'misc_expenses': misc_expense,
                    })
            except (ValueError, TypeError):
                messages.error(request, 'Invalid date. Please select a valid date.')
                misc_expenses = MiscExpense.objects.filter(business=business)
                return render(request, 'Expense/misc_and_expense_create.html', {
                    'section': 'expense',
                    'misc_expenses': misc_expenses,
                })
                
            for misc_id in selected_ids:
                misc = get_object_or_404(MiscExpense, business=business, id=misc_id)
                amount = request.POST.get(f"amount_{misc_id}")
                date = request.POST.get('date')
                total_amount += Decimal(amount)
                
                ExpenseItem.objects.create(
                    expense=expense,
                    misc_expense=misc,
                    name=misc.name,
                    amount=amount,
                )
            
            expense.total_amount = total_amount
            expense.date = date
            expense.save()
                
            messages.success(request, 'Expense has been created.')
            return redirect('expense-list', business_slug=business.slug)
        
    misc_expenses = MiscExpense.objects.filter(business=business)
    
    context = {'section': 'expense', 'misc_expenses': misc_expenses}
    return render(request, 'Expense/misc_and_expense_create.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def expense_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    average_amount_cost = 0
    
    expenses = get_queryset_for_user(request.user, Expense.objects.all()).filter(business=business)
    shifts = get_queryset_for_user(request.user, Shift.objects.all()).filter(business=business)
    
    expense_by_dates = expenses.values('date').annotate(total_amount=Sum('total_amount')).order_by('-date')
    shift_by_dates = shifts.values('date').annotate(total_shift=Sum('amount')).order_by('-date')
    
    # Calculate average
    average_expense = expenses.values('date').aggregate(total_expenses=Avg('total_amount'))['total_expenses'] or 0
    average_salary = shifts.values('date').aggregate(total_shift=Avg('amount'))['total_shift'] or 0
    
    if expenses and shifts:
        average_amount_cost = (expenses.aggregate(expense=Sum('total_amount'))['expense'] + shifts.aggregate(shift=Sum('amount'))['shift']) / 2
    
    # Apply filters
    form = ExpenseFilterForm(request.GET or None)
    period = request.GET.get('period')
    now = timezone.now()
    
    month = now.month
    year = now.year
    current_year = f"{year}-01"
    

    if form.is_valid():
        select_month = form.cleaned_data.get('select_month')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')
        
        if start_date and end_date:
            expenses = expenses.filter(date__range=(start_date, end_date))
            shifts = shifts.filter(date__range=(start_date, end_date))
        
        if select_month:
            parsed_date = datetime.strptime(select_month, '%Y-%m')
            expenses = expenses.filter(date__month=parsed_date.month, date__year=parsed_date.year)
            shifts = shifts.filter(date__month=parsed_date.month, date__year=parsed_date.year)
            
        if period == 'last_month':
            if month == 1:
                last_month = 12
                last_year = year - 1
                expenses = expenses.filter(date__month=last_month, date__year=last_year)
                shifts = shifts.filter(date__month=last_month, date__year=last_year)
            else:
                expenses = expenses.filter(date__month=month-1, date__year=year)
                shifts = shifts.filter(date__month=month-1, date__year=year)
                
        if period == 'month':
            expenses = expenses.filter(date__month=month, date__year=year)
            shifts = shifts.filter(date__month=month, date__year=year)
        
        
        expense_by_dates = expenses.values('date').annotate(total_amount=Avg('total_amount')) 
        shift_by_dates = shifts.values('date').annotate(total_shift=Avg('amount'))
        
    # Build summary dict
    summary = {}
    
    for e in expense_by_dates:
        summary[e['date']] = {
            'total_amount': e['total_amount'],
            'total_shift': 0
        }
    
    for s in shift_by_dates:
        if s['date'] in summary:
            summary[s['date']]['total_shift'] = s['total_shift']
        else:
            summary[s['date']] = {
                'total_shift': s['total_shift'],
                'total_amount': 0,
            }

    # Convert summary dict to list and sort
    summary_list = []
    
    grand_total_expense = 0
    grand_total_salary = 0
    
    for date, value in summary.items():
        total_amount = value['total_amount']
        total_shift = value['total_shift']
        
        grand_total_expense += total_amount
        grand_total_salary += total_shift
        
        
        summary_list.append({
            'date': date,
            'total_amount': total_amount,
            'total_shift': total_shift,
            
            
        })
        
    # Calculate average
    average_expense = expenses.values('date').aggregate(total_expenses=Avg('total_amount'))['total_expenses'] or 0
    average_salary = shifts.values('date').aggregate(total_shift=Avg('amount'))['total_shift'] or 0
    
    sorted_list = sorted(summary_list, key=lambda x: x['date'], reverse=True)
    
    
    # Pagination
    pagination = Paginator(sorted_list, 8)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {
        
        'page_obj': page_obj, 
        'section': 'expense', 
        'current_year': current_year,
        'average_expense': average_expense,
        'average_salary': average_salary,
        'grand_total_salary': grand_total_salary,
        'grand_total_expense': grand_total_expense,
        'average_amount_cost': average_amount_cost,
        'form': form,
    }
    
    return render(request, 'Expense/expense_list.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def expense_detail(request, business_slug, date):
    business = get_business_for_user(request.user, business_slug)
    
    # Get all expenses and employees for this date
    expense = Expense.objects.filter(business=business, date=date)
    exp_items = ExpenseItem.objects.filter(expense__in=expense)
    
    shift = Shift.objects.filter(business=business, date=date)
    shift_employees = ShiftEmployee.objects.filter(shift__in=shift)
    
    # Calculate totals
    total_expense_cost = expense.aggregate(total=Sum('total_amount'))['total'] or 0
    total_salary_cost = shift_employees.aggregate(total=Sum('daily_rate'))['total'] or 0
    total_cost = total_expense_cost + total_salary_cost

    # Build expense items
    expense_items = []
    for exp in exp_items:
        expense_items.append({
            'type': 'expense',
            'name': exp.name,
            'category': exp.category,
            'amount': exp.amount,
        })
    
    # Build employee items
    employee_items = []
    for emp in shift_employees:
        employee_items.append({
            'type': 'salary',
            'name': emp.name,
            'daily_rate': emp.daily_rate,
        })
    
    context = {
        'date': date,
        'expense': expense,
        'shift': shift,
        'expense_items': expense_items,
        'employee_items': employee_items,
        'total_expense_cost': total_expense_cost,
        'total_salary_cost': total_salary_cost,
        'total_cost': total_cost,
        'expense_count': expense.count(),
        'employee_count': shift_employees.count(),
        'section': 'expense'
    }
    
    return render(request, 'Expense/expense_detail.html', context)

# @login_required(login_url='login')
# @permission_required('owner_only')
# def expense_detail(request, username, date):
#     if request.user.role == 'developer':
#         owner = get_object_or_404(User, username=username)
#     else:
#         business = get_business_for_user(request.user, business_slug)
        

#     expense = Expense.objects.filter(user=owner, date=date)
    
#     expense_items = ExpenseItem.objects.filter(expense__in=expense)
    
#     sale = Sale.objects.filter(user=owner, date=date)
#     sale_employees = SaleEmployee.objects.filter(sale__in=sale)
#     total_salary_cost = sale_employees.aggregate(s=Sum(F('daily_rate')))['s'] or 0
    
#     total_amount = 0
    
#     items = []
    
#     for expense in expense_items:
#         expense_name = expense.name
#         amount = expense.amount
#         total_amount += amount
        
#         items.append({
#             'expense_name': expense_name,
#             'amount': amount,
#         })
    
#     for employee in sale_employees:
#         employee_name = employee.name,
#         daily_rate = employee.daily_rate,
        
#         items.append({
#             'employee_name': employee_name,
#             'daily_rate': daily_rate,
            
#         })
    
#     context = {
#         'total_amount': total_amount,
#         'total_salary_cost': total_salary_cost,
#         'items': items, 
#         'expense': expense, 
#         'section': 'expense'
#         }
    
#     return render(request, 'Expense/expense_detail.html', context)


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def misc_expense_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    misc_expenses = get_queryset_for_user(request.user, MiscExpense.objects.all()).filter(business=business).order_by('-created_at')
    
    pagination = Paginator(misc_expenses, 5)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {'page_obj': page_obj, 'section': 'expense'}
    return render(request, 'Expense/misc_expense_list.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def misc_expense_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    if request.method == 'POST':
        form = MiscExpenseForm(request.POST, business=business)
        
        if form.is_valid():
            misc_expense = form.save(commit=False)
            misc_expense.user = business.user
            misc_expense.business = business
            misc_expense.created_by = request.user
            misc_expense.name = misc_expense.name.title()
            misc_expense.save()
            messages.success(request, f"{misc_expense.name} has been added to expense.")
            return redirect('misc-expense-list', business_slug=business.slug)
        
    else:
        form = MiscExpenseForm(business=business)
        
    context = {'form': form, 'section': 'expense'}
    return render(request, 'Expense/misc_and_expense_create.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def misc_expense_detail(request, business_slug, misc_expense_id):
    business = get_business_for_user(request.user, business_slug)
        
    misc_expense = get_object_or_404(MiscExpense, business=business, id=misc_expense_id)
    
    context = {'misc_expense': misc_expense, 'section': 'expense'}
    
    return render(request, 'Expense/misc_expense_detail.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def misc_expense_update(request, business_slug, misc_expense_id):
    business = get_business_for_user(request.user, business_slug)
        
    misc_expense = get_object_or_404(MiscExpense, business=business, id=misc_expense_id)
    
    if request.method == 'POST':
        form = MiscExpenseForm(request.POST, instance=misc_expense, business=business)
        
        if form.is_valid():
            misc_expense = form.save(commit=False)
            misc_expense.name = misc_expense.name.title()
            misc_expense.save()
            messages.success(request, f"{misc_expense.name} has been updated.")
            return redirect('misc-expense-list', business_slug=business.slug)
    
    else:
        form = MiscExpenseForm(instance=misc_expense, business=business)
    
    context = {'form': form, 'section': 'expense', 'misc_expense': misc_expense}
    return render(request, 'Expense/misc_expense_update.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
@permission_required('read_only') # dev
def misc_expense_delete(request, business_slug, misc_expense_id):
    business = get_business_for_user(request.user, business_slug)
        
    misc_expense = get_object_or_404(MiscExpense, business=business, id=misc_expense_id)
    
    if request.method == 'POST':
        misc_expense.delete()
        messages.success(request, f"{misc_expense.name} has been deleted.")
        return redirect('misc-expense-list', business_slug=business.slug)
    
    context = {'misc_expense': misc_expense, 'section': 'expense'}
    return render(request, 'Expense/misc_expense_delete.html', context)