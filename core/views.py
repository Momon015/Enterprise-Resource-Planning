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
    
    context = {'page_obj': page_obj, 'section': section}
    return render(request, 'core/category_list.html', context)

@login_required(login_url='login')
def category_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    if request.method == 'POST':
        form = CategoryForm(request.POST)
        
        if form.is_valid():
            category = form.save(commit=False)
            
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
        form = CategoryForm()
    
    context = {'form': form}
    return render(request, 'core/category_create.html', context)

@login_required(login_url='login')
def category_update(request, business_slug, category_id, slug):
    business = get_business_for_user(request.user, business_slug)
    category = get_object_or_404(Category, business=business, id=category_id, slug=slug)

    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category)
        
        if form.is_valid():
            if category.slug == 'no-category' or category.slug.startswith('no-category-'):
                messages.warning(request, '"No Category" is a system default and cannot be edited — it holds materials and products without a category.')
                return redirect('category-list', business_slug=business.slug)
            obj = form.save(commit=False)
            obj.name = obj.name.title()
            obj.save()
            messages.success(request, f"{category.name} has successfully updated.")
            return redirect('category-list', business_slug=business.slug)
    
    else:
        form = CategoryForm(instance=category)
    
    context = {'form': form, 'category': category}
    return render(request, 'core/category_update.html', context)


@login_required(login_url='login')
@permission_required('staff_delete')
@permission_required('read_only') # dev
def category_delete(request, business_slug, category_id, slug):
    business = get_business_for_user(request.user, business_slug)
    category = get_object_or_404(Category, business=business, id=category_id, slug=slug)
    
    if request.method == 'POST':
        if category.slug == 'no-category' or category.slug.startswith('no-category-'):
            messages.warning(request, '"No Category" is a system default and cannot be deleted — it holds materials and products without a category.')
            return redirect('category-list', business_slug=business.slug)
        
        category.delete()
        messages.success(request, f"{category.name} has successfully deleted.")
        return redirect('category-list', business_slug=business.slug)
    
    context = {'category': category}
    return render(request, 'core/category_delete.html', context)