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

from Expense.models import PurchaseItem, Purchase, Employee, WasteItem
from Expense.forms import PurchaseForm, PurchaseItemForm, PurchaseFilterForm, EmployeeForm, ProductWasteForm, MaterialWasteForm, WasteItemFilterForm

from Supplier.models import Material
from Supplier.forms import MaterialForm

from Inventory.models import Stock
from Product.models import Product
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
# logging
import logging

# Create your views here.

logger = logging.getLogger('Expense')

def purchase_history(request):
    purchases = Purchase.objects.all().order_by('-created_at')
    
    # forms
    form = PurchaseFilterForm(request.GET or None)
    
    # count, sum and purchased total cost.
    total_count = purchases.count()
    total_cost = purchases.purchase_total_cost()
    average_cost = purchases.average_total_cost()
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')
        select_month = form.cleaned_data.get('select_month')
        period = form.cleaned_data.get('period')
        
        if search:
            purchases = purchases.filter(
                Q(line_count__iexact=search) |
                Q(id__iexact=search) | 
                Q(materials__quantity__iexact=search) |
                Q(total_cost__icontains=search)
            )

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
        
        now = timezone.now()
        iso_year, iso_week, iso_weekday = now.isocalendar()
        today = now.day
        year = now.year
        month = now.month
        
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
                "last_year": {'purchase_date__year': last_year},
                "today": {'purchase_date__day': today},
                "week": {"purchase_date__year": year, "purchase_date__week": iso_week},
                "month": {"purchase_date__month": month, "purchase_date__year": year},
                "year": {"purchase_date__year": year},
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
    
    context = {'page_obj': purchases, 'page_obj': page_obj, 'total_count': total_count, 'total_cost': total_cost, 'average_cost': average_cost, 'section': 'purchase'}
    return render(request, 'Expense/purchase_history.html', context)

@login_required(login_url='login')
def purchase_detail(request, purchase_id):
    purchase = get_object_or_404(Purchase, id=purchase_id)
    purchase_items = purchase.materials.select_related('material')
    line_count = purchase_items.count()
    
    context = {'purchase': purchase, 'purchase_items': purchase_items, 'line_count': line_count}
    return render(request, 'Expense/purchase_detail.html', context)

"""clearing cart just in case there's a bug """
@login_required(login_url='login')
def clear_cart(request):
    request.session['cart'] = {}
    request.session.modified = True
    messages.success(request, "All items has been removed.")
    return redirect('material-list')

"""clearing cart just in case there's a bug """

@login_required(login_url='login')
def add_to_cart(request, id):
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, id=id) 
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
    
    url = reverse('material-list')
    if query_params:
        url += "?" + urlencode(query_params)
    
    # LOGGING: add to cart
    logger.debug(f"Current Session Cart: {request.session.get('cart')}")
    
    # save the session
    request.session['cart'] = cart
    request.session.modified = True
    
    # return redirect('material-list')
    """
    request.META['QUERY_STRING'] is the raw query string sent by the browser.
    It is already URL-encoded (same format as urllib.parse.urlencode output),
    so it can be safely appended to redirects to preserve pagination and filters.
    """
    # return redirect(f"{reverse('material-list')}?{request.META.get('QUERY_STRING', '')}")

    return redirect(url)

@login_required(login_url='login')
def view_cart(request):
    cart = request.session.get('cart', {})
    subtotal = 0
    total_discount = 0
    cart_items = []

    for material_id, data in cart.items():
        material = get_object_or_404(Material, id=material_id)
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
def view_cart_summary(request):
    cart = request.session.get('cart', {})
    subtotal = 0
    total_discount = 0
    cart_items = []
    
    for material_id, data in cart.items():
        material = get_object_or_404(Material, id=material_id)
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
def confirm_purchase_summary(request):
    cart = request.session.get('cart', {})
    lines = request.session.get('lines', 0)
    subtotal = 0
    total_discount = 0
    
    try: 
        with transaction.atomic():
            status, created = StatusModel.objects.get_or_create(name='paid') # cash payment directly so automatically paid
            purchase = Purchase.objects.create(total_cost=0, status=status, user=request.user)

            for material_id, data in cart.items():
                material = get_object_or_404(Material, id=material_id)
                str_discount = data.get('discount', 0)
                discount = Decimal(str_discount)
                quantity = data['quantity']
                price = data.get('price')
                print('price', price)
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
                print('actual_unit_cost', actual_unit_cost)
                stock, created = Stock.objects.get_or_create(
                    user=request.user,
                    material=material,
                    defaults={
                        'quantity': quantity,
                        'price': actual_unit_cost / quantity
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
                    user=request.user,
                    material=material,
                    name=material.name,
                    defaults={
                        'cost_price': actual_unit_cost / quantity,
                        'selling_price': 0.00,
                        'prepared_quantity': quantity
                    }
                )
                
                if not created:
                    previous_qty = product.prepared_quantity
                    previous_price = product.cost_price
                    total_quantity = previous_qty + quantity
                    
                    product.prepared_quantity = total_quantity
                    product.cost_price = ((previous_price * previous_qty) + actual_unit_cost) / total_quantity
                    product.save()

                
    except ValidationError:
        messages.error(request, f"Cannot complete the purchase - Insufficient stock.")
        return redirect('material-list')
        
    # check if there's a discount
    total_after_discount = max(subtotal - total_discount, 0)
    
    # save purchase lines - cart length
    purchase.line_count = lines
    
    # save the purchase object
    purchase.total_cost = total_after_discount
    purchase.save()
    
    # save the purchase ID for ref
    request.session['purchase_id'] = purchase.id
    
    # clear the session
    request.session['cart'] = {}
    request.session.modified = True

    return redirect('view-purchase-summary', purchase.id)

@login_required(login_url='login')
def view_purchase_summary(request, purchase_id):
    purchase = get_object_or_404(Purchase, id=purchase_id, user=request.user)
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
            'name': item.material.name,
            'price': item.price,
            'quantity': quantity,
            'item_total': item_total,
            'discount': discount,
            'item_discount': item_discount,

        })

    context = {'cart_items': cart_items, 'subtotal': subtotal, 'total_cost': purchase.total_cost, 'total_discount': total_discount, 'purchase': purchase}
    return render(request, 'Expense/view_purchase_summary.html', context)

@login_required(login_url='login')
def cart_remove_materials(request, id):
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, id=id)
    
    material_key = str(material.id)
    
    if material_key in cart:
        del cart[material_key]
        messages.success(request, f"{material.name} removed from the purchase record.")
         
    request.session.modified = True
    return redirect('view-cart')

@login_required(login_url='login')
def edit_total_price(request, material_id):
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, id=material_id)
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
    
    return redirect('view-cart')

@login_required(login_url='login')
def cart_edit_material(request, id):
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, id=id)

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
             messages.warning(request, f"{material.name}: quantity limit reached.")
    
    return redirect('view-cart')

@login_required(login_url='login')
def cart_discount_material(request):
    cart = request.session.get('cart', {})
    
    for material_id, data in cart.items():

        raw_discount = request.POST.get(f"discount_{material_id}")
        discount_input = Decimal(raw_discount) if raw_discount else 0
        cart[material_id]['discount'] = str(discount_input)
    
    request.session['cart'] = cart
    request.session.modified = True
    
    return redirect('view-cart')

@login_required(login_url='login')  
def employee_create(request):
    page = 'employee'
    
    if request.method == 'POST':
        form = EmployeeForm(request.POST)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.save()
            
            messages.success(request, f"{obj.name}'s details has successfully created.")
            return redirect('employee-list')
        else:
            print(form.errors)
    else:
        form = EmployeeForm()

    context = {'form': form, 'section': 'employee'}
    return render(request, 'Expense/employee_create.html', context)

@login_required(login_url='login')
def employee_list(request):
    employees = Employee.objects.all()

    context = {'employees': employees, 'section': 'employee', 'section': 'employee'}
    return render(request, 'Expense/employee_list.html', context)

@login_required(login_url='login')
def employee_detail(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)
    
    monthly_rate = employee.daily_rate * 30
    
    context = {'employee': employee, 'monthly_rate': monthly_rate, 'section': 'employee'}
    
    return render(request, 'Expense/employee_detail.html', context)

@login_required(login_url='login')
def employee_update(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)
    
    if request.method == 'POST':
        form = EmployeeForm(request.POST, instance=employee)
        
        if form.is_valid():
            obj = form.save(commit=False)
            obj.user = request.user
            obj.save()
            messages.success(request, f"{obj.name}'s details has been updated.")
            return redirect('employee-detail', employee.id)
        else:
            print(form.errors)
    else:
        form = EmployeeForm(instance=employee)
        
    context = {'form': form, 'employee': employee, 'section': 'employee'}
    return render(request, 'Expense/employee_update.html', context)

@login_required(login_url='login')
def employee_delete(request, employee_id):
    employee = get_object_or_404(Employee, id=employee_id)

    if request.method == 'POST':
        employee.delete()
        messages.success(request, f"{employee.name} - has been deleted from employee record.")
        return redirect('employee-list')
    context = {'employee': employee, 'section': 'employee'}
    return render(request, 'Expense/employee_delete.html', context)

@login_required(login_url='login')
def waste_list(request):
    wastes = WasteItem.objects.all().order_by('-date')

    total_waste_cost = wastes.total_waste_cost()
    total_product_waste = wastes.total_product_waste()
    total_material_waste = wastes.total_material_waste()
    
    form = WasteItemFilterForm(request.GET or None)
    period = request.GET.get('period')
    now = timezone.now()
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        select_month = form.cleaned_data.get('select_month')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')
        
        if search:
            wastes = wastes.filter(
                Q(material__name__iexact=search) |
                Q(price__iexact=search) |
                Q(quantity__iexact=search)
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
        total_product_waste = wastes.total_product_waste()
        total_material_waste = wastes.total_material_waste()

    pagination = Paginator(wastes, 6)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {'wastes': page_obj, 'page_obj': page_obj, 'section': 'waste', 'total_waste_cost': total_waste_cost, 'total_product_waste': total_product_waste, 'total_material_waste': total_material_waste}
    return render(request, 'Expense/waste_list.html', context)

@login_required(login_url='login')
def waste_product_create(request):
    page = 'waste_product' 
    if request.method == 'POST':
        form = ProductWasteForm(request.POST)

        if form.is_valid():
            item = form.save(commit=False)
            item.user = request.user
            item.product.prepared_quantity -= item.quantity
            item.save()
            messages.success(request, f"{item.product.name} - has been added to expense.")
            return redirect('expense-waste-list')         
    else:
        form = ProductWasteForm()

    context = {'form': form, 'page': page, 'section': 'waste'}
    return render(request, 'Expense/waste_create.html', context)

@login_required(login_url='login')
def waste_material_create(request):
    page = 'waste_material'
    if request.method == 'POST':
        form = MaterialWasteForm(request.POST)

        if form.is_valid():
            item = form.save(commit=False)
            item.user = request.user
            item.save()
            
            # deduct from the stock
            try:
                stock = Stock.objects.get(user=request.user, material=item.material)
                stock.quantity -= item.quantity
                stock.save()
            except Stock.DoesNotExist:
                pass

            # deduct as well for the product
            try:
                product = Product.objects.get(user=request.user, material=item.material)
                product.prepared_quantity -= item.quantity
                product.save()
            except Product.DoesNotExist:
                pass
            
                
            messages.success(request, f"{item.material.name} has been recorded as waste.")
            return redirect('expense-waste-list')         
    else:
        form = MaterialWasteForm()

    context = {'form': form, 'page': page, 'section': 'waste'}
    return render(request, 'Expense/waste_create.html', context)



