# Sales/api.py
"""JSON endpoints for the React sale-cart island (plain JsonResponse, not DRF)."""
from decimal import Decimal

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

    