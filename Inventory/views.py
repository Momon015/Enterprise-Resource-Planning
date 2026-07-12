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

from Inventory.models import Stock, STOCK_CRITICAL_Q, STOCK_LOW_Q
from Inventory.forms import StockFilterForm

from django.db.models import Q, F, Sum, Avg, Max, DecimalField
from django.db.models.functions import Coalesce
from decimal import Decimal

from django.core.paginator import Paginator

from user.models import User

from core.utils.owner import get_owner, permission_required, get_queryset_for_user, get_business_for_user

from subscription.decorators import capacity_required

from activity.models import ActivityEvent
from activity.utils import scope_events_for_user

from core.constants import (LOW_STOCK_THRESHOLD, HIGH_STOCK_THRESHOLD, NO_STOCK_THRESHOLD,
                            CRITICAL_STOCK_THRESHOLD)

from activity.models import ActivityEvent 

# Create your views here.

@login_required(login_url='login')
def view_inventory_stock(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    stocks = get_queryset_for_user(request.user, Stock.objects.all()) \
        .filter(business=business) \
        .exclude(material__status='inactive') \
        .order_by('-quantity')

    most_stock_category_name = (stocks.values('material__category__name') \
    .annotate(total_count=Sum('material')).order_by('-total_count').first()
    )
    
    
    # Retail / Grocery / Pharmacy classify stock by the linked PRODUCT's category.
    # Cafe / Restaurant keep the MATERIAL's (ingredient) category.
    user_product_category = (
        business.business_type != 'cafe' and business.business_type != 'restaurant'
    )
    category_type = 'product' if user_product_category else 'material'
    
    most_stock_category_name = most_stock_category_name['material__category__name'] if most_stock_category_name else 'N/A'
    
    form = StockFilterForm(request.GET or None, business=business, category_type=category_type)
    categories = form.fields['category'].queryset
    
    all_stocks = stocks.count()
    in_stock = stocks.filter(quantity__gte=HIGH_STOCK_THRESHOLD).count()
    # low and critical are DISJOINT bands (Inventory/models.py) — Low Stock EXCLUDES the
    # criticals, and ?stock=low does not list them.
    low_stock = stocks.filter(STOCK_LOW_Q).count()
    critical_stock = stocks.filter(STOCK_CRITICAL_Q).count()
    out_of_stock = stocks.filter(quantity=NO_STOCK_THRESHOLD).count()

    # ── Money tied up in each state (the KPI card ▼ breakdowns) ──────────────
    # ONE pass over the same rows, three conditional sums — same bands as the counts above,
    # so each ▼ describes exactly the rows its card counts. Computed BEFORE the search /
    # category / stock filters below reassign `stocks`, so both always cover the whole catalog.
    # No Out-of-Stock value: qty is 0 there, so price x qty can only ever be 0.
    # Low and Critical are DISJOINT, so their money does NOT overlap — the two ▼ figures
    # are separate pots and can be added together.
    def _value_of(condition):
        return Coalesce(
            Sum(F('price') * F('quantity'), filter=condition,
                output_field=DecimalField(max_digits=14, decimal_places=2)),
            Decimal('0'),
            output_field=DecimalField(max_digits=14, decimal_places=2),
        )

    stock_values = stocks.aggregate(
        in_stock=_value_of(Q(quantity__gte=HIGH_STOCK_THRESHOLD)),
        low_stock=_value_of(STOCK_LOW_Q),
        critical_stock=_value_of(STOCK_CRITICAL_Q),
    )

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
        stocks = stocks.filter(quantity__gte=HIGH_STOCK_THRESHOLD)
        
    elif stock_filter == 'low':
        stocks = stocks.filter(STOCK_LOW_Q)      # excludes critical

    elif stock_filter == 'critical':
        stocks = stocks.filter(STOCK_CRITICAL_Q)

    elif stock_filter == 'none':
        stocks = stocks.filter(quantity=NO_STOCK_THRESHOLD)
        
    grand_total_value = sum(stock.price * stock.quantity for stock in stocks)
    
    pagination = Paginator(stocks, 5)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    MULTI_UNIT_TYPES = ('Pack', 'Bundle', 'Tray', 'Dozen', 'Carton', 'Sachet', 'Box', 'Bag')
    
    recent_events = ActivityEvent.objects.filter(
        Q(verb__startswith='stock.') |
        Q(verb__startswith='material.'),
        business=business
    )
    recent_events = scope_events_for_user(recent_events, request.user)[:4]
    
    from core.utils.kpis import get_inventory_kpis
    
    kpis = get_inventory_kpis(business)
    
    INVENTORY_VERBS = [
        'stock.adjusted',   # fires for purchase/sale/waste/returns — captures all movement
        'stock.low',        # threshold alert
        'stock.out',        # threshold alert
    ]

    recent_events = (
        ActivityEvent.objects
        .filter(business=business, verb__in=INVENTORY_VERBS)
        .select_related('actor')
        .order_by('-created_at')[:4]
    )

    context = {
               'page_obj': page_obj, 
               'section': 'inventory',
               'grand_total_value': grand_total_value,
               'multi_unit_types': MULTI_UNIT_TYPES, 
               'most_stock_category_name': most_stock_category_name,
               'out_of_stock': out_of_stock,
               'low_stock': low_stock,
               'critical_stock': critical_stock,
               'in_stock': in_stock,
               'all_stocks': all_stocks,
               'stock_values': stock_values,
               'categories': categories,
               'recent_events': recent_events,
               'kpis': kpis,
            }
    
    return render(request, 'Inventory/view_inventory_stock.html', context)

