from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db.models import Sum, F, Q, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone

from core.constants import LOW_STOCK_THRESHOLD, NO_STOCK_THRESHOLD

CACHE_TTL = 60 * 60 * 24  # 24 hours

# ─── PRODUCTS ───────────────────────────────────────────────────────────────

def compute_product_kpis(business, as_of=None):
    """
    Live KPI computation for the Products page.
    Counts active (non-archived) products only.
    """
    from Product.models import Product

    qs = Product.objects.filter(business=business, is_active=True)

    total = qs.count()
    low_stock = qs.filter(
        prepared_quantity__lte=LOW_STOCK_THRESHOLD,
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
    
    return {
        'total': total,
        'low_stock': low_stock,
        'out_of_stock': out_of_stock,
        'inventory_value': str(inventory_value),
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
        'out_of_stock': out_of_stock,
        'total_value': str(total_value),
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
        return Sale.objects.filter(business=business, **filters).aggregate(
            t=Coalesce(Sum('total_revenue'),
                       Decimal('0'),
                       output_field=DecimalField(max_digits=14, decimal_places=2))
        )['t']

    def _count(filters):
        return Sale.objects.filter(business=business, **filters).count()

    return {
        'count_today':         _count({'date': today}),
        'revenue_today':       str(_revenue({'date': today})),
        'revenue_yesterday':   str(_revenue({'date': yesterday})),
        'revenue_week':        str(_revenue({'date__gte': this_week_start})),
        'revenue_last_week':   str(_revenue({'date__range': (last_week_start, last_week_end)})),
        'count_month':         _count({'date__gte': this_month_start}),
        'revenue_month':       str(_revenue({'date__gte': this_month_start})),
        'revenue_last_month':  str(_revenue({'date__range': (last_month_start, last_month_end)})),
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
        