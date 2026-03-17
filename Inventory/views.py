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

from django.db.models import Q

from django.core.paginator import Paginator

from django.db.models import F

# Create your views here.

@login_required(login_url='login')
def view_inventory_stock(request):
    stocks = Stock.objects.all().order_by('-created_at')
    grand_total_value = 0
    
    for stock in stocks:
        total_value = stock.price * stock.quantity
        grand_total_value += total_value
    
    form = StockFilterForm(request.GET or None)
    categories = form.fields['category'].queryset
    if form.is_valid():
        search = form.cleaned_data.get('search')
        category = form.cleaned_data.get('category')
        
        if search:
            unit_map = {label.lower(): key for key, label in Material.UNIT_CHOICES}
            matched_unit = unit_map.get(search.lower())
            stocks = stocks.filter(
                Q(material__name__icontains=search) |
                Q(material__unit=matched_unit or search) |
                Q(material__supplier__name__icontains=search)
            )
        
        if category:
            stocks = stocks.filter(material__category=category)
        
    stock = request.GET.get('stock')
    
    if stock == 'high':
        stocks = stocks.filter(quantity__gte=25)
        
    elif stock == 'low':
        stocks = stocks.filter(quantity__lte=24)
    
    elif stock == 'none':
        stocks = stocks.filter(quantity=0)
    
    pagination = Paginator(stocks, 6)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)

    context = {'stocks': page_obj.object_list, 'page_obj': page_obj, 'section': 'inventory', 'grand_total_value': grand_total_value, 'categories': categories}
    return render(request, 'Inventory/view_inventory_stock.html', context)