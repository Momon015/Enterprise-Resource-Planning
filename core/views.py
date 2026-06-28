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

from Sales.models import Sale, SaleItem
from Sales.forms import SaleForm

from Product.models import Product
from Product.forms import ProductForm

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

from core.models import Category
from core.forms import CategoryForm, CategoryFilterForm

from core.utils.owner import get_owner, permission_required, get_queryset_for_user, get_business_for_user
# logging
import logging

# Create your views here.

def landing_page(request):
    if request.user.is_authenticated:
        return redirect('')
    
    return render(request, 'landing.html')


@login_required(login_url='login')
def category_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    categories = get_queryset_for_user(request.user, Category.objects.all()).filter(business=business).order_by('-name')
    section = None
    
    form = CategoryFilterForm(request.GET or None)
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        
        if search:
            categories = categories.filter(name__icontains=search)

    category_type = request.GET.get('category_type')
    
    if category_type == 'product':
        categories = categories.filter(category_type='product')
        section = 'product'

    elif category_type == 'material':
        categories = categories.filter(category_type='material')
        section = 'supplier'
    
    elif category_type == 'expense':
        categories = categories.filter(category_type='expense')
        section = 'expense'

    pagination = Paginator(categories, 5)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {
        'business': business,
        'page_obj': page_obj, 
        'section': section
    }
    return render(request, 'core/category_list.html', context)

@login_required(login_url='login')
def category_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    if request.method == 'POST':
        form = CategoryForm(request.POST, user=request.user, business=business)
        
        if form.is_valid():
            category = form.save(commit=False)
            if category.category_type != 'product':
                category.target_margin = None
            
            if Category.objects.filter(name__iexact=category.name, business=business).exists():
                messages.error(request, f"{category.name.title()} is already exist. Please use a different name for Category.")
            else:
                category.user = business.user
                category.business = business
                category.created_by = request.user
                category.name = category.name.title()
                try:
                    category.save()
                except ValidationError as e:
                    messages.warning(request, e.messages[0])
                    return redirect('category-list', business_slug=business.slug)
 
                messages.success(request, f"{category.name} has successfully created.")
                return redirect('category-list', business_slug=business.slug)
    else:
        form = CategoryForm(user=request.user, business=business)
        
    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/_category_form_modal.html', {
            'form': form,
            'cat_title': 'New Category',
            'cat_subtitle': 'Create a category to organize your items.',
            'cat_action': reverse('category-create', kwargs={'business_slug': business.slug}),
            'cat_label': 'Save Category',
            'cat_icon': 'bi-tag-fill',
        })
        
    context = {'form': form}
    return render(request, 'core/category_create.html', context)

@login_required(login_url='login')
def category_update(request, business_slug, category_id, slug):
    business = get_business_for_user(request.user, business_slug)
    category = get_object_or_404(Category, business=business, id=category_id, slug=slug)

    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category, user=request.user, business=business)
        
        if form.is_valid():
            if category.slug == 'no-category' or category.slug.startswith('no-category-'):
                messages.warning(request, '"No Category" is a system default and cannot be edited — it holds materials and products without a category.')
                return redirect('category-list', business_slug=business.slug)
            obj = form.save(commit=False)
            if obj.category_type != 'product':
                obj.target_margin = None

            obj.name = obj.name.title()
            obj.save()
            messages.success(request, f"{category.name} has successfully updated.")
            return redirect('category-list', business_slug=business.slug)
    
    else:
        form = CategoryForm(instance=category, user=request.user, business=business)
        
    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/_category_form_modal.html', {
            'form': form,
            'cat_title': 'Edit Category',
            'cat_subtitle': category.name,
            'cat_action': reverse('category-update', kwargs={
                'business_slug': business.slug, 'category_id': category.id, 'slug': category.slug}),
            'cat_label': 'Save Changes',
            'cat_icon': 'bi-pencil-square',
        })

    context = {'form': form, 'category': category}
    return render(request, 'core/category_update.html', context)


@login_required(login_url='login')
@permission_required('staff_delete')
@permission_required('read_only') # dev
def category_delete(request, business_slug, category_id, slug):
    business = get_business_for_user(request.user, business_slug)
    category = get_object_or_404(Category, business=business, id=category_id, slug=slug)

    def _back_to_list():
        if request.headers.get('HX-Request'):
            resp = HttpResponse(status=204)
            resp['HX-Redirect'] = reverse('category-list', kwargs={'business_slug': business.slug})
            return resp
        return redirect('category-list', business_slug=business.slug)

    if request.method == 'POST':
        if category.slug == 'no-category' or category.slug.startswith('no-category-'):
            messages.warning(request, '"No Category" is a system default and cannot be deleted — it holds materials and products without a category.')
            return _back_to_list()

        category.delete()
        messages.success(request, f"{category.name} has successfully deleted.")
        return _back_to_list()

    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/_confirm_modal.html', {
            'cm_title': category.name,
            'cm_subtitle': category.get_category_type_display,
            'cm_note': "Items in this category will fall back to <strong>No Category</strong>. This can’t be undone.",
            'cm_action': reverse('category-delete', kwargs={
                'business_slug': business.slug, 'category_id': category.id, 'slug': category.slug}),
            'cm_label': 'Delete Category',
            'cm_tone': 'danger',
            'cm_icon': 'bi-tag-fill',
            'cm_btn_icon': 'bi-trash',
        })

    context = {'category': category}
    return render(request, 'core/category_delete.html', context)


from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Q

from core.utils.owner import get_business_for_user
from Product.models import Product, ProductPreset
from Supplier.models import Material, Supplier, MaterialPreset
from Employee.models import Employee
from Sales.models import Sale
from Expense.models import Purchase


@login_required(login_url='login')
def global_search(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    q = request.GET.get('q', '').strip()

    # under 2 chars → empty response so the dropdown stays hidden (:empty)
    if len(q) < 2:
        return render(request, 'core/partials/_search_results.html', {'q': q})

    is_owner = request.user.role == 'owner'

    products = Product.goods.filter(business=business, name__icontains=q)[:6]
    services = Product.services.filter(business=business, name__icontains=q)[:6]
    materials = Material.objects.filter(business=business, name__icontains=q)[:5]
    suppliers = Supplier.objects.filter(business=business, name__icontains=q)[:5]
    product_presets = (ProductPreset.objects
                       .filter(business=business, name__icontains=q)
                       .prefetch_related('product_preset_items__product'))[:5]
    material_presets = MaterialPreset.objects.filter(business=business, name__icontains=q)[:5]

    # staff — OWNER ONLY
    staff = (Employee.objects.filter(business=business)
             .filter(Q(name__icontains=q) | Q(staff_user__username__icontains=q))[:5]
             if is_owner else Employee.objects.none())

    # references — owner: all · staff: their own
    sales = Sale.objects.filter(business=business, reference__icontains=q)
    purchases = Purchase.objects.filter(business=business, reference__icontains=q)
    if not is_owner:
        sales = sales.filter(created_by=request.user)
        purchases = purchases.filter(created_by=request.user)
    sales = sales.order_by('-id')[:5]
    purchases = purchases.order_by('-id')[:5]

    context = {
        'q': q, 'current_business': business,
        'products': products, 'services': services,
        'materials': materials, 'suppliers': suppliers,
        'product_presets': product_presets, 'material_presets': material_presets,
        'staff': staff, 'sales': sales, 'purchases': purchases,
        'has_results': any([products, services, materials, suppliers, 
                            product_presets, material_presets, staff, sales, purchases]),
    }

    return render(request, 'core/partials/_search_results.html', context)
