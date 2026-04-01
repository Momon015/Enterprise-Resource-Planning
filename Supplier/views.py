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
from Supplier.forms import MaterialForm, MaterialFilterForm, SupplierForm, SupplierFilterForm

from django.core.paginator import Paginator

from django.db.models import Q, F, Sum, Max, Avg, Count

from decimal import Decimal

from user.models import User

from core.utils.owner import get_owner, permission_required, get_queryset_for_user

@login_required(login_url='login')
def material_list(request):
    owner = get_owner(request.user)
    cart = request.session.get('cart', {})
    total = 0
    
    cart_items = []
    
    if cart:
        for material_id, data in cart.items():
            material = get_object_or_404(Material, user=owner, id=material_id)
            
            # computations
            line_total = data.get('quantity', 0) * material.price
            total += line_total
            
            cart_items.append({
                'id': material.id,
                'name': material.name,
                'quantity': data.get('quantity', 0),
                'price': material.price,
                'line_total': line_total,
                'unit': material.unit
            })
            
    form = MaterialFilterForm(request.GET or None, user=owner)
    
    materials = get_queryset_for_user(request.user, Material.objects.all()).order_by('name')

    
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
    ).order_by('-material_count')[:3]  
    
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
           

    suppliers = get_queryset_for_user(request.user, Supplier.objects.all())
    
    context = {
        'categories': categories, 
        'page_obj': page_obj, 
        'cart_items': cart_items,
        'total': total,
        'suppliers': suppliers,
        'categories_count': categories_count,
        'top_categories': top_categories,
        'section': 'supplier',
          
        }
    
    return render(request, 'Supplier/material_list.html', context)

@login_required(login_url='login')
@permission_required('create')
def material_create(request):
    owner = get_owner(request.user)
    if request.method == 'POST':
        form = MaterialForm(request.POST, user=owner)
        
        if form.is_valid():
            material = form.save(commit=False)
            if Material.objects.filter(user=owner, name__iexact=material.name, unit=material.unit).exists():
                messages.warning(request, f"{material.name.title()} - {material.get_unit_display()} already exists.")
                return redirect('material-list')
            
            material.user = owner
            material.created_by = request.user
            material.name = material.name.title()
            material.save()
            messages.success(request, f"{material.name} successfully created.")
            return redirect('material-list')
    else:
        form = MaterialForm(user=owner)
        
    context = {'form': form, 'section': 'supplier'}
    return render(request, 'Supplier/material_create.html', context)

@login_required(login_url='login')
def material_detail(request, username, slug):
    if request.user.role == 'developer':
        owner = get_object_or_404(User, username=username)
    else:
        owner = get_owner(request.user)
    material = get_object_or_404(Material, slug=slug, user=owner)
    
    context = {'material': material, 'section': 'Supplier'}
    return render(request, 'Supplier/material_detail.html', context)

@login_required(login_url='login')
@permission_required('update')
def material_update(request, username, slug):
    if request.user.role == 'developer':
        owner = get_object_or_404(User, username=username)
        
        if owner != request.user:
            return render(request, 'core/no_access.html', status=403)
    else:   
        owner = get_owner(request.user)
        
    material = get_object_or_404(Material, user=owner, slug=slug)

    if request.method == 'POST':
        form = MaterialForm(request.POST, instance=material, user=owner)
        
        if form.is_valid():
            material = form.save(commit=False)
            material.name = material.name.title()
            material.user = owner
            material.created_by = request.user
            material.save()
            
            messages.success(request, f"{material.name} successfully updated.")
            url = request.GET.get('next', 'material-list')
            return redirect(url)
        else:
            print(form.errors)
        
    else:
        form = MaterialForm(instance=material, user=owner)
        
    context = {'form': form, 'material': material, 'section': 'supplier'}
    return render(request, 'Supplier/material_update.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
def material_delete(request, username, slug):
    
    if request.user.role == 'developer':
        owner = get_object_or_404(User, username=username)
        
        if owner != request.user:
            return render(request, 'core/no_access.html', status=403)
    else:
        owner = get_owner(request.user)
    
    material = get_object_or_404(Material, user=owner, slug=slug)
    
    if request.method == 'POST':
        material.delete()
        messages.success(request, f"{material.name} successfully deleted.")
        return redirect('material-list')
    
    context = {'material': material, 'section': 'supplier'}
    return render(request, 'Supplier/material_delete.html', context)

@login_required(login_url='login')
@permission_required('add')
def save_items(request):
    cart = request.session.get('cart', {})
    checkbox = request.POST.get('checkbox')
    name = request.POST.get('name').title()
    print('cart', cart)
    owner = get_owner(request.user)
    
    if not checkbox:
        messages.warning(request, 'You forgot to click the checkbox.')
        
    else:
        preset, _ = MaterialPreset.objects.get_or_create(user=owner, is_active=True, name=name, created_by=request.user)
      
        for material_id, data in cart.items():
            
            material = get_object_or_404(Material, user=owner, id=material_id)
            quantity = data['quantity']
            discount = data.get('discount', 0)
            
            MaterialPresetItem.objects.get_or_create(
                preset=preset,
                material=material,
                defaults={'quantity': quantity, 'discount': discount}
                
            )
        messages.success(request, f"{name} added to preset.")
        request.session['preset_id'] = preset.id
        return redirect('view-cart')

@login_required(login_url='login')
@permission_required('add') # dev
def adding_preset_to_cart(request, username, preset_id):
    if request.user.role == 'developer':
        owner = get_object_or_404(User, username=username)
        
        if owner != request.user:
            return render(request, 'core/no_access.html', status=403)
    else:
        owner = get_owner(request.user)
    cart = request.session.get('cart', {})
    
    preset = get_object_or_404(MaterialPreset, user=owner, id=preset_id)
    items = preset.preset_items.select_related('material')
    
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
            
            if material.quantity >= item.quantity + existing_qty:
                if material_key in cart:
                    cart[material_key]['quantity'] += item.quantity
                    messages.success(request, f"{material.name} quantity updated in purchase.")
                else:
                    cart[material_key] = {
                        'id': item.material.id,
                        'name': item.material.name,
                        'quantity': item.quantity,
                        'price': str(item.material.price),
                        'discount': str(item.discount),
                        
                    }
                    messages.success(request, f"{material.name} has added to purchase.")
                
            else:
                messages.warning(request, f"{material.name} exceeds the quantity limit.")
                    
    request.session['cart'] = cart
    request.session.modified = True

    # This allows to stay which 
    return redirect(f"{reverse('material-preset-list')}?{request.META.get('QUERY_STRING', '')}")
    
@login_required(login_url='login')
def preset_list(request):
    presets = get_queryset_for_user(request.user, MaterialPreset.objects.all()).order_by('name')
    
    paginator = Paginator(presets, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {'page_obj': presets, 'page_obj': page_obj, 'section': 'supplier'}
    return render(request, 'Supplier/list_preset.html', context)

@login_required(login_url='login')
def preset_detail(request, username, preset_id):
    if request.user.role == 'developer':
        owner = get_object_or_404(User, username=username)
        
    else:
        owner = get_owner(request.user)
        
    preset = get_object_or_404(MaterialPreset, user=owner, id=preset_id)

    context = {'preset': preset, 'section': 'supplier'}
    return render(request, 'Supplier/detail_preset.html', context)

@login_required(login_url='login')
@permission_required('update') # dev
def edit_preset(request, username, preset_id):
    if request.user.role == 'developer':
        owner = get_object_or_404(User, username=username)
        
        if owner != request.user:
            return render(request, 'core/no_access.html', status=403)
    else:
        owner = get_owner(request.user)
        
    preset = get_object_or_404(MaterialPreset, user=owner, id=preset_id)
    save_items = preset.preset_items.select_related('material')
    
    qty_changed = False
    discount_changed = False
    
    owner = get_owner(request.user)
    
    if request.method == 'POST':
        for item in save_items:
            new_qty = int(request.POST.get(f'quantity_{item.id}'))
            new_discount = int(request.POST.get(f"discount_{item.id}"))
            new_name = request.POST.get(f'preset_{preset.id}')
            
            if new_name and new_name != preset.name:
                preset.name = new_name.title()
                preset.user = owner
                preset.created_by = owner
                preset.save()
                messages.success(request, f"Preset Name has been updated. ")
                    
            if new_qty and new_qty != item.quantity:
                item.quantity = int(new_qty)
                item.save()
                qty_changed = True
                
            if new_discount and new_discount != item.discount: 
                item.discount = int(new_discount)
                item.save()
                discount_changed = True
                
        if qty_changed == True and discount_changed == True:
            messages.success(request, f"Both has been updated. ")

        if qty_changed == True and not discount_changed == True:
            messages.success(request, f"{item.material.name}'s quantity has been updated. ")
                    
        if discount_changed == True and not qty_changed == True:
            messages.success(request, f"{item.material.name}'s discount has been updated. ")
        
        return redirect('material-preset-list')
      
    context = {'preset': preset, 'items': save_items, 'section': 'supplier'}
    return render(request, 'Supplier/edit_preset.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
def delete_preset(request, username, preset_id):
    if request.user.role == 'developer':
        owner = get_object_or_404(User, username=username)
        
        if owner != request.user:
            return render(request, 'core/no_access.html', status=403)
    else:
        owner = get_owner(request.user)
    preset = get_object_or_404(MaterialPreset, user=owner, id=preset_id)
    
    if request.method == 'POST':
        preset.delete()
        messages.success(request, f"{preset.name} has been deleted.")
        return redirect('material-preset-list')
    
    context = {'preset': preset, 'section': 'supplier'}
    return render(request, 'Supplier/delete_preset.html', context)

@login_required(login_url='login')
def supplier_list(request):
    suppliers = get_queryset_for_user(request.user, Supplier.objects.all()).order_by('name')
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
@permission_required('add')
def supplier_create(request):
    owner = get_owner(request.user)
    
    if request.method == 'POST':
        form = SupplierForm(request.POST)
        
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.user = owner
            supplier.created_by = request.user
            supplier.name = supplier.name.title()
            supplier.save()
            messages.success(request, f"{supplier.name} successfully created.")
            return redirect('supplier-list')
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
@permission_required('update')
def supplier_update(request, username, supplier_id):
    if request.user.role == 'developer':
        owner = get_object_or_404(User, username=username)
        
        if owner != request.user:
            return render(request, 'core/no_access.html', status=403)
    else:
        owner = get_owner(request.user)
        
    supplier = get_object_or_404(Supplier, user=owner, id=supplier_id)
    owner = get_owner(request.user)
    
    if request.method == 'POST':
        form = SupplierForm(request.POST, instance=supplier)
        
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.name = supplier.name.title()
            supplier.user = owner
            supplier.created_by = request.user
            supplier.save()
            
            messages.success(request, f"{supplier.name} successfully updated.")
            return redirect(f"{reverse('supplier-list')}?{request.META.get('QUERY_STRING', '')}")
        else:
            print(form.errors)
        
    else:
        form = SupplierForm(instance=supplier)
        
    context = {'form': form, 'supplier': supplier, 'section': 'supplier'}
    return render(request, 'Supplier/supplier_update.html', context)


@login_required(login_url='login')
@permission_required('staff_delete')
def supplier_delete(request, username, supplier_id):
    if request.user.role == 'developer':
        owner = get_object_or_404(User, username=username)
        
        if owner != request.user:
            return render(request, 'core/no_access.html', status=403)
    else:
        owner = get_owner(request.user)
    supplier = get_object_or_404(Supplier, user=owner, id=supplier_id)
    
    if request.method == 'POST':
        supplier.delete()
        messages.success(request, f"{supplier.name} successfully deleted.")
        return redirect('supplier-list')
    
    context = {'supplier': supplier, 'section': 'supplier'}
    return render(request, 'Supplier/supplier_delete.html', context)