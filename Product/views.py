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

from django.core.paginator import Paginator

from core.models import Category
from Product.models import Product, ProductPreset, ProductPresetItem
from Product.forms import ProductForm, ProductFilterForm

from user.models import User

from decimal import Decimal
from django.db.models import Q, F
# Create your views here.

@login_required(login_url='login')
def product_list(request):
    form = ProductFilterForm(request.GET or None)
    products = Product.objects.all().order_by('name')
    
    """
    this allows to filter things without 
    causing any bugs like not showing anything 
    in template to ensure this always work
    """
    categories = form.fields['category'].queryset
    
    stock_filter = request.GET.get('stock')
    out_of_stock = products.filter(prepared_quantity=0).count()

    if form.is_valid():
        search = form.cleaned_data.get('search')
        category = form.cleaned_data.get('category')
        
        if search:

            products = products.filter(
                Q(name__icontains=search) | 
                Q(category__name__icontains=search) | 
                Q(description__icontains=search) |
                Q(category__category_type__icontains=search) 
                
            )
        if category:
            products = products.filter(category=category)
            
        if stock_filter == 'high':
            products = products.filter(prepared_quantity__gte=50)
            
        elif stock_filter == 'low':
            products = products.filter(Q(prepared_quantity__lte=49) & Q(prepared_quantity__gte=1))
        elif stock_filter == 'none':
            products = products.filter(prepared_quantity=0)
    

    paginator = Paginator(products, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
        
    context = {
        "page_obj": page_obj, # keep this as the Page object
        "products": page_obj.object_list,  # optional: if you want a plain list
        "form": form,
        "categories": categories,
        "out_of_stock": out_of_stock,
        'section': 'product',
    }

    return render(request, 'Product/product_list.html', context)

@login_required(login_url='login')
def product_create(request):
    if request.method == 'POST':
        form = ProductForm(request.POST)
        
        if form.is_valid():
            product = form.save(commit=False)
            product.description = product.description.title()
            product.name = product.name.title()
            product.save()

            messages.success(request, f"{product.name} has been created.")
            return redirect('product-list')
    
    else:
        form = ProductForm()
        
    context = {'form': form}
    return render(request, 'Product/product_create.html', context)

@login_required(login_url='login')
def product_detail(request, product_slug):
    product = get_object_or_404(Product, slug=product_slug)
    
    context = {'product': product}
    return render(request, 'Product/product_detail.html', context)

@login_required(login_url='login')
def product_update(request, product_slug):
    product = get_object_or_404(Product, slug=product_slug)
  
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        
        if form.is_valid():
            product = form.save(commit=False)
            product.name = product.name.title()
            product.save()
            messages.success(request, f"{product.name} has been updated.")
            return redirect('product-list')
    else:
        form = ProductForm(instance=product)
        
    context = {'form': form, 'product': product}
    return render(request, 'Product/product_update.html', context)

@login_required(login_url='login')
def product_delete(request, product_slug):
    product = get_object_or_404(Product, slug=product_slug)
    
    if request.method == 'POST':
        
        if product.material:
            messages.warning(request, f"This product is linked to inventory. Delete the stock record instead.")
            return redirect('product-list')

        product.delete()
        messages.success(request, f"{product.name} has been deleted.")
        return redirect('product-list')
    
    context = {'product': product}
    return render(request, 'Product/product_delete.html', context)

@login_required(login_url='login')  
def restore_batch_product(request):
    Product.objects.all().update(prepared_quantity=F('default_quantity'))
    messages.success(request, 'All products has been restored successfully.')
    return redirect('product-list')

@login_required(login_url='login')
def restore_product_quantity(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    product.restore_product_quantity()
    product.save()
    messages.success(request, f'{product.name} has been restored successfully.')
    return redirect(f"{reverse('product-list')}?{request.META.get('QUERY_STRING', '')}")
    

@login_required(login_url='login')
def add_product_to_preset(request):
    sale = request.session.get('sale', {})

    if request.method == 'POST':
        product_checkbox = request.POST.get('product_checkbox')
        product_name = request.POST.get('product_name')
        if product_checkbox and product_name:
            preset, _ = ProductPreset.objects.get_or_create(user=request.user, name=product_name, is_active=True)
            for product_id, data in sale.items():
                product = get_object_or_404(Product, id=product_id)
                quantity = data.get('quantity', 0)
                cost_price = data.get('cost_price', 0)
                
                ProductPresetItem.objects.get_or_create(
                    preset=preset,
                    product=product,
                    quantity=quantity,
                    cost_price=Decimal(cost_price),
                )
            messages.success(request, f"{product_name} has been added to preset.")
            
        elif product_checkbox and not product_name:
            messages.warning(request, "Forgot to add a preset title.")
            
        elif not product_checkbox and product_name:
            messages.warning(request, "Forgot to click the checkbox.")

    return redirect('view-sale')

@login_required(login_url='login')
def list_product_preset(request):
    presets = ProductPreset.objects.all().order_by('-created_at')
    
    pagination = Paginator(presets, 5)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {'presets': page_obj.object_list, 'page_obj': page_obj, 'section': 'product'}
    return render(request, 'Product/list_product_preset.html', context)

@login_required(login_url='login')
def detail_product_preset(request, preset_id):
    preset = get_object_or_404(ProductPreset, id=preset_id)
    preset_items = preset.product_preset_items.select_related('product')
    items = []
    
    
    for item in preset_items:
        name = item.product.name
        quantity = item.quantity
        selling_price = item.product.selling_price
        cost_price = Decimal(item.cost_price)
        total_cost_per_line = cost_price * quantity
        line_total = (selling_price * quantity)

        items.append({
            'id': item.product.id,
            'name': name,
            'quantity': quantity,
            'cost_price': cost_price,
            'line_total': line_total,
            'selling_price': selling_price,
            'total_cost_per_line': total_cost_per_line,
            
        })
    item_count = len(items)
    
    context= {'preset': preset, 'items': items, 'item_count': item_count, 'section': 'product'}
    return render(request, 'Product/detail_product_preset.html', context)

@login_required(login_url='login')
def edit_product_preset(request, preset_id):
    preset = get_object_or_404(ProductPreset, id=preset_id)
    preset_items = preset.product_preset_items.select_related('product')

    if request.method == 'POST':
        new_preset_name = request.POST.get(f'new_preset_name_{preset.id}')
    
        if new_preset_name and new_preset_name != preset.name:
            preset.name = new_preset_name
            preset.save()
            messages.success(request, f"The Preset Title has been updated.")
            
        for item in preset_items:
            # get the raw value int and then convert
            raw_qty = request.POST.get(f"new_product_quantity_{item.product.id}") 
            new_product_quantity = int(raw_qty) if raw_qty else None
            
            if new_product_quantity and new_product_quantity != item.quantity:
                item.quantity = new_product_quantity
                item.save()
                messages.success(request, f"The quantity has been updated.")
            
        return redirect('product-preset-list')
    
    context = {'preset': preset, 'preset_items': preset_items, 'section': 'product'}
    return render(request, 'Product/edit_product_preset.html', context)

@login_required(login_url='login')
def delete_product_preset(request, preset_id):
    preset = get_object_or_404(ProductPreset, id=preset_id)
    
    if request.method == 'POST':
        preset.delete()
        messages.success(request, f"{preset.name} has been deleted.")
        return redirect('product-preset-list')
    
    context = {'preset': preset, 'section': 'product'}
    return render(request, 'Product/delete_product_preset.html', context)

@login_required(login_url='login')
def product_add_preset_to_sale(request, preset_id):
    sale = request.session.get('sale', {})
    preset = get_object_or_404(ProductPreset, id=preset_id, is_active=True)
    preset_items = preset.product_preset_items.select_related('product')
    
    for item in preset_items:
        product = item.product
        quantity = item.quantity
        
        product_key = str(product.id)
        existing_qty = sale.get(product_key, {}).get('quantity', 0) + quantity

        if product_key in sale:
            if product.prepared_quantity >= existing_qty:
                sale[product_key]['quantity'] = existing_qty
                messages.success(request, f"{product.name}'s quantity has increased.")
            else:
                messages.warning(request, f"{product.name} - quantity limit reached.")
        else:
            sale[product_key] = {
                'id': item.product.id,
                'name': item.product.name,
                'quantity': quantity,
                'cost_price': str(item.cost_price),
                'unsold_quantity': 0,
            }
            messages.success(request, f"{preset.name} has been added to sale.")
            
    request.session['sale'] = sale
    request.session.modified = True
            
    url = request.GET.get('next', 'product-preset-list')
    return redirect(url)