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

from Supplier.models import Material, MaterialPreset, MaterialPresetItem, Supplier
from Supplier.forms import MaterialForm, MaterialFilterForm, SupplierForm, SupplierFilterForm, PresetFilterForm

from django.core.paginator import Paginator

from django.db.models import Q, F, Sum, Max, Avg, Count

from decimal import Decimal

from user.models import User

from core.utils.owner import permission_required, get_queryset_for_user, get_business_for_user

from django.contrib.messages import get_messages

from django.core.exceptions import ValidationError

from subscription.decorators import capacity_required

# Create your views here

@login_required(login_url='login')
def material_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    cart = request.session.get('cart', {})
    total = 0
    
    cart_items = []
    
    if cart:
        for material_id, data in cart.items():
            material = get_object_or_404(Material, business=business, id=material_id)
            
            # computations
            line_total = data.get('quantity', 0) * material.price
            total += line_total
            
            cart_items.append({
                'id': material.id,
                'name': material.name,
                'quantity': data.get('quantity', 0),
                'price': material.price,
                'line_total': line_total,
                'unit': material.unit,
            })
            
    form = MaterialFilterForm(request.GET or None, business=business)
    
    materials = get_queryset_for_user(request.user, Material.objects.all()).filter(business=business).order_by('name')

    
    """
    this allows to filter things without 
    causing any bugs like not showing anything 
    in template to ensure this always work
    """
    categories = form.fields['category'].queryset
    categories_count = categories.count()
    
    # top 3 categories
    top_categories = categories.annotate(
        material_count=Count('materials')
    ).exclude(name='No category') \
    .order_by('-material_count')[:3]
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        category = request.GET.get('category')
        
        unit_map = {label.lower(): key for key, label in Material.RETAIL_UNIT_CHOICES}
        matched_unit = unit_map.get(search.lower())
        
        if search:
            materials = materials.filter(
                Q(name__icontains=search) |
                    Q(price__icontains=search) |
                    Q(quantity__icontains=search) |
                    Q(category__name__icontains=search) |
                    Q(unit__icontains=matched_unit or search)
            )   
        if category:
            materials = materials.filter(category=category)
        
    # pagination
    paginator = Paginator(materials, 7)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
           

    suppliers = get_queryset_for_user(request.user, Supplier.objects.all()).filter(business=business).order_by('-name')
    
    context = {
        'categories': categories, 
        'page_obj': page_obj, 
        'total': total,
        'suppliers': suppliers,
        'categories_count': categories_count,
        'top_categories': top_categories,
        'section': 'supplier',

        # HTMX
        'cart_count': sum(item['quantity'] for item in cart.values()),
        'clear_sessions': 'clear-cart',
        'cart_items': len(cart),
        'name': 'Materials',
        'total_name': 'cost',
        'type': 'purchase',
    
        }
    
    return render(request, 'Supplier/material_list.html', context)

@login_required(login_url='login')
@capacity_required('material')
@permission_required('create') # dev
def material_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    if request.method == 'POST':
        form = MaterialForm(request.POST, business=business)
        
        if form.is_valid():
            material = form.save(commit=False)
            if Material.objects.filter(business=business, name__iexact=material.name, unit=material.unit).exists():
                messages.warning(request, f"{material.name.title()} - {material.get_unit_display()} already exists.")
                return redirect('material-list', business=business_slug)
            
            material.user = business.user
            material.created_by = request.user
            material.name = material.name.title()
            material.business = business
            material.save()
            messages.success(request, f"{material.name} successfully created.")
            return redirect('material-list', business_slug=business.slug)
    else:
        form = MaterialForm(business=business)
        
    context = {'form': form, 'section': 'supplier'}
    return render(request, 'Supplier/material_create.html', context)

@login_required(login_url='login')
def material_detail(request, slug, id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    material = get_object_or_404(Material, business=business, slug=slug, id=id)
    
    context = {'material': material, 'section': 'Supplier'}
    return render(request, 'Supplier/material_detail.html', context)

@login_required(login_url='login')
@permission_required('update')
def material_update(request,  slug, id, business_slug):
    business = get_business_for_user(request.user, business_slug)
        
    material = get_object_or_404(Material, business=business, slug=slug, id=id)

    if request.method == 'POST':
        form = MaterialForm(request.POST, instance=material, business=business)
        
        if form.is_valid():
            material = form.save(commit=False)
            material.name = material.name.title()
            # material.user = business.user
            material.save()
            
            messages.success(request, f"{material.name} successfully updated.")
            url = request.GET.get('next', 'material-list')
            return redirect(url, business_slug=business.slug)
        else:
            print(form.errors)
        
    else:
        form = MaterialForm(instance=material, business=business)
        
    context = {'form': form, 'material': material, 'section': 'supplier'}
    return render(request, 'Supplier/material_update.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
@permission_required('delete') # dev
def material_delete(request, slug, id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    material = get_object_or_404(Material, business=business, slug=slug, id=id)
    
    if request.method == 'POST':
        material.delete()
        messages.success(request, f"{material.name} successfully deleted.")
        return redirect('material-list', business_slug=business.slug)
    
    context = {'material': material, 'section': 'supplier'}
    return render(request, 'Supplier/material_delete.html', context)

@login_required(login_url='login')
@capacity_required('material_preset')
@permission_required('add') # dev
def save_items(request, business_slug):
    cart = request.session.get('cart', {})
    
    business = get_business_for_user(request.user, business_slug)
    
    if request.method == 'POST':
        checkbox = request.POST.get('checkbox')
        name = request.POST.get('name').title()
        
        if checkbox and not name:
                messages.warning(request, "You forgot to add a preset title.")
                
        elif not checkbox and name:
            messages.warning(request, "You forgot to click the checkbox.")
        
        else:
            messages.warning(request, "Please add a preset title and don't forget to check the checkbox.")
            
        if checkbox and name:
            preset, _ = MaterialPreset.objects.get_or_create(
                user=business.user,
                business=business, 
                name=name,
                defaults={
                    'is_active': True,
                    'created_by': request.user
                })
        
            for material_id, data in cart.items():
                
                material = get_object_or_404(Material, business=business, id=material_id)
                quantity = data['quantity']
                discount = data.get('discount', 0)
                
                MaterialPresetItem.objects.get_or_create(
                    preset=preset,
                    material=material,
                    defaults={'quantity': quantity, 'discount': discount}
                    
                )
            messages.success(request, f"{name} added to preset.")
            request.session['preset_id'] = preset.id
        return redirect('view-cart', business_slug=business.slug)

@login_required(login_url='login')
@permission_required('add') # dev
def adding_preset_to_cart(request, preset_id, business_slug):
    business = get_business_for_user(request.user, business_slug)

    cart = request.session.get('cart', {})
    
    preset = get_object_or_404(MaterialPreset, business=business, id=preset_id)
    items = preset.preset_items.select_related('material')
    
    added_count = 0
    failed_count = 0
    
    if preset:
        for item in items:
            material = item.material
            material_key = str(material.id)
            
            """
            The cart.get() will get the material_key if it exists
            then it will get the quantity else 0 and it will not 
            throw a KeyError if there is a low stock in inventory
            because in your condition you added both item's quantity 
            and cart's quantity to check if it's low then It will not 
            be added to the cart session.
            """
            existing_qty = cart.get(material_key, {}).get('quantity', 0)
            
            if material_key in cart:
                if material.quantity >= item.quantity + existing_qty:
                    cart[material_key]['quantity'] += item.quantity
                    added_count += 1
                else:
                    failed_count += 1
            else:
                cart[material_key] = {
                    'id': item.material.id,
                    'name': item.material.name,
                    'quantity': item.quantity,
                    'price': str(item.material.price),
                    'discount': str(item.discount),
                }
                added_count += 1
                
        # Show ONE summary message
        if added_count > 0:
            messages.success(request, f"{preset.name} - {added_count} item(s) added to purchase.")
        if failed_count > 0:
            messages.warning(request, f"{failed_count} item(s) couldn't be added (quantity limit).")
                

    request.session['cart'] = cart
    request.session.modified = True
    
    
    # HTMX
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'core/partials/_preset_response.html', {
            'cart_url': 'view-cart',
            'label': 'Purchase Record',
            'cart_count': sum(item['quantity'] for item in cart.values()),
            'cart_items': len(cart),
            'messages': get_messages(request)
        })

    # This allows to stay which page the user currently in after adding.
    query_string = request.META.get('QUERY_STRING', '')
    url = reverse('material-preset-list', kwargs={'business_slug': business.slug})
    return redirect(f"{url}?{query_string}" if query_string else url)
    
@login_required(login_url='login')
def preset_list(request, business_slug):
    cart = request.session.get('cart', {})
    
    business = get_business_for_user(request.user, business_slug)
    
    presets = get_queryset_for_user(request.user, MaterialPreset.objects.all()).filter(business=business).order_by('-created_at')
    
    form = PresetFilterForm(request.GET or None)
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        
        if search:
            presets = presets.filter(name__icontains=search)
    
    paginator = Paginator(presets, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {
        'page_obj': page_obj, 
        'section': 'supplier',
        
        # HTMX
        'cart_url': 'view-cart',
        'label': 'Purchase Record',
        'cart_count': sum(item['quantity'] for item in cart.values()),
        'cart_items': len(cart),
        'messages': get_messages(request)
    }
    return render(request, 'Supplier/list_preset.html', context)

@login_required(login_url='login')
def preset_detail(request, business_slug, id, slug):
    business = get_business_for_user(request.user, business_slug)
    
    preset = get_object_or_404(MaterialPreset, business=business, id=id, slug=slug)

    context = {'preset': preset, 'section': 'supplier'}
    return render(request, 'Supplier/detail_preset.html', context)

@login_required(login_url='login')
@permission_required('update') # dev
def edit_preset(request, business_slug, id, slug):
    business = get_business_for_user(request.user, business_slug)
        
    preset = get_object_or_404(MaterialPreset, business=business, id=id, slug=slug)
    save_items = preset.preset_items.select_related('material')
    
    qty_changed = False
    discount_changed = False
    
    if request.method == 'POST':
        for item in save_items:
            raw_qty = request.POST.get(f'quantity_{item.id}')
            new_qty = int(raw_qty)
            
            raw_discount = request.POST.get(f"discount_{item.id}")
            new_discount = int(raw_discount)
            
            new_name = request.POST.get(f'preset_{preset.id}')
            
            if new_name and new_name != preset.name: # validate the name with max and min length 
                preset.name = new_name.title()
                preset.save()
                messages.success(request, f"Preset Name has been updated. ")
                    
            if new_qty and new_qty != item.quantity:
                item.quantity = new_qty
                item.save()
                qty_changed = True
                
            if new_discount and new_discount != item.discount: 
                item.discount = new_discount
                item.save()
                discount_changed = True
                
        if qty_changed == True and discount_changed == True:
            messages.success(request, f"Both has been updated. ")

        if qty_changed == True and not discount_changed == True:
            messages.success(request, f"{item.material.name}'s quantity has been updated. ")
                    
        if discount_changed == True and not qty_changed == True:
            messages.success(request, f"{item.material.name}'s discount has been updated. ")
        
        return redirect('material-preset-list', business_slug=business.slug)
      
    context = {'preset': preset, 'items': save_items, 'section': 'supplier'}
    return render(request, 'Supplier/edit_preset.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
def delete_preset(request, business_slug, id, slug):
    business = get_business_for_user(request.user, business_slug)
    
    preset = get_object_or_404(MaterialPreset, business=business, id=id, slug=slug)
    
    if request.method == 'POST':
        preset.delete()
        messages.success(request, f"{preset.name} has been deleted.")
        return redirect('material-preset-list', business_slug=business.slug)
    
    context = {'preset': preset, 'section': 'supplier'}
    return render(request, 'Supplier/delete_preset.html', context)

@login_required(login_url='login')
def supplier_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    suppliers = get_queryset_for_user(request.user, Supplier.objects.all()).filter(business=business).order_by('name')
    form = SupplierFilterForm(request.GET or None)
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        
        if search:
            suppliers = suppliers.filter(name__icontains=search)
        
    pagination = Paginator(suppliers, 6)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {'page_obj': page_obj, 'section': 'supplier'}
    return render(request, 'Supplier/supplier_list.html', context)

@login_required(login_url='login')
@capacity_required('supplier')
@permission_required('add') # dev
def supplier_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    if request.method == 'POST':
        form = SupplierForm(request.POST)
        
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.user = business.user
            supplier.created_by = request.user
            supplier.business = business
            supplier.name = supplier.name.title()
            try:
                supplier.save()
            except ValidationError as e:
                messages.warning(request, e.messages[0])
                return redirect('supplier-list', business_slug=business.slug)
            
            messages.success(request, f"{supplier.name} successfully created.")
            return redirect('supplier-list', business_slug=business.slug)
    else:
        form = SupplierForm()
        
    context = {'form': form, 'section': 'supplier'}
    return render(request, 'Supplier/supplier_create.html', context)

# @login_required(login_url='login')
# def supplier_detail(request, supplier_id):
#     supplier = get_object_or_404(Supplier, id=supplier_id)
    
#     context = {'supplier': supplier, 'section': 'Supplier'}
#     return render(request, 'Supplier/supplier_detail.html', context)

@login_required(login_url='login')
@permission_required('update') # dev
def supplier_update(request, business_slug, supplier_id, slug):
    business = get_business_for_user(request.user, business_slug)
    supplier = get_object_or_404(Supplier, business=business, id=supplier_id, slug=slug)

    if request.method == 'POST':
        form = SupplierForm(request.POST, instance=supplier)
        
        if form.is_valid():
            if supplier.slug == 'no-supplier':
                messages.warning(request, '"No Supplier" is a system default and cannot be edited — it holds materials that have no supplier assigned.')
                return redirect('supplier-list', business_slug=business.slug)
            
            supplier = form.save(commit=False)
            supplier.name = supplier.name.title()
            supplier.save()
            messages.success(request, f"{supplier.name} successfully updated.")
            query_string = request.META.get('QUERY_STRING', '')
            url = reverse('supplier-list', kwargs={'business_slug': business.slug})
            return redirect(f"{url}?{query_string}" if query_string else url)
        else:
            print(form.errors)
    else:
        form = SupplierForm(instance=supplier)
        
    context = {'form': form, 'supplier': supplier, 'section': 'supplier'}
    return render(request, 'Supplier/supplier_update.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
@permission_required('delete') # dev
def supplier_delete(request, business_slug, supplier_id, slug):
    business = get_business_for_user(request.user, business_slug)
    supplier = get_object_or_404(Supplier, business=business, id=supplier_id, slug=slug)
    
    if request.method == 'POST':
        if supplier.slug == 'no-supplier':
            messages.warning(request, '"No Supplier" is a system default and cannot be deleted — it holds materials that have no supplier assigned.')
            return redirect('supplier-list', business_slug=business.slug)
            
        supplier.delete()
        messages.success(request, f"{supplier.name} successfully deleted.")
        return redirect('supplier-list', business_slug=business.slug)
    
    context = {'supplier': supplier, 'section': 'supplier'}
    return render(request, 'Supplier/supplier_delete.html', context)