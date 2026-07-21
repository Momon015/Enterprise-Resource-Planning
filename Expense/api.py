
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

from Supplier.models import Material
from core.utils.owner import get_business_for_user
from .views import _normalize_cart_discount_mode

"""JSON endpoints for the React purchase-cart island (plain JsonResponse, not DRF)."""

def _serialize_cart(request, business):
    """Whole purchase cart as JSON. Runs the discount-mode normalizer first
    so per-item vs whole-order % stays consistent."""
    _normalize_cart_discount_mode(request, business)
    cart = request.session.get('cart', {})

    items = []
    subtotal = Decimal('0')
    total_discount = Decimal('0')

    for material_id, data in cart.items():
        try:
            material = Material.objects.get(business=business, id=material_id)
        except Material.DoesNotExist:
            continue

        quantity = int(data.get('quantity', 1) or 1)
        price = Decimal(str(data.get('price') or '0'))
        discount = Decimal(str(data.get('discount') or '0'))
        item_total = price * quantity
        item_discount = item_total - discount
        subtotal += item_total
        total_discount += discount

        linked = material.products.first()
        image = linked.image.url if linked and linked.image else ''

        items.append({
            'id': material.id,
            'material': material.name,
            'supplier': material.supplier.name if material.supplier else 'No supplier',
            'image': image,
            'price': f'{price:.2f}',
            'quantity': quantity,
            'item_total': f'{item_total:.2f}',
            'discount': f'{discount:.2f}',
            'item_discount': f'{item_discount:.2f}',
            'stock': material.quantity,          # qty cap
        })

    percent = Decimal('0')
    if business.enable_purchase_discount:
        percent = Decimal(request.session.get('purchase_discount_percent', '0') or '0')

    return {
        'items': items,
        'item_count': len(items),
        'subtotal': f'{subtotal:.2f}',
        'total_discount': f'{total_discount:.2f}',
        'total_after_discount': f'{max(subtotal - total_discount, Decimal("0")):.2f}',
        'discount_enabled': bool(business.enable_purchase_discount),  # True = % mode
        'purchase_discount_percent': f'{percent:.2f}',
    }


@login_required(login_url='login')
def cart_state(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    return JsonResponse(_serialize_cart(request, business))


@login_required(login_url='login')
def cart_search(request, business_slug):
    """Typeahead for the purchase-search island.

    Materials only — a purchase is materials-from-suppliers, so only materials are
    addable and there is nothing else worth showing here. (Sell-side products used to
    ride along "for reference", but they only added noise to a materials-only action.)
    Empty q lists the first page so the dropdown has something on focus.

    On an EMPTY query (the cashier just clicked the box) we return the 5 MOST-PURCHASED
    materials rather than the first 5 alphabetically — with 30–50 materials a full list
    is a wall to scroll, and the things you buy most are the things you're most likely
    restocking. A typed query searches the full catalogue (capped at 10).

    "Most purchased" is ranked by how many non-void purchase lines reference the material,
    i.e. how OFTEN it's been bought — a recurring weekly restock outranks a one-off bulk
    buy. A business with no history just gets its materials name-ordered (all rank 0), so
    the dropdown is never empty on focus.

    Each row carries `in_cart`, the quantity already sitting in the purchase cart, so
    the cashier can see what they've set without leaving the search. The island
    re-fetches on `cart:changed`, so this stays live as items are added.
    """
    business = get_business_for_user(request.user, business_slug)
    q = (request.GET.get('q') or '').strip()

    # Session cart is keyed by material_id (as a string), same shape _serialize_cart reads.
    cart = request.session.get('cart', {})

    materials = Material.objects.filter(business=business).select_related('supplier')
    if q:
        materials = materials.filter(
            Q(name__icontains=q) | Q(supplier__name__icontains=q)
        ).order_by('name')[:10]
    else:
        # Rank by purchase frequency; voided purchases don't count as a real buy.
        materials = materials.annotate(
            purchase_count=Count('items', filter=Q(items__purchase__is_void=False)),
        ).order_by('-purchase_count', 'name')[:5]

    mats = [{
        'id': m.id,
        'name': m.name,
        'supplier': m.supplier.name if m.supplier else 'No supplier',
        'price': f'{m.price:.2f}',      # unit COST
        # `Material.quantity` is a REFERENCE quantity (how the item is defined — e.g. 24 per
        # Box), NOT live inventory stock. Shown so the buyer has that reference while
        # deciding how many to order. Never label it "in stock".
        'qty': m.quantity,
        'unit': m.get_unit_display(),
        'in_cart': int(cart.get(str(m.id), {}).get('quantity', 0) or 0),
    } for m in materials]

    # `suggested` tells the island to label this a "Most purchased" shortlist, not a search.
    return JsonResponse({'materials': mats, 'suggested': not q})


@login_required(login_url='login')
@require_POST
def cart_add(request, business_slug):
    """Add ONE material to the purchase cart from the search island.

    Mirrors views.add_to_cart's session shape exactly (so the rest of the checkout
    flow reads the same keys) and returns the serialized cart. The island dispatches
    `cart:changed` on success so the sibling purchase-cart island re-reads its state.
    """
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, business=business, id=request.POST.get('material_id'))
    key = str(material.id)

    warning = None
    if material.quantity < 1:
        warning = f'{material.name} — out of stock.'
    elif key in cart:
        if cart[key]['quantity'] < material.quantity:
            cart[key]['quantity'] += 1
        else:
            warning = f'{material.name} — only {material.quantity} available.'
    else:
        cart[key] = {
            'supplier': material.supplier.name if material.supplier else 'No supplier',
            'id': material.id,
            'slug': material.slug,
            'name': material.name,
            'price': float(material.price),
            'quantity': 1,
            'discount': str(0),
        }

    request.session['cart'] = cart
    request.session.modified = True
    payload = _serialize_cart(request, business)
    payload['added'] = material.name
    if warning:
        payload['warning'] = warning
    return JsonResponse(payload)


@login_required(login_url='login')
@require_POST
def cart_set_qty(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material_id = str(request.POST.get('material_id', ''))
    if material_id not in cart:
        return JsonResponse({'error': 'not_in_cart'}, status=404)

    material = get_object_or_404(Material, business=business, id=material_id)
    try:
        quantity = int(request.POST.get('quantity', 1))
    except (TypeError, ValueError):
        quantity = 1
    if quantity < 1:
        quantity = 1

    warning = None
    if material.quantity >= quantity:
        cart[material_id]['quantity'] = quantity
    else:
        warning = f'{material.name} — only {material.quantity} available.'

    request.session['cart'] = cart
    request.session.modified = True
    payload = _serialize_cart(request, business)
    if warning:
        payload['warning'] = warning
    return JsonResponse(payload)


@login_required(login_url='login')
@require_POST
def cart_set_line(request, business_slug):
    """Set a line's total price (→ unit price) and/or its flat discount."""
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material_id = str(request.POST.get('material_id', ''))
    if material_id not in cart:
        return JsonResponse({'error': 'not_in_cart'}, status=404)

    get_object_or_404(Material, business=business, id=material_id)
    quantity = int(cart[material_id].get('quantity', 1) or 1)

    try:
        raw_total = request.POST.get('total_price')
    except (TypeError, InvalidOperation):
        pass
    if raw_total not in (None, ''):
        cart[material_id]['price'] = str(Decimal(raw_total) / quantity)

    # flat discount only applies when NOT in % mode
    if not business.enable_purchase_discount:
        try:
            raw_discount = request.POST.get('discount')
        except (TypeError, InvalidOperation):
            pass
        if raw_discount not in (None, ''):
            cart[material_id]['discount'] = str(Decimal(raw_discount))

    request.session['cart'] = cart
    request.session.modified = True
    return JsonResponse(_serialize_cart(request, business))


@login_required(login_url='login')
@require_POST
def cart_remove(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material_id = str(request.POST.get('material_id', ''))
    if material_id in cart:
        del cart[material_id]
    request.session['cart'] = cart
    request.session.modified = True
    return JsonResponse(_serialize_cart(request, business))


@login_required(login_url='login')
@require_POST
def cart_clear(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    request.session['cart'] = {}
    request.session.modified = True
    return JsonResponse(_serialize_cart(request, business))
