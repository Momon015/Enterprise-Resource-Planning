from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404, HttpResponseNotAllowed
from django.views.generic import ListView, UpdateView, CreateView, DeleteView, FormView, DetailView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required

from django.contrib import messages

from django.utils import timezone
from datetime import timedelta
import random

from django.views.decorators.http import require_POST
from django.urls import reverse

from django.core.paginator import Paginator

from Product.models import Product, ProductPreset, ProductPresetItem
from Product.forms import ProductForm, ProductFilterForm, ProductPresetFilterForm, ServiceForm, ServiceSessionFormSet

from Sales.models import Sale, SaleItem, SalesReturnItem
from Expense.models import Purchase, PurchaseItem

from decimal import Decimal
from django.db.models import Q, F, Sum
from django.db.models.functions import Coalesce
from django.contrib.messages import get_messages

from subscription.decorators import capacity_required

from activity.models import ActivityEvent
from activity.utils import log_activity, scope_events_for_user
from Product.models import CRITICAL_BAND_Q, LOW_BAND_Q, with_stock_bands

from core.utils.owner import permission_required, get_queryset_for_user, get_business_for_user
from core.constants import LOW_STOCK_THRESHOLD, HIGH_STOCK_THRESHOLD, NO_STOCK_THRESHOLD

# Create your views here.

@login_required(login_url='login')
def product_list(request, business_slug):
    sale = request.session.get('sale', {})
    total = 0
    
    business = get_business_for_user(request.user, business_slug)
    request.session['catalog_return'] = request.path
    
    if sale:
        for data in sale.values():
            price = Decimal(data['selling_price']) * data['quantity']
            total += price

    form = ProductFilterForm(request.GET or None, business=business)
    
    """
    The helper function allows to isolate the owner and the staffs for every client.
    """
    
    products = get_queryset_for_user(request.user, Product.goods.all()) \
        .filter(business=business) \
        .select_related('category', 'material__supplier') \
        .order_by('is_locked', '-prepared_quantity')
        
    products = products.annotate(units_sold=Coalesce(Sum('sale_items__quantity'), 0))

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
    velocity_filter = request.GET.get('velocity')
    
    all_products = products.count()
    in_stock = products.filter(prepared_quantity__gte=F('high_stock_threshold')).count()
    # low and critical are DISJOINT bands (see Product/models.py) — a critically-low
    # product is NOT counted in Low Stock, and ?stock=low does not list it.
    _banded = with_stock_bands(products)
    low_stock = _banded.filter(LOW_BAND_Q).count()
    critical_stock = _banded.filter(CRITICAL_BAND_Q).count()
    out_of_stock = products.filter(prepared_quantity=NO_STOCK_THRESHOLD).count()
    
    
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
            products = products.filter(prepared_quantity__gte=F('high_stock_threshold'))
        elif stock_filter == 'low':
            # low EXCLUDES critical — the two cards are separate buckets
            products = with_stock_bands(products).filter(LOW_BAND_Q)
        elif stock_filter == 'critical':
            products = with_stock_bands(products).filter(CRITICAL_BAND_Q)
        elif stock_filter == 'none':
            products = products.filter(prepared_quantity=NO_STOCK_THRESHOLD)
            
            
        if velocity_filter in ('never', 'best', 'slow'):
            products = products.annotate(
                units_sold=Coalesce(Sum('sale_items__quantity'), 0)
            )
            if velocity_filter == 'never':
                products = products.filter(units_sold=0)
            elif velocity_filter == 'best':
                products = products.filter(units_sold__gt=0).order_by('-units_sold')
            elif velocity_filter == 'slow':
                products = products.filter(units_sold__gt=0).order_by('units_sold')

    

    paginator = Paginator(products, 5)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    MULTI_UNIT_TYPES = ('Pack', 'Bundle', 'Tray', 'Dozen', 'Carton', 'Sachet', 'Box', 'Bag')
    
    # recent_events = ActivityEvent.objects.filter(
    #     Q(verb__startswith='product.') |
    #     Q(verb__startswith='sale.') |
    #     Q(verb__startswith='purchase.') |
    #     Q(verb__startswith='stock.'),
    #     business=business,
    # )
    # recent_events = scope_events_for_user(recent_events, request.user)[:3]
    
    # recent_events = list(recent_events)
    # for e in recent_events:
    #     if e.target_url(business.slug):
    #         e.computed_url = reverse('activity-click', kwargs={
    #             'business_slug': business.slug, 'event_id': e.id,
    #         })
    #     else:
    #         e.computed_url = None


    from core.utils.kpis import get_product_kpis
    kpis = get_product_kpis(business)
    
    archived_count = Product.all_objects.filter(business=business, is_active=False).count()
        
    context = {
        "page_obj": page_obj, # keep this as the Page object
        "products": page_obj.object_list,  # optional: if you want a plain list
        "form": form,
        "categories": categories,
        "out_of_stock": out_of_stock,
        'low_stock': low_stock,
        'critical_stock': critical_stock,
        'in_stock': in_stock,
        'all_products': all_products,
        'multi_unit_types': MULTI_UNIT_TYPES,
        'section': 'product',
        'archived_count': archived_count,
        'kpis': kpis,
        
        #htmx
        'cart_items': len(sale),
        'cart_count': sum(item['quantity'] for item in sale.values()),
        'clear_sessions': 'clear-sale',
        'total': total,
        'name': 'Products',
        'total_name': 'sales',
        'type': 'sales',
    }

    return render(request, 'Product/product_list.html', context)

@login_required(login_url='login')
@capacity_required('product')
@permission_required('add') # dev
def product_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    # ── Retail / pharmacy: products are BORN from purchases, never hand-made ──────
    # Recording a purchase creates the Material's Stock row AND the Product together
    # (Expense/views.py), so they stay linked. A hand-made product has material=None,
    # which means: Sales can't find its Stock row (the lookup is keyed on material and
    # the miss is swallowed), and a purchase can never restock it — it would only ever
    # count DOWN. So this is a real dead-end, not just UI tidiness: the button is hidden
    # in the template AND the URL is closed here.
    # Cafe/restaurant are exempt — there, products (menu items) genuinely are hand-made
    # FROM materials, so the two are different things. Services have their own
    # service_create view and are unaffected.
    # 404, not a redirect: nobody reaches this by accident (the button is hidden for these
    # types), so the only visitor is someone poking at the URL — and bouncing a real user
    # to a page they didn't ask for is more confusing than saying the page isn't there.
    if business.business_type in ('retail', 'pharmacy'):
        raise Http404("Products are created from purchases for this business type.")

    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, business=business, user=request.user)

        if form.is_valid():
            
            # if business.business_type == 'retail':
                
            product = form.save(commit=False)
            existing = Product.all_objects.filter(
                business=business,
                name__iexact=product.name.title(),
            ).first()
            
            if existing:
                if existing.is_active:
                    messages.warning(request, f"{existing.name} already exists.")
                    return redirect('product-create', business_slug=business.slug)
                else:
                    # Archived twin exists — offer restore instead of creating duplicate
                    messages.info(request,f"{existing.name} exists in your archive.")
                    return redirect('product-create', business_slug=business.slug)

            product.user = business.user
            product.name = product.name.title()
            product.business = business
            product.created_by = request.user
            
            if product.description:
                product.description = product.description.title()

            product.save()
            
            log_activity(business, request.user, 'product.created',
                target=product, description=f"{product.name} added")

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
        form = ProductForm(business=business, user=request.user)
        
    context = {'form': form, 'section': 'product'}
    return render(request, 'Product/product_create.html', context)

@login_required(login_url='login')
def product_detail(request, business_slug, product_slug, product_id):
    business = get_business_for_user(request.user, business_slug)
    product = get_object_or_404(Product, business=business, slug=product_slug, id=product_id)
    request.session['catalog_return'] = request.path
    
    total_stock_cost = product.prepared_quantity * product.cost_price
    potential_revenue = product.prepared_quantity * product.selling_price

    # ── Demand + restock signals ──────────────────────────────
    today = timezone.localdate()
    window_start = today - timedelta(days=30)

    # Units SOLD (net of returns), excluding voided sales
    sold_30d = SaleItem.objects.filter(
        product=product, sale__is_void=False, sale__date__gte=window_start,
    ).aggregate(q=Sum('quantity'))['q'] or 0
    returned_30d = SalesReturnItem.objects.filter(
        original_sale_item__product=product,
        original_sale_item__sale__is_void=False,
        original_sale_item__sale__date__gte=window_start,
    ).aggregate(q=Sum('quantity'))['q'] or 0
    units_sold_30d = max(sold_30d - returned_30d, 0)

    sold_all = SaleItem.objects.filter(
        product=product, sale__is_void=False,
    ).aggregate(q=Sum('quantity'))['q'] or 0
    returned_all = SalesReturnItem.objects.filter(
        original_sale_item__product=product,
        original_sale_item__sale__is_void=False,
    ).aggregate(q=Sum('quantity'))['q'] or 0
    units_sold_all = max(sold_all - returned_all, 0)

    last_sold = Sale.objects.filter(
        sale_items__product=product, is_void=False,
    ).order_by('-date').values_list('date', flat=True).first()

    # RESTOCK + mover — only valid where product IS its stock 1:1 (resale).
    # Cafe/restaurant products are recipes of many materials → sell-through is meaningless.
    restocked_30d, last_restock, first_restock, mover = 0, None, None, None
    is_resale = business.is_retail or business.is_pharmacy
    if is_resale and product.material_id:
        restocked_30d = PurchaseItem.objects.filter(
            material_id=product.material_id,
            purchase__is_void=False,
            purchase__purchase_date__gte=window_start,
        ).aggregate(q=Sum('quantity'))['q'] or 0

        dates = list(Purchase.objects.filter(
            materials__material_id=product.material_id,
            is_void=False, purchase_date__isnull=False,
        ).order_by('purchase_date').values_list('purchase_date', flat=True))
        if dates:
            first_restock, last_restock = dates[0], dates[-1]

        if restocked_30d > 0:
            ratio = units_sold_30d / restocked_30d
            if ratio >= 0.70:
                mover = 'fast'
            elif ratio >= 0.30:
                mover = 'steady'
            else:
                mover = 'slow'
            if mover == 'slow' and first_restock and first_restock > window_start:
                mover = 'new'
                
    recent_sales = (SaleItem.objects
        .filter(product=product, sale__is_void=False)
        .select_related('sale')
        .order_by('-sale__date', '-sale__id')[:6])

    recent_restocks = []
    if product.material_id:
        recent_restocks = (PurchaseItem.objects
            .filter(material_id=product.material_id, purchase__is_void=False)
            .select_related('purchase')
            .order_by('-purchase__purchase_date', '-purchase__id')[:6])


    context = {
        'product': product,
        'total_stock_cost': total_stock_cost,
        'potential_revenue': potential_revenue,
        'section': 'product',
        'units_sold_30d': units_sold_30d,
        'units_sold_all': units_sold_all,
        'last_sold': last_sold,
        'restocked_30d': restocked_30d,
        'last_restock': last_restock,
        'mover': mover,
        'recent_sales': recent_sales,
        'recent_restocks': recent_restocks,

    }
    
    return render(request, 'Product/product_detail.html', context)




@login_required(login_url='login')
@permission_required('read_only') # dev
def product_update(request, business_slug, product_slug, product_id):
    business = get_business_for_user(request.user, business_slug)
        
    product = get_object_or_404(Product, business=business, slug=product_slug, id=product_id)
    
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product, business=business, user=request.user)
        if form.is_valid():
            product = form.save(commit=False)
            product.name = product.name.title()
            
            existing = Product.all_objects.filter(
                business=business,
                name__iexact=product.name.title(),
            ).exclude(id=product_id).first()
        
            if existing:
                if existing.is_active:
                    messages.warning(request, f"{existing.name} already exists.")
                    return redirect('product-update', business_slug=business.slug, product_id=product_id, product_slug=product_slug)
                else:
                    # Archived twin exists — offer restore instead of creating duplicate
                    messages.info(request,f"{existing.name} exists in your archive.")
                    return redirect('product-update', business_slug=business.slug, product_id=product_id, product_slug=product_slug)
            
            product.save()
            
            log_activity(business, request.user, 'product.updated',
                target=product, description=f"{product.name} updated")

            messages.success(request, f"{product.name} has been updated.")
            return redirect('product-list', business_slug=business.slug)
    else:
        form = ProductForm(instance=product, business=business, user=request.user)
        
    context = {'form': form, 'product': product, 'section': 'product'}
    return render(request, 'Product/product_update.html', context)


def product_archive(request, business_slug, product_slug, product_id):
    business = get_business_for_user(request.user, business_slug)
    product = get_object_or_404(Product, business=business, slug=product_slug, id=product_id)

    if request.method == 'POST':
        product.is_active = False
        product.save(update_fields=['is_active'])
        log_activity(business, request.user, 'product.archived',
            target=product, description=f"{product.name} archived")
        messages.success(request, f"{product.name} has been archived. You can restore it anytime.")
        if request.headers.get('HX-Request'):
            resp = HttpResponse(status=204)
            resp['HX-Redirect'] = reverse('product-list', kwargs={'business_slug': business.slug})
            return resp
        return redirect('product-list', business_slug=business.slug)

    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/_confirm_modal.html', {
            'cm_title': f"{product.name}",
            'cm_subtitle': f"{getattr(product.category, 'name', 'No category')} · SKU {product.sku}",
            'cm_note': "Hidden from listings &amp; stock tracking · Sales history kept · <strong>Restore anytime</strong>.",
            'cm_action': reverse('product-archive', kwargs={
                'business_slug': business.slug, 'product_slug': product.slug, 'product_id': product.id}),
            'cm_label': "Confirm Archive",
            'cm_icon': 'bi-basket',
            'cm_image': product.image.url if product.image else None,
        })

    return render(request, 'Product/product_archive.html', {'product': product})  # full-page fallback


@login_required(login_url='login')  
@permission_required('staff_add') # staff
@permission_required('add') # dev
def restore_batch_product(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    Product.goods.filter(business=business).update(prepared_quantity=F('default_quantity'))
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
@capacity_required('product_preset')
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
            # only non-service cart items can seed a preset
            sale_products = []
            for product_id, data in sale.items():
                product = Product.objects.filter(business=business, id=product_id).first()
                if not product or product.is_service:
                    continue
                sale_products.append((product, data))

            if not sale_products:
                messages.warning(request, "Service fees can't be saved as presets — presets are for products only.")
                return redirect('view-sale', business_slug=business.slug)

            preset, _ = ProductPreset.objects.get_or_create(
                business=business,
                user=business.user,
                name=product_name.title(),
                defaults={'is_active': True, 'created_by': request.user},
            )

            for product, data in sale_products:
                ProductPresetItem.objects.get_or_create(
                    preset=preset,
                    product=product,
                    defaults={
                        'quantity': data.get('quantity', 0),
                        'cost_price': Decimal(data.get('cost_price', 0)),
                    }
                )

            messages.success(request, f"{product_name} has been added to preset.")


    return redirect('view-sale', business_slug=business.slug)

@login_required(login_url='login')
def list_product_preset(request, business_slug):
    sale = request.session.get('sale', {})
    
    business = get_business_for_user(request.user, business_slug)
    presets = get_queryset_for_user(request.user, ProductPreset.objects.all()).filter(business=business).order_by('is_locked', 'name')
    
    form = ProductPresetFilterForm(request.GET or None)
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        
        if search:
            presets = presets.filter(name__icontains=search)
    
    
    pagination = Paginator(presets, 5)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {
        'page_obj': page_obj, 
        'section': 'product',
        
        # HTMX
        'label': 'Sales Record',
        'messages': get_messages(request),
        'cart_items': len(sale),
        'cart_count': sum(product['quantity'] for product in sale.values()),
        'cart_url': 'view-sale',
        
    }
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
def remove_product_preset_item(request, business_slug, preset_id, item_id):
    business = get_business_for_user(request.user, business_slug)
    preset = get_object_or_404(ProductPreset, business=business, id=preset_id)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    # Empty-preset guard
    if preset.product_preset_items.count() <= 1:
        messages.error(request, "A preset needs at least one item. Delete the preset instead.")
        return HttpResponse(status=204, headers={'HX-Refresh': 'true'})

    item = get_object_or_404(ProductPresetItem, id=item_id, preset=preset)
    name = item.product.name
    item.delete()
    messages.success(request, f"{name} removed from {preset.name}.")
    return HttpResponse("")  # htmx swaps the row out with empty content



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
    
    
    # HTMX 
    if request.headers.get('HX-Request') == 'true':
        resp = render(request, 'core/partials/_preset_response.html', {
            'label': 'Sales Record',
            'messages': get_messages(request),
            'cart_items': len(sale),
            'cart_count': sum(product['quantity'] for product in sale.values()),
            'cart_url': 'view-sale',
            'preset': preset,
        })
        resp['HX-Trigger'] = 'cartChanged'
        return resp
    
    # fallback — honor ?next= so we stay on the detail page the user added from
    next_url = request.GET.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(f"{reverse('product-preset-list', kwargs={'business_slug': business.slug})}?{request.META.get('QUERY_STRING', '')}")

@login_required(login_url='login')
@permission_required('owner_only')  # owner
@permission_required('add') # dev
def archived_products(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    products = Product.all_objects.filter(business=business, is_active=False, is_service=False).order_by('-id')
    if request.headers.get('HX-Request'):
        return render(request, 'Product/partials/_archived_products_modal.html', {
            'products': products,
            'business': business,
        })
    return render(request, 'Product/archived_products.html', {
        'products': products,
        'business': business,
        'section': 'product'
    })

@login_required(login_url='login')
@permission_required('owner_only') # owner
@permission_required('add') # dev
def restore_product(request, business_slug, product_id):
    business = get_business_for_user(request.user, business_slug)
    product = get_object_or_404(Product.all_objects, business=business, id=product_id, is_active=False)
    
    if request.method == 'POST':
        if product.material and product.material.status == 'inactive':
            alert = f"Restore '{product.material.name}' first from materials — this product is linked to it."
            if request.headers.get('HX-Request'):
                products = Product.all_objects.filter(business=business, is_active=False, is_service=False).order_by('-id')
                return render(request, 'Product/partials/_archived_products_modal.html', {
                    'products': products,
                    'business': business,
                    'reload_on_close': True,
                    'cm_alert': alert,
                })
            messages.warning(request, alert)
            return redirect('archived-products', business_slug=business.slug)

        product.is_active = True
        product.save(update_fields=['is_active'])

        log_activity(business, request.user, 'product.restored',
             target=product, description=f"{product.name} restored")

        # messages.success(request, f"{product.name} restored.")
        if request.headers.get('HX-Request'):
            products = Product.all_objects.filter(business=business, is_active=False, is_service=False).order_by('-id')
            return render(request, 'Product/partials/_archived_products_modal.html', {
                'products': products,
                'business': business,
                'reload_on_close': True,
            })
    return redirect('archived-products', business_slug=business.slug)

# ── Service Fees ──────────────────────────────────────────────
@login_required(login_url='login')
def service_list(request, business_slug):
    cart_count = 0
    sale = request.session.get('sale', '')
    if sale:
        cart_count = sum(item['quantity'] for item in sale.values())
    business = get_business_for_user(request.user, business_slug)
    request.session['catalog_return'] = request.path

    services = get_queryset_for_user(request.user, Product.services.all()) \
        .filter(business=business) \
        .order_by('is_locked', 'name')

    search = request.GET.get('search', '').strip()
    if search:
        services = services.filter(
            Q(name__icontains=search) | Q(selling_price__icontains=search)
        )

    all_services = services.count()

    from core.utils.kpis import get_service_kpis
    kpis = get_service_kpis(business)

    paginator = Paginator(services, 8)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'page_obj': page_obj,
        'services': page_obj.object_list,
        'search': search,
        'all_services': all_services,
        'cart_count': cart_count,
        'section': 'service',
        
        'kpis': kpis,


    }
    return render(request, 'Product/service_list.html', context)

@login_required(login_url='login')
def service_session_picker(request, business_slug, product_id):
    business = get_business_for_user(request.user, business_slug)
    service = get_object_or_404(Product.services, business=business, id=product_id, is_session_based=True)
    return render(request, 'core/partials/_session_picker.html', {'service': service})


@login_required(login_url='login')
@permission_required('add')  # dev
def service_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    if request.method == 'POST':
        form = ServiceForm(request.POST, request.FILES, business=business, user=request.user)
        formset = ServiceSessionFormSet(request.POST, prefix='sessions')

        if form.is_valid() and formset.is_valid():
            session_based = form.cleaned_data.get('is_session_based')
            has_tier = any(
                cd and cd.get('label') and not cd.get('DELETE')
                for cd in (f.cleaned_data for f in formset.forms)
            )
            if session_based and not has_tier:
                messages.error(request, "Add at least one session tier (e.g. 1 hr — ₱70).")
            else:
                service = form.save(commit=False)
                existing = Product.all_objects.filter(
                    business=business, name__iexact=service.name.title(),
                ).first()
                if existing:
                    messages.warning(request, f"{existing.name} already exists.")
                    return redirect('service-create', business_slug=business.slug)

                service.user = business.user
                service.name = service.name.title()
                service.business = business
                service.created_by = request.user
                service.save()

                if session_based:
                    formset.instance = service
                    formset.save()

                log_activity(business, request.user, 'product.created',
                    target=service, description=f"{service.name} (service fee) added")
                messages.success(request, f"{service.name} has been created.")
                return redirect('service-list', business_slug=business.slug)
    else:
        form = ServiceForm(business=business, user=request.user)
        formset = ServiceSessionFormSet(prefix='sessions')

    context = {'form': form, 'formset': formset, 'section': 'service'}
    return render(request, 'Product/service_create.html', context)


@login_required(login_url='login')
@permission_required('read_only')  # dev
def service_update(request, business_slug, service_slug, service_id):
    business = get_business_for_user(request.user, business_slug)
    service = get_object_or_404(Product.services, business=business, slug=service_slug, id=service_id)

    if request.method == 'POST':
        form = ServiceForm(request.POST, request.FILES, instance=service, business=business, user=request.user)
        formset = ServiceSessionFormSet(request.POST, instance=service, prefix='sessions')

        if form.is_valid() and formset.is_valid():
            session_based = form.cleaned_data.get('is_session_based')
            has_tier = any(
                cd and cd.get('label') and not cd.get('DELETE')
                for cd in (f.cleaned_data for f in formset.forms)
            )
            if session_based and not has_tier:
                messages.error(request, "Add at least one session tier (e.g. 1 hr — ₱70).")
            else:
                service = form.save(commit=False)
                service.name = service.name.title()

                existing = Product.all_objects.filter(
                    business=business, name__iexact=service.name.title(),
                ).exclude(id=service.id).first()
                if existing:
                    if existing.is_active:
                        messages.warning(request, f"{existing.name} already exists.")
                    else:
                        messages.info(request, f"{existing.name} exists in your archive.")
                    return redirect('service-update', business_slug=business.slug,
                                    service_id=service_id, service_slug=service_slug)

                service.save()

                if session_based:
                    formset.save()
                else:
                    service.sessions.all().delete()   # switched to flat → drop stale tiers

                log_activity(business, request.user, 'product.updated',
                    target=service, description=f"{service.name} (service fee) updated")
                messages.success(request, f"{service.name} has been updated.")
                return redirect('service-list', business_slug=business.slug)
    else:
        form = ServiceForm(instance=service, business=business, user=request.user)
        formset = ServiceSessionFormSet(instance=service, prefix='sessions')

    context = {'form': form, 'formset': formset, 'service': service, 'section': 'service'}
    return render(request, 'Product/service_update.html', context)


@login_required(login_url='login')
@permission_required('staff_delete')
@login_required(login_url='login')
@permission_required('staff_delete')
def service_archive(request, business_slug, service_slug, service_id):
    business = get_business_for_user(request.user, business_slug)
    service = get_object_or_404(Product.services, business=business, slug=service_slug, id=service_id)

    if request.method == 'POST':
        service.is_active = False
        service.save(update_fields=['is_active'])
        messages.success(request, f"{service.name} has been removed.")
        if request.headers.get('HX-Request'):
            resp = HttpResponse(status=204)
            resp['HX-Redirect'] = reverse('service-list', kwargs={'business_slug': business.slug})
            return resp
        return redirect('service-list', business_slug=business.slug)

    # GET — modal fragment (htmx) or bounce to list (no-JS, no full page exists)
    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/_confirm_modal.html', {
            'cm_title': service.name,
            'cm_subtitle': f"₱{service.selling_price:.2f} service fee",  # see note below
            'cm_note': "Hidden from your service list · Past sales kept · <strong>Restore anytime</strong>.",
            'cm_action': reverse('service-archive', kwargs={
                'business_slug': business.slug, 'service_slug': service.slug, 'service_id': service.id}),
            'cm_label': "Confirm Archive",
            'cm_btn_icon': 'bi-archive-fill',
            'cm_icon': 'bi bi-ticket-perforated',
        })
    return redirect('service-list', business_slug=business.slug)


@login_required(login_url='login')
@permission_required('owner_only')  # owner
@permission_required('add')  # dev
def archived_services(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    services = Product.all_objects.filter(
        business=business, is_active=False, is_service=True
    ).order_by('-id')
    if request.headers.get('HX-Request'):
        return render(request, 'Product/partials/_archived_services_modal.html', {
            'services': services,
            'business': business,
        })
    return render(request, 'Product/archived_service.html', {
        'services': services,
        'business': business,
        'section': 'service',
    })


@login_required(login_url='login')
@permission_required('owner_only')  # owner
@permission_required('add')  # dev
def restore_service(request, business_slug, service_id):
    business = get_business_for_user(request.user, business_slug)
    service = get_object_or_404(
        Product.all_objects, business=business, id=service_id,
        is_active=False, is_service=True,
    )
    if request.method == 'POST':
        service.is_active = True
        service.save(update_fields=['is_active'])
        log_activity(business, request.user, 'product.restored',
            target=service, description=f"{service.name} (service fee) restored")
        # messages.success(request, f"{service.name} restored.")
        if request.headers.get('HX-Request'):
            services = Product.all_objects.filter(
                business=business, is_active=False, is_service=True
            ).order_by('-id')
            return render(request, 'Product/partials/_archived_services_modal.html', {
                'services': services,
                'business': business,
                'reload_on_close': True,
            })
    return redirect('archived-services', business_slug=business.slug)
