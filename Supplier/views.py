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

from django.db.models import Q

from django.core.paginator import Paginator

from django.db.models import F

from decimal import Decimal

# Create your views here.

@login_required(login_url='login')
def material_list(request):
    cart = request.session.get('cart', {})
    total = 0
    
    cart_items = []
    
    if cart:
        for material_id, data in cart.items():
            material = get_object_or_404(Material, id=material_id)
            
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
            
    form = MaterialFilterForm(request.GET or None)
    materials = Material.objects.all().order_by('name')

    
    """
    this allows to filter things without 
    causing any bugs like not showing anything 
    in template to ensure this always work
    """
    categories = form.fields['category'].queryset 
    
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
    paginator = Paginator(materials, 8)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
           

    suppliers = Supplier.objects.all()
    
    context = {
        'page_obj': materials, 
        'categories': categories, 
        'page_obj': page_obj, 
        'cart_items': cart_items,
        'total': total,
        'suppliers': suppliers,
        'section': 'supplier',
          
        }
    
    return render(request, 'Supplier/material_list.html', context)

@login_required(login_url='login')
def material_create(request):
    if request.method == 'POST':
        form = MaterialForm(request.POST)
        
        if form.is_valid():
            material = form.save(commit=False)
            if Material.objects.filter(user=request.user, name__iexact=material.name, unit=material.unit).exists():
                messages.warning(request, f"{material.name.title()} - {material.get_unit_display()} already exists.")
                return redirect('material-list')
            
            material.user = request.user
            material.name = material.name.title()
            material.save()
            messages.success(request, f"{material.name} successfully created.")
            return redirect('material-list')
    else:
        form = MaterialForm()
        
    context = {'form': form, 'section': 'supplier'}
    return render(request, 'Supplier/material_create.html', context)

@login_required(login_url='login')
def material_detail(request, slug):
    material = get_object_or_404(Material, slug=slug)
    
    context = {'material': material, 'section': 'Supplier'}
    return render(request, 'Supplier/material_detail.html', context)

@login_required(login_url='login')
def material_update(request, slug):
    material = get_object_or_404(Material, slug=slug)
    
    if request.method == 'POST':
        form = MaterialForm(request.POST, instance=material)
        
        if form.is_valid():
            material = form.save(commit=False)
            material.name = material.name.title()
            material.user = material.user
            material.save()
            
            messages.success(request, f"{material.name} successfully updated.")
            url = request.GET.get('next', 'material-list')
            return redirect(url)
        else:
            print(form.errors)
        
    else:
        form = MaterialForm(instance=material)
        
    context = {'form': form, 'material': material, 'section': 'supplier'}
    return render(request, 'Supplier/material_update.html', context)

@login_required(login_url='login')
def material_delete(request, slug):
    material = get_object_or_404(Material, slug=slug)
    
    if request.method == 'POST':
        material.delete()
        messages.success(request, f"{material.name} successfully deleted.")
        return redirect('material-list')
    
    context = {'material': material, 'section': 'supplier'}
    return render(request, 'Supplier/material_delete.html', context)

@login_required(login_url='login')  
def save_items(request):
    cart = request.session.get('cart', {})
    checkbox = request.POST.get('checkbox')
    name = request.POST.get('name')
    print('cart', cart)
    if checkbox:
        preset, _ = MaterialPreset.objects.get_or_create(user=request.user, is_active=True, name=name)
        
        for material_id, data in cart.items():
            
            material = get_object_or_404(Material, id=material_id)
            quantity = data['quantity']
            discount = data.get('discount', 0)
            
            MaterialPresetItem.objects.get_or_create(
                preset=preset,
                material=material,
                defaults={'quantity': quantity, 'discount': discount}
                
            )
            return redirect('material-preset-list')
        request.session['preset_id'] = preset.id
        
    return redirect('view-cart')

@login_required(login_url='login')
def adding_preset_to_cart(request, preset_id):
    cart = request.session.get('cart', {})
    
    preset = get_object_or_404(MaterialPreset, id=preset_id)
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
                    messages.success(request, f"{preset.name} has added to purchase.")
                
            else:
                messages.warning(request, f"{material.name} exceeds the quantity limit. Please adjust the {material.name}'s quantity accordingly.")
                    
    request.session['cart'] = cart
    request.session.modified = True


    
    # This allows to stay which 
    url = request.GET.get('next', reverse('material-preset-list'))
    return redirect(url)
    
@login_required(login_url='login')
def preset_list(request):
    presets = MaterialPreset.objects.all().order_by('name')
    
    paginator = Paginator(presets, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    context = {'page_obj': presets, 'page_obj': page_obj, 'section': 'supplier'}
    return render(request, 'Supplier/list_preset.html', context)

@login_required(login_url='login')
def preset_detail(request, preset_id):
    preset = get_object_or_404(MaterialPreset, id=preset_id)

    context = {'preset': preset, 'section': 'supplier'}
    return render(request, 'Supplier/detail_preset.html', context)

@login_required(login_url='login')
def edit_preset(request, preset_id):
    preset = get_object_or_404(MaterialPreset, id=preset_id)
    save_items = preset.preset_items.select_related('material')
    
    qty_changed = False
    discount_changed = False
    
    if request.method == 'POST':
        for item in save_items:
            new_qty = int(request.POST.get(f'quantity_{item.id}'))
            new_discount = int(request.POST.get(f"discount_{item.id}"))
            new_name = request.POST.get(f'preset_{preset.id}')
            
            if new_name and new_name != preset.name:
                preset.name = new_name
                preset.user = request.user
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
        
        return redirect('material-preset-detail', preset.id)
      
    context = {'preset': preset, 'items': save_items, 'section': 'supplier'}
    return render(request, 'Supplier/edit_preset.html', context)

@login_required(login_url='login')  
def delete_preset(request, preset_id):
    preset = get_object_or_404(MaterialPreset, id=preset_id)
    
    if request.method == 'POST':
        preset.delete()
        messages.success(request, f"{preset.name} has been deleted.")
        return redirect('material-preset-list')
    
    context = {'preset': preset, 'section': 'supplier'}
    return render(request, 'Supplier/delete_preset.html', context)

@login_required(login_url='login')
def supplier_list(request):
    suppliers = Supplier.objects.all().order_by('name')
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
def supplier_create(request):
    if request.method == 'POST':
        form = SupplierForm(request.POST)
        
        
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.user = request.user
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
def supplier_update(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    
    if request.method == 'POST':
        form = SupplierForm(request.POST, instance=supplier)
        
        if form.is_valid():
            supplier = form.save(commit=False)
            supplier.name = supplier.name.title()
            supplier.user = supplier.user
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
def supplier_delete(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id)
    
    if request.method == 'POST':
        supplier.delete()
        messages.success(request, f"{supplier.name} successfully deleted.")
        return redirect('supplier-list')
    
    context = {'supplier': supplier, 'section': 'supplier'}
    return render(request, 'Supplier/supplier_delete.html', context)