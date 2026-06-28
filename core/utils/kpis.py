from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Sum, F, Q, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.constants import LOW_STOCK_THRESHOLD, NO_STOCK_THRESHOLD, HIGH_STOCK_THRESHOLD, KPI_CACHE_TTL, KPI_BUST_DEBOUNCE

CACHE_TTL = KPI_CACHE_TTL

KPI_PAGES = ('products', 'suppliers', 'inventory', 'sales', 'purchases', 'services')

def bust_kpis(business, pages=None):
    """Invalidate cached KPI 'current' blocks for a business.
    Debounced per page so a burst of writes = one bust per KPI_BUST_DEBOUNCE.
    Only clears the cached 'current' dict — the daily snapshot used for
    vs-yesterday deltas is untouched."""
    if business is None:
        return
    today = timezone.localdate().isoformat()
    for page in (pages or KPI_PAGES):
        cache_key = f'kpis:{page}:{business.id}:{today}'
        throttle_key = f'{cache_key}:bust_throttle'
        if cache.add(throttle_key, True, timeout=KPI_BUST_DEBOUNCE):
            cache.delete(cache_key)


# ─── PRODUCTS ───────────────────────────────────────────────────────────────

def compute_product_kpis(business, as_of=None):
    """
    Live KPI computation for the Products page.
    Counts active (non-archived) products only.
    """
    from Product.models import Product
    from Sales.models import SaleItem

    today = as_of or timezone.localdate()
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    # same-pace window: first N days of last month, N = today's day-of-month
    last_month_same_pace_end = min(
        last_month_start + timedelta(days=today.day - 1),
        last_month_end,
    )

    qs = Product.objects.filter(business=business, is_active=True, is_service=False)

    total = qs.count()
    in_stock = qs.filter(prepared_quantity__gte=F('high_stock_threshold')).count()
    low_stock = qs.filter(
        prepared_quantity__lte=F('low_stock_threshold'),
        prepared_quantity__gte=1,
    ).count()
    out_of_stock = qs.filter(prepared_quantity=NO_STOCK_THRESHOLD).count()

    inventory_value = qs.aggregate(
        total=Coalesce(
            Sum(F('cost_price') * F('prepared_quantity'),
                output_field=DecimalField(max_digits=14, decimal_places=2)),
            Decimal('0'),
        )
    )['total']

    # ─── Sales velocity (units sold) ───
    sale_items = SaleItem.objects.filter(sale__business=business, product__isnull=False, product__is_service=False)

    def _units(items):
        return items.aggregate(u=Coalesce(Sum('quantity'), 0))['u']

    units_sold_month = _units(sale_items.filter(sale__date__gte=month_start))
    units_sold_last_pace = _units(sale_items.filter(
        sale__date__gte=last_month_start, sale__date__lte=last_month_same_pace_end))
    units_sold_all = _units(sale_items)

    def _top(items, n=10):
        rows = (items.values('product__name')
                     .annotate(units=Sum('quantity'))
                     .order_by('-units')[:n])
        return [{'name': r['product__name'], 'units': r['units']} for r in rows]

    top_items_month = _top(sale_items.filter(sale__date__gte=month_start))
    top_items_all = _top(sale_items)
    top_items_has_more = max(len(top_items_month), len(top_items_all)) > 3

    never_sold = qs.annotate(
        u=Coalesce(Sum('sale_items__quantity'), 0)
    ).filter(u=0).count()

    return {
        'total': total,
        'in_stock': in_stock,
        'low_stock': low_stock,
        'out_of_stock': out_of_stock,
        'inventory_value': str(inventory_value),
        # velocity
        'units_sold_month': units_sold_month,
        'units_sold_delta': units_sold_month - units_sold_last_pace,
        'units_sold_all': units_sold_all,
        'top_items_month': top_items_month,
        'top_items_all': top_items_all,
        'top_items_has_more': top_items_has_more,
        'never_sold': never_sold,
        'computed_at': timezone.now(),
    }

    

def get_product_kpis(business, as_of=None):
    """
    Today's product KPIs (24h cache) + 'vs yesterday' delta from snapshot.
    Returns {'current': {...}, 'deltas': {...}}.
    """
    today = timezone.localdate()
    cache_key = f'kpis:products:{business.id}:{today.isoformat()}'

    current = cache.get(cache_key)
    if current is None:
        current = compute_product_kpis(business)
        cache.set(cache_key, current, timeout=CACHE_TTL)

    yesterday = today - timedelta(days=1)
    deltas = _compute_deltas(business, page='products', yesterday=yesterday, current=current)
    
    return {
        'current': current,
        'deltas': deltas,
    }
    
# ─── SERVICES ────────────────────────────────────────────────────────────────

def compute_service_kpis(business, as_of=None):
    """Live KPI computation for the Service Fees page (no vs-yesterday delta)."""
    from Product.models import Product
    from Sales.models import SaleItem

    today = as_of or timezone.localdate()
    month_start = today.replace(day=1)

    service_items = SaleItem.objects.filter(
        sale__business=business, product__isnull=False, product__is_service=True
    )

    units_sold_all = service_items.aggregate(u=Coalesce(Sum('quantity'), 0))['u']

    def _top(items, n=10):
        rows = (items.values('product__name')
                     .annotate(units=Sum('quantity'))
                     .order_by('-units')[:n])
        return [{'name': r['product__name'], 'units': r['units']} for r in rows]

    # collapser only appears once there are 11+ services with sales
    distinct_sold = service_items.values('product_id').distinct().count()

    return {
        'units_sold_all': units_sold_all,
        'top_services_month': _top(service_items.filter(sale__date__gte=month_start)),
        'top_services_all': _top(service_items),
        'top_services_has_more': distinct_sold > 10,
        'services_total': Product.services.filter(business=business).count(),
        'computed_at': timezone.now(),
    }


def get_service_kpis(business):
    """Cached service KPIs (TTL backstop + bust-on-write). No delta block."""
    today = timezone.localdate()
    cache_key = f'kpis:services:{business.id}:{today.isoformat()}'

    current = cache.get(cache_key)
    if current is None:
        current = compute_service_kpis(business)
        cache.set(cache_key, current, timeout=CACHE_TTL)

    return {'current': current}
    
# ─── SUPPLIERS ───────────────────────────────────────────────────────────────

def compute_supplier_kpis(business, as_of=None):
    from Supplier.models import Supplier
    from Expense.models import Purchase

    today = as_of or timezone.localdate()
    month_start = today.replace(day=1)

    suppliers = Supplier.objects.filter(business=business)  # ActiveManager hides inactive
    purchases_month = Purchase.objects.filter(
        business=business, purchase_date__gte=month_start
    )

    total = suppliers.count()
    on_hold = suppliers.filter(status='on_hold').count()
    purchases_count_month = purchases_month.count()
    total_spend_month = purchases_month.aggregate(
        total=Coalesce(Sum('total_cost'),
                       Decimal('0'),
                       output_field=DecimalField(max_digits=14, decimal_places=2))
    )['total']

    return {
        'total': total,
        'on_hold': on_hold,
        'purchases_count_month': purchases_count_month,
        'total_spend_month': str(total_spend_month),
        'computed_at': timezone.now(),
    }


def get_supplier_kpis(business):
    today = timezone.localdate()
    cache_key = f'kpis:suppliers:{business.id}:{today.isoformat()}'

    current = cache.get(cache_key)
    if current is None:
        current = compute_supplier_kpis(business)
        cache.set(cache_key, current, timeout=CACHE_TTL)

    deltas = _compute_deltas(business, page='suppliers',
                             yesterday=today - timedelta(days=1), current=current)
    return {'current': current, 'deltas': deltas}
    
# ─── INVENTORY (Stocks) ───────────────────────────────────────────────────────
    
def compute_inventory_kpis(business, as_of=None):
    from Inventory.models import Stock

    qs = Stock.objects.filter(business=business).exclude(material__status='inactive')

    total = qs.count()
    low_stock = qs.filter(
        quantity__lte=LOW_STOCK_THRESHOLD, quantity__gte=1
    ).count()
    out_of_stock = qs.filter(quantity=NO_STOCK_THRESHOLD).count()
    in_stock = qs.filter(quantity__gt=LOW_STOCK_THRESHOLD).count()


    total_value = qs.aggregate(
        total=Coalesce(
            Sum(F('price') * F('quantity'),
                output_field=DecimalField(max_digits=14, decimal_places=2)),
            Decimal('0'),
        )
    )['total']

    return {
        'total': total,
        'low_stock': low_stock,
        'in_stock': in_stock,
        'out_of_stock': out_of_stock,
        'total_value': str(total_value),
        'computed_at': timezone.now(),
    }


def get_inventory_kpis(business):
    today = timezone.localdate()
    cache_key = f'kpis:inventory:{business.id}:{today.isoformat()}'

    current = cache.get(cache_key)
    if current is None:
        current = compute_inventory_kpis(business)
        cache.set(cache_key, current, timeout=CACHE_TTL)

    deltas = _compute_deltas(business, page='inventory',
                             yesterday=today - timedelta(days=1), current=current)
    return {'current': current, 'deltas': deltas}


# ─── SALES ─────────────────────────────────────────────────────────────────────

def compute_sale_kpis(business, as_of=None):
    from Sales.models import Sale

    today = as_of or timezone.localdate()
    yesterday = today - timedelta(days=1)

    this_week_start = today - timedelta(days=today.weekday())
    last_week_end   = this_week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)

    this_month_start = today.replace(day=1)
    last_month_end   = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    def _revenue(filters):
        return Sale.objects.active().filter(business=business, **filters).aggregate(
            t=Coalesce(Sum('total_revenue'),
                       Decimal('0'),
                       output_field=DecimalField(max_digits=14, decimal_places=2))
        )['t']

    def _count(filters):
        return Sale.objects.active().filter(business=business, **filters).count()

    return {
        'count_today':         _count({'date': today}),
        'revenue_today':       str(_revenue({'date': today})),
        'revenue_yesterday':   str(_revenue({'date': yesterday})),
        'revenue_week':        str(_revenue({'date__gte': this_week_start})),
        'revenue_last_week':   str(_revenue({'date__range': (last_week_start, last_week_end)})),
        'count_month':         _count({'date__gte': this_month_start}),
        'revenue_month':       str(_revenue({'date__gte': this_month_start})),
        'revenue_last_month':  str(_revenue({'date__range': (last_month_start, last_month_end)})),
        'computed_at': timezone.now(),
    }


def get_sale_kpis(business):
    today = timezone.localdate()
    cache_key = f'kpis:sales:{business.id}:{today.isoformat()}'

    current = cache.get(cache_key)
    if current is None:
        current = compute_sale_kpis(business)
        cache.set(cache_key, current, timeout=CACHE_TTL)

    deltas = _compute_deltas(business, page='sales',
                             yesterday=today - timedelta(days=1), current=current)
    return {'current': current, 'deltas': deltas}

# ─── PURCHASES ─────────────────────────────────────────────────────────────────

def compute_purchase_kpis(business, as_of=None):
    from Expense.models import Purchase

    today = as_of or timezone.localdate()
    yesterday = today - timedelta(days=1)

    this_week_start = today - timedelta(days=today.weekday())
    last_week_end   = this_week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)

    this_month_start = today.replace(day=1)
    last_month_end   = this_month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    def _cost(filters):
        return Purchase.objects.filter(business=business, **filters).aggregate(
            t=Coalesce(Sum('total_cost'),
                       Decimal('0'),
                       output_field=DecimalField(max_digits=14, decimal_places=2))
        )['t']

    def _count(filters):
        return Purchase.objects.filter(business=business, **filters).count()

    return {
        'count_today':      _count({'purchase_date': today}),
        'cost_today':       str(_cost({'purchase_date': today})),
        'cost_yesterday':   str(_cost({'purchase_date': yesterday})),
        'cost_week':        str(_cost({'purchase_date__gte': this_week_start})),
        'cost_last_week':   str(_cost({'purchase_date__range': (last_week_start, last_week_end)})),
        'count_month':      _count({'purchase_date__gte': this_month_start}),
        'cost_month':       str(_cost({'purchase_date__gte': this_month_start})),
        'cost_last_month':  str(_cost({'purchase_date__range': (last_month_start, last_month_end)})),
        'computed_at': timezone.now(),
    }


def get_purchase_kpis(business):
    today = timezone.localdate()
    cache_key = f'kpis:purchases:{business.id}:{today.isoformat()}'

    current = cache.get(cache_key)
    if current is None:
        current = compute_purchase_kpis(business)
        cache.set(cache_key, current, timeout=CACHE_TTL)

    deltas = _compute_deltas(business, page='purchases',
                             yesterday=today - timedelta(days=1), current=current)
    return {'current': current, 'deltas': deltas}
    
# ─── SHARED HELPER ─────────────────────────────────────────────────

def _compute_deltas(business, page, yesterday, current):
    """
    Look up yesterday's snapshot and return per-metric numeric deltas.
    If no snapshot exists (first day), all deltas are None.
    """
    from core.models import KpiSnapshot

    snap = KpiSnapshot.objects.filter(
        business=business, page=page, date=yesterday
    ).first()

    if not snap:
        return {k: None for k in current}

    deltas = {}
    for key, value in current.items():
        old = snap.metrics.get(key)
        if old is None:
            deltas[key] = None
            continue
        # Handle numeric strings (Decimal serialized as str)
        try:
            deltas[key] = float(value) - float(old)
        except (TypeError, ValueError):
            deltas[key] = None
    return deltas
        