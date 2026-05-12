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

from core.utils.owner import get_owner, permission_required, get_queryset_for_user, get_business_for_user
# Create your views here.

@login_required(login_url='login')
def product_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    form = ProductFilterForm(request.GET or None, business=business)
    
    """
    The helper function allows to isolate the owner and the staffs for every client.
    """
    
    products = get_queryset_for_user(request.user, Product.objects.all()).filter(business=business).order_by('name')
    
    # option 2
    # if request.user.role == 'developer':
    #     products = Product.objects.all().order_by('name')
    # else:
    #     products = Product.objects.filter(user=owner).order_by('name')

    """
    this allows to filter things without 
    causing any bugs like not showing anything 
    in template to ensure this always work
    """
    categories = form.fields['category'].queryset
    
    stock_filter = request.GET.get('stock')
    all_products = products.count()
    in_stock = products.filter(prepared_quantity__gte=50).count()
    low_stock = products.filter(Q(prepared_quantity__lte=49) & Q(prepared_quantity__gte=1)).count()
    out_of_stock = products.filter(prepared_quantity=0).count()
    
    

    if form.is_valid():
        search = form.cleaned_data.get('search')
        category = form.cleaned_data.get('category')
        
        if search:

            products = products.filter(
                Q(name__icontains=search) | 
                Q(category__name__icontains=search) | 
                Q(description__icontains=search) |
                Q(category__category_type__icontains=search) |
                Q(selling_price__icontains=search)
                
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
    
    MULTI_UNIT_TYPES = ('Pack', 'Bundle', 'Tray', 'Dozen', 'Carton', 'Sachet', 'Box', 'Bag')
    
        
    context = {
        "page_obj": page_obj, # keep this as the Page object
        "products": page_obj.object_list,  # optional: if you want a plain list
        "form": form,
        "categories": categories,
        "out_of_stock": out_of_stock,
        'low_stock': low_stock,
        'in_stock': in_stock,
        'all_products': all_products,
        'multi_unit_types': MULTI_UNIT_TYPES,
        'section': 'product',
    }

    return render(request, 'Product/product_list.html', context)

@login_required(login_url='login')
@permission_required('add') # dev
def product_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    if request.method == 'POST':
        form = ProductForm(request.POST, business=business)

        if form.is_valid():
            
            # if business.business_type == 'retail':
                
            product = form.save(commit=False)
            product.name = product.name.title()
            if Product.objects.filter(business=business, name__iexact=product.name.title(), unit=product.unit).exists():
                messages.warning(request, f"{product.name} is already exists. Please create another product name.")
                return redirect('product-list', business_slug=business.slug)
            
            product.user = business.user
            product.name = product.name.title()
            product.business = business
            product.created_by = request.user
            
            if product.description:
                product.description = product.description.title()

            product.save()

            messages.success(request, f"{product.name} has been created.")
            return redirect('product-list', business_slug=business.slug)
            
            # elif business.business_type in ('cafe', 'restaurant'):
            #     messages.info(request, "🚀 Cafe & Restaurant features launching soon! For now, this business is in view-only mode.")
            #     return redirect('product-list', business_slug=business.slug)
            # else:
            #     # Fallback for unknown types
            #     messages.error(request, "Unsupported business type.")
            #     return redirect('product-list', business_slug=business.slug)
    else:
        form = ProductForm(business=business)
        
    context = {'form': form}
    return render(request, 'Product/product_create.html', context)

@login_required(login_url='login')
def product_detail(request, business_slug, product_slug, product_id):
    business = get_business_for_user(request.user, business_slug)
    
    product = get_object_or_404(Product, business=business, slug=product_slug, id=product_id)
    
    context = {'product': product}
    return render(request, 'Product/product_detail.html', context)

@login_required(login_url='login')
@permission_required('read_only') # dev
def product_update(request, business_slug, product_slug, product_id):
    business = get_business_for_user(request.user, business_slug)
        
    product = get_object_or_404(Product, business=business, slug=product_slug, id=product_id)
    
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product, business=business)
        
        if form.is_valid():
            product = form.save(commit=False)
            product.name = product.name.title()
            product.save()
            messages.success(request, f"{product.name} has been updated.")
            return redirect('product-list', business_slug=business.slug)
    else:
        form = ProductForm(instance=product, business=business)
        
    context = {'form': form, 'product': product}
    return render(request, 'Product/product_update.html', context)


@login_required(login_url='login')
@permission_required('staff_delete')
def product_delete(request, business_slug, product_slug, product_id):
    business = get_business_for_user(request.user, business_slug)
        
    product = get_object_or_404(Product, business=business, slug=product_slug, id=product_id)
    
    if request.method == 'POST':
        
        if product.material:
            messages.warning(request, f"This product is linked to inventory. Delete the stock record instead.")
            return redirect('product-list', business_slug=business.slug)

        product.delete()
        messages.success(request, f"{product.name} has been deleted.")
        return redirect('product-list', business_slug=business.slug)
    
    context = {'product': product}
    return render(request, 'Product/product_delete.html', context)

@login_required(login_url='login')  
@permission_required('staff_add') # staff
@permission_required('add') # dev
def restore_batch_product(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    Product.objects.filter(business=business).update(prepared_quantity=F('default_quantity'))
    messages.success(request, 'All products has been restored successfully.')
    return redirect('product-list', business_slug=business.slug)

@login_required(login_url='login')
@permission_required('staff_add') # staff
@permission_required('add') # dev
def restore_product_quantity(request, business_slug, product_id):
    business = get_business_for_user(request.user, business_slug)
    product = get_object_or_404(Product, business=business, id=product_id)
    product.restore_product_quantity()
    product.save()
    messages.success(request, f'{product.name} has been restored successfully.')
    return redirect(f"{redirect('product-list', business_slug=business.slug)}?{request.META.get('QUERY_STRING', '')}")
    

@login_required(login_url='login')
@permission_required('add') # dev
def add_product_to_preset(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})

    if request.method == 'POST':
        product_checkbox = request.POST.get('product_checkbox')
        product_name = request.POST.get('product_name')
        
        if product_checkbox and not product_name:
            messages.warning(request, "You forgot to add a preset title.")
            
        elif not product_checkbox and product_name:
            messages.warning(request, "You forgot to click the checkbox.")
        
        elif not product_checkbox and not product_name:
            messages.warning(request, "Please add a preset title and don't forget to click the checkbox.")
            
        if product_checkbox and product_name:
            preset, _ = ProductPreset.objects.get_or_create(
                business=business,
                user=business.user,
                name=product_name.title(),
                defaults={
                'is_active':True, 
                'created_by':request.user
                
            })
            
            for product_id, data in sale.items():
                product = get_object_or_404(Product, business=business, id=product_id)
                quantity = data.get('quantity', 0)
                cost_price = data.get('cost_price', 0)
                
                ProductPresetItem.objects.get_or_create(
                    preset=preset,
                    product=product,
                    defaults={
                    'quantity': quantity,
                    'cost_price': Decimal(cost_price),
                }
)
            messages.success(request, f"{product_name} has been added to preset.")  

    return redirect('view-sale', business_slug=business.slug)

@login_required(login_url='login')
def list_product_preset(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    presets = get_queryset_for_user(request.user, ProductPreset.objects.all()).filter(business=business).order_by('-created_at')
    
    pagination = Paginator(presets, 5)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {'presets': page_obj.object_list, 'page_obj': page_obj, 'section': 'product'}
    return render(request, 'Product/list_product_preset.html', context)

@login_required(login_url='login')
def detail_product_preset(request, business_slug, preset_id, preset_slug):
    business = get_business_for_user(request.user, business_slug)
    
    preset = get_object_or_404(ProductPreset, business=business, slug=preset_slug, id=preset_id)
    preset_items = preset.product_preset_items.select_related('product')
    items = []

    for item in preset_items:
        
        if item.product:
            id = item.product.id
            name = item.product.name
            supplier_name = item.supplier_name
            quantity = item.quantity
            selling_price = item.product.selling_price
            cost_price = Decimal(item.cost_price)
            total_cost_per_line = cost_price * quantity
            line_total = (selling_price * quantity)
        else:
            continue
        
        items.append({
            'supplier_name': supplier_name,
            'id': id,
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
def edit_product_preset(request, business_slug, preset_id, preset_slug):
    business = get_business_for_user(request.user, business_slug)
        
    preset = get_object_or_404(ProductPreset, business=business, slug=preset_slug, id=preset_id)
    preset_items = preset.product_preset_items.select_related('product')

    if request.method == 'POST':
        new_preset_name = request.POST.get(f'new_preset_name_{preset.id}')
    
        if new_preset_name and new_preset_name != preset.name:
            preset.name = new_preset_name.title()
            preset.save()
            messages.success(request, f"The Preset Title has been updated.")
            
        for item in preset_items:
            if not item.product:
                continue
            
            # get the raw value int and then convert
            raw_qty = request.POST.get(f"new_product_quantity_{item.product.id}") 
            new_product_quantity = int(raw_qty) if raw_qty else None
            
            if new_product_quantity and new_product_quantity != item.quantity:
                item.quantity = new_product_quantity
                item.save()
                messages.success(request, f"The quantity has been updated.")
            
        return redirect('product-preset-list', business_slug=business.slug)
    
    context = {'preset': preset, 'preset_items': preset_items, 'section': 'product'}
    return render(request, 'Product/edit_product_preset.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
def delete_product_preset(request, business_slug, preset_slug, preset_id):
    business = get_business_for_user(request.user, business_slug)
    preset = get_object_or_404(ProductPreset, business=business, slug=preset_slug, id=preset_id)
    
    if request.method == 'POST':
        preset.delete()
        messages.success(request, f"{preset.name} has been deleted.")
        return redirect('product-preset-list', business_slug=business.slug)
    
    context = {'preset': preset, 'section': 'product'}
    return render(request, 'Product/delete_product_preset.html', context)

@login_required(login_url='login')
def product_add_preset_to_sale(request, business_slug, preset_slug, preset_id):
    business = get_business_for_user(request.user, business_slug)
        
    sale = request.session.get('sale', {})
    preset = get_object_or_404(ProductPreset, business=business, slug=preset_slug, id=preset_id)
    preset_items = preset.product_preset_items.select_related('product')
    
    added_count = 0
    failed_count = 0
    
    for item in preset_items:
        product = item.product
        
        if not product:
            failed_count += 1
            continue
            
        id = item.product.id
        name = item.product.name
        quantity = item.quantity
        product_key = str(product.id)
        existing_qty = sale.get(product_key, {}).get('quantity', 0) + quantity

        if product_key in sale:
            if product.prepared_quantity >= existing_qty:
                sale[product_key]['quantity'] = existing_qty
                added_count += 1
            else:
                failed_count += 1
        else:
            sale[product_key] = {
                'id': id,
                'name': name,
                'quantity': quantity,
                'cost_price': str(item.cost_price),
                'selling_price': str(item.product.selling_price)
            }
            added_count += 1
    
    # Show ONE summary message
    if added_count > 0:
        messages.success(request, f"{preset.name} - {added_count} product(s) added to sale.")
    if failed_count > 0:
        messages.warning(request, f"{failed_count} product(s) couldn't be added (quantity limit).")
    
    request.session['sale'] = sale
    request.session.modified = True
            
    return redirect(f"{reverse('product-preset-list', kwargs={'business_slug': business.slug})}?{request.META.get('QUERY_STRING', '')}")