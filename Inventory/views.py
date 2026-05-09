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

from Supplier.models import Material, MaterialPreset, MaterialPresetItem
from Supplier.forms import MaterialForm, MaterialFilterForm

from Product.models import Product

from Inventory.models import Stock
from Inventory.forms import StockFilterForm

from django.db.models import Q, F, Sum, Avg, Max

from django.core.paginator import Paginator

from user.models import User

from core.utils.owner import get_owner, permission_required, get_queryset_for_user, get_business_for_user

# Create your views here.

@login_required(login_url='login')
def view_inventory_stock(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    stocks = get_queryset_for_user(request.user, Stock.objects.all()).filter(business=business).order_by('-name')
    
    most_stock_category_name = (stocks.values('material__category__name') \
    .annotate(total_count=Sum('material')).order_by('-total_count').first()
    )
    
    most_stock_category_name = most_stock_category_name['material__category__name'] if most_stock_category_name else 'N/A'
    
    form = StockFilterForm(request.GET or None, business=business)
    categories = form.fields['category'].queryset
    if form.is_valid():
        search = form.cleaned_data.get('search')
        category = form.cleaned_data.get('category')
        
        if search:
            unit_map = {label.lower(): key for key, label in Material.RETAIL_UNIT_CHOICES}
            matched_unit = unit_map.get(search.lower())
            stocks = stocks.filter(
                Q(material__name__icontains=search) |
                Q(material__unit=matched_unit or search) |
                Q(material__supplier__name__icontains=search)
            )
        
        if category:
            stocks = stocks.filter(material__category=category)
        
    stock_filter = request.GET.get('stock')
    
    if stock_filter == 'high':
        stocks = stocks.filter(quantity__gte=50)
        
    elif stock_filter == 'low':
        stocks = stocks.filter(quantity__lte=49, quantity__gte=1)
    
    elif stock_filter == 'none':
        stocks = stocks.filter(quantity=0)
        
    grand_total_value = sum(stock.price * stock.quantity for stock in stocks)
    
    pagination = Paginator(stocks, 9)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    MULTI_UNIT_TYPES = ('Pack', 'Bundle', 'Tray', 'Dozen', 'Carton', 'Sachet', 'Box', 'Bag')
    
    context = {
               'page_obj': page_obj, 
               'section': 'inventory', 
               'grand_total_value': grand_total_value,
               'multi_unit_types': MULTI_UNIT_TYPES, 
               'most_stock_category_name': most_stock_category_name,
               'categories': categories
            }
    
    return render(request, 'Inventory/view_inventory_stock.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
def inventory_stock_delete(request, business_slug, stock_id):
    business = get_business_for_user(request.user, business_slug)
    
    stock = get_object_or_404(Stock, business=business, id=stock_id)

    if request.method == 'POST':
        if stock.material:
            Product.objects.filter(business=business, material=stock.material).delete()
            stock.delete()
            messages.success(request, f"{stock.name} - both stock and product has been deleted.")
        return redirect('view-inventory-stock', business_slug=business.slug)
    
    context = {'stock': stock, 'section': 'inventory'}
    return render(request, 'Inventory/inventory_stock_delete.html', context)