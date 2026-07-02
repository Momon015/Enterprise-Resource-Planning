
from decimal import Decimal

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

    raw_total = request.POST.get('total_price')
    if raw_total not in (None, ''):
        cart[material_id]['price'] = str(Decimal(raw_total) / quantity)

    # flat discount only applies when NOT in % mode
    if not business.enable_purchase_discount:
        raw_discount = request.POST.get('discount')
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
