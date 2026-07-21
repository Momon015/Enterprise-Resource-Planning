# Sales/api.py
"""JSON endpoints for the React sale-cart island (plain JsonResponse, not DRF)."""
from decimal import Decimal

from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

from Product.models import Product
from core.utils.owner import get_business_for_user

def _serialize_cart(request, business):
    """Build the cart's current state as a plain dict (JSON-ready)."""
    sale = request.session.get('sale', {})
    items = []
    subtotal = Decimal('0')
    
    for product_id, data in sale.items():
        try:
            product = Product.objects.get(business=business, id=product_id)
        except Product.DoesNotExist:
            continue # stale id in session — skip it
        
        quantity = int(data.get('quantity' or 1) or 1)
        selling_price = Decimal(str(data.get('selling_price') or '0'))
        cost_price = Decimal(str(data.get('cost_price') or '0'))
        line_total = selling_price * quantity
        subtotal += line_total
        
        supplier = ''
        if product.material and product.material.supplier:
            supplier = product.material.supplier.name
            
        items.append({
            'id': product.id,
            'name': product.name,
            'supplier': supplier,
            'image': product.image.url if product.image else '',
            'selling_price': f'{selling_price:.2f}',
            'cost_price': f'{cost_price:.2f}',
            'quantity': quantity,
            'line_total': f'{line_total:.2f}',
            'stock': product.prepared_quantity,
            'is_service': product.is_service,
        })

    return {
        'items': items,
        'item_count': len(items),
        'subtotal': f'{subtotal:.2f}',
        'discount_percent': str(request.session.get('sale_discount_percent', 0) or 0),
        'discount_enabled': bool(business.enable_sale_discount),
        # The statutory customer has to come back too. Without these, clicking Edit on the
        # summary returned to a cart that had forgotten the senior — it showed "Regular
        # customer" while the server still held the SC type, so the screen and the pending
        # sale disagreed about who was being served.
        'discount_type': request.session.get('sale_discount_type', '') or '',
        'discount_id_no': request.session.get('sale_discount_id_no', '') or '',
        'discount_name': request.session.get('sale_discount_name', '') or '',
    }
    
@login_required(login_url='login')
def cart_state(request, business_slug):
    """GET → current cart as JSON (React loads this on mount)."""
    business = get_business_for_user(request.user, business_slug)

    return JsonResponse(_serialize_cart(request, business))


@login_required(login_url='login')
def sale_search(request, business_slug):
    """Typeahead for the sale-search island — the sale-side twin of Expense.cart_search.

    On an EMPTY query (the cashier just clicked the box) we return a BEST-SELLERS
    shortlist rather than the first N alphabetically — with a big catalogue a full list
    is a wall, and the things you sell most are what you're most likely ringing up.
    A typed query searches the whole catalogue (goods + services, capped at 10).

    The empty shortlist splits by kind so services don't get buried under fast-moving
    goods: **top 3 goods + top 3 services = 6**. When the shop sells no services — none
    exist, OR the owner switched Service Fees OFF (`offers_services`) — those 3 slots
    would be dead, so goods take the whole shortlist (**top 5**) instead. With the toggle
    off, services are excluded from the typed search too: a hidden feature must not still
    be sellable through the box.

    "Best sellers" is ranked by how many completed, non-void sale lines reference the
    product. A shop with no sales yet just gets its catalogue name-ordered (all rank 0),
    so the dropdown is never empty on focus.

    Each row carries `in_cart` (qty already in the sale) and, unlike the materials side,
    a real `image` when the product has one — falling back to initials in the island.
    """
    business = get_business_for_user(request.user, business_slug)
    q = (request.GET.get('q') or '').strip()

    # Session cart is keyed by product_id (as a string), same shape _serialize_cart reads.
    sale = request.session.get('sale', {})

    def row(p):
        return {
            'id': p.id,
            'name': p.name,
            'supplier': p.material.supplier.name if p.material and p.material.supplier else '',
            'price': f'{p.selling_price:.2f}',
            'image': p.image.url if p.image else '',
            'stock': p.prepared_quantity,
            'is_service': p.is_service,
            'in_cart': int(sale.get(str(p.id), {}).get('quantity', 0) or 0),
        }

    def by_sales(manager):
        # Rank by sales frequency; drafts and voids aren't real sales.
        return manager.filter(business=business).select_related(
            'material', 'material__supplier',
        ).annotate(sold_count=Count('sale_items', filter=Q(
            sale_items__sale__is_void=False,
            sale_items__sale__status='completed',
        ))).order_by('-sold_count', 'name')

    # Service Fees off → services are hidden everywhere they'd otherwise be sellable here.
    services_on = bool(business.offers_services)

    if q:
        # Typed: goods + services when the feature is on, goods only when it's off.
        catalogue = Product.objects if services_on else Product.goods
        products = catalogue.filter(
            business=business, name__icontains=q,
        ).select_related('material', 'material__supplier').order_by('name')[:10]
        return JsonResponse({'products': [row(p) for p in products],
                             'services': [], 'suggested': False})

    # Empty: top-3 services beside top-3 goods — but if the shop has no services (none
    # exist, or the toggle is off), give goods all 5 slots rather than leave three empty.
    services = [row(p) for p in by_sales(Product.services)[:3]] if services_on else []
    goods_limit = 3 if services else 5
    goods = [row(p) for p in by_sales(Product.goods)[:goods_limit]]
    return JsonResponse({'products': goods, 'services': services, 'suggested': True})


@login_required(login_url='login')
@require_POST
def sale_add(request, business_slug):
    """Add ONE product to the sale cart from the search island.

    Mirrors views.add_to_sales' session shape and stock rules so the rest of checkout
    reads the same keys. Session-based rentals need a time-block picked, which a one-tap
    add can't express — same as the topbar search, we refuse them with a warning rather
    than adding an untiered line. The island dispatches `cart:changed` on success so the
    sibling sale-cart island re-reads.
    """
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=request.POST.get('product_id'))
    key = str(product.id)

    warning = None
    if product.is_service and not business.offers_services:
        # Defense-in-depth: the search already hides services when the toggle is off, but a
        # hand-crafted POST would otherwise still add one. A disabled feature must not be
        # sellable through any door.
        warning = f"{product.name} — Service Fees are turned off."
    elif product.is_locked:
        warning = f"{product.name} is locked — upgrade your plan or unlock it to sell."
    elif product.is_session_based:
        warning = f"{product.name} is a session rental — open it to pick a time block."
    elif product.is_service or product.prepared_quantity >= 1:
        if key in sale:
            if product.is_service or sale[key]['quantity'] < product.prepared_quantity:
                sale[key]['quantity'] += 1
            else:
                warning = f"{product.name} — only {product.prepared_quantity} in stock."
        else:
            sale[key] = {
                'id': product.id,
                'name': product.name,
                'quantity': 1,
                'cost_price': str(product.cost_price),
                'selling_price': str(product.selling_price),
            }
    else:
        warning = f"{product.name} — out of stock."

    request.session['sale'] = sale
    request.session.modified = True
    payload = _serialize_cart(request, business)
    payload['added'] = product.name
    if warning:
        payload['warning'] = warning
    return JsonResponse(payload)


@login_required(login_url='login')
@require_POST
def cart_set_qty(request, business_slug):
    """POST product_id + quantity → update qty (stock-checked)."""
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})
    product_id = str(request.POST.get('product_id', ''))
    if product_id not in sale:
        return JsonResponse({'error': 'not_in_cart'}, status=404)

    product = get_object_or_404(Product, business=business, id=product_id)
    try:
        new_quantity = int(request.POST.get('quantity', 1))
    except (TypeError, ValueError):
        new_quantity = 1
    if new_quantity < 1:
        new_quantity = 1

    warning = None
    if product.is_service or product.prepared_quantity >= new_quantity:
        sale[product_id]['quantity'] = new_quantity
    else:
        warning = f'{product.name} — only {product.prepared_quantity} in stock.'

    request.session['sale'] = sale
    request.session.modified = True

    payload = _serialize_cart(request, business)
    if warning:
        payload['warning'] = warning
    return JsonResponse(payload)


@login_required(login_url='login')
@require_POST
def cart_set_price(request, business_slug):
    """POST product_id + total_price → set LINE total (÷ qty = unit price)."""
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})
    product_id = str(request.POST.get('product_id', ''))
    if product_id not in sale:
        return JsonResponse({'error': 'not_in_cart'}, status=404)

    get_object_or_404(Product, business=business, id=product_id)
    quantity = int(sale[product_id].get('quantity', 1) or 1)
    raw_total = request.POST.get('total_price')
    if raw_total:
        sale[product_id]['selling_price'] = str(Decimal(raw_total) / quantity)

    request.session['sale'] = sale
    request.session.modified = True
    return JsonResponse(_serialize_cart(request, business))  

@login_required(login_url='login')
@require_POST
def cart_remove(request, business_slug):
    """POST product_id → remove one line."""
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})
    product_id = str(request.POST.get('product_id', ''))
    if product_id in sale:
        del sale[product_id]
        
    request.session['sale'] = sale
    request.session.modified = True
    return JsonResponse(_serialize_cart(request, business))


@login_required(login_url='login')
@require_POST
def cart_clear(request, business_slug):
    """POST → empty the cart."""
    business = get_business_for_user(request.user, business_slug)
    
    request.session['sale'] = {}
    request.session.modified = True
    return JsonResponse(_serialize_cart(request, business))

    