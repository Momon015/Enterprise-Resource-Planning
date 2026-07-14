from activity.models import ActivityEvent
from django.db.models import Q, F, Count
from django.urls import reverse


def attention_items(business):
    """PINNED bell items — things that are TRUE RIGHT NOW, not things that happened.

    Deliberately NOT an ActivityEvent and NOT cached:
      • not an event  — an event goes stale ("3 awaiting" is a lie the moment one is
                        confirmed) and can be marked read while still true. State can't.
      • not cached    — measured at 1.7ms worst case (100 businesses x 300 products,
                        150 of them out of stock). A cache would save under a
                        millisecond and buy a way for the panel to lie. Revisit only
                        if ONE business's catalog passes ~10k products.

    COUNTS ONLY — never enumerate rows. A 150-product outage must stay ONE line,
    or it buries the feed underneath it (which is the thing this panel exists to stop).

    Each row is a breadcrumb — ENTITY • STATE ("3 Products • Out of Stock") — and wears
    its destination's SIDEBAR icon (basket=Products, boxes=Stock, box-seam=Materials), so
    the row tells you where it lands before you click. Retail/pharmacy is 1:1 (products
    only); phase 2 (cafe/restaurant) adds material rows beside them and the entity segment
    is what keeps "5 Products • Out of Stock" and "2 Materials • Out of Stock" legible
    stacked together.
    """
    from Sales.models import Sale
    from Product.models import Product, CRITICAL_BAND_Q, LOW_BAND_Q, with_stock_bands

    slug = business.slug
    items = []

    def row(icon, tone, count, noun, state, sub, text, url):
        """One item, TWO renderings — the bell shows the breadcrumb (entity • state),
        the dashboard panel shows the sentence (text). Same source, so the two
        surfaces can never drift into telling different stories."""
        items.append({
            'icon':   icon,
            'tone':   tone,
            'entity': f"{count} {noun}{'' if count == 1 else 's'}",
            'state':  state,
            'sub':    sub,
            'text':   text,
            'url':    url,
        })

    def be(n):
        return 'is' if n == 1 else 'are'

    pending = Sale.objects.filter(business=business, status='pending').count()
    if pending:
        row('bi-hourglass-split', 'warning', pending, 'Sale', 'Awaiting Payment',
            'Confirm after payment is received',
            f"{pending} sale{'' if pending == 1 else 's'} {be(pending)} awaiting payment "
            f"— confirm after payment is received",
            reverse('sale-draft-list', kwargs={'business_slug': slug}))

    # ── PRODUCTS (retail/pharmacy = the only stock there is) ──
    # ONE conditional aggregate, not three counts — same table, same rows, so scanning
    # it once and tallying three buckets beats three separate scans (measured 2.3ms/1q
    # vs 3.0ms/3q). Each bucket mirrors a product_list ?stock= filter EXACTLY, so every
    # number here lands on precisely that many rows when clicked.
    #
    #   out      → ?stock=none      qty = 0
    #   critical → ?stock=critical  1 .. max(1, round(low * 0.2))
    #   low      → ?stock=low       crit+1 .. low_stock_threshold
    #
    # The three bands are DISJOINT (Product/models.py) — a critically-low product is NOT
    # also counted as Running Low. Severity order below: Out → Critically Low → Running Low.
    product_url = reverse('product-list', kwargs={'business_slug': slug})
    stock = with_stock_bands(Product.goods.filter(business=business)).aggregate(
        out=Count('pk', filter=Q(prepared_quantity=0)),
        critical=Count('pk', filter=CRITICAL_BAND_Q),
        low=Count('pk', filter=LOW_BAND_Q),
    )

    # ICONS MIRROR THE DASHBOARD STOCK CARDS — a row here and the KPI card it lands on must
    # wear the same face, or the bell looks like it's reporting something else. The ladder is
    # a severity ladder, not decoration:
    #   out      → bi-slash-circle       (nothing left)
    #   critical → bi-exclamation-octagon (stop sign — the sharpest shape we use)
    #   low      → bi-exclamation-triangle (a nudge; the SILVER hue keeps it quiet)
    # bi-basket used to be on all three. It's the PRODUCTS icon (navbar, product list), so it
    # said "this is about products" — which the row's own text already says — while saying
    # nothing about how bad it is. Basket now means goods, not alarm.
    out_of_stock = stock['out']
    if out_of_stock:
        row('bi-slash-circle', 'danger', out_of_stock, 'Product', 'Out of Stock',
            'Restock to avoid missed sales',
            f"{out_of_stock} product{'' if out_of_stock == 1 else 's'} {be(out_of_stock)} "
            f"out of stock — restock to avoid missed sales",
            product_url + '?stock=none')

    critical = stock['critical']
    if critical:
        # amber — the only warm step between red (out) and silver (low). Tried orange
        # and yellow here first; both are <10 degrees from amber and read identically at 32px.
        row('bi-exclamation-octagon', 'warning', critical, 'Product', 'Critically Low',
            'Almost gone — restock now',
            f"{critical} product{'' if critical == 1 else 's'} {be(critical)} "
            f"critically low — restock now",
            product_url + '?stock=critical')

    low_only = stock['low']
    if low_only:
        # SILVER, not warm — "running low" is a nudge, not an alarm. Colouring it warm
        # implies an urgency it doesn't have and steals the eye from the rows above.
        row('bi-exclamation-triangle', 'neutral', low_only, 'Product', 'Running Low',
            'Reorder soon',
            f"{low_only} product{'' if low_only == 1 else 's'} {be(low_only)} running low on stock",
            product_url + '?stock=low')

    # ── PHASE 2 — cafe/restaurant also track raw MATERIALS (Inventory Stock).
    #    Same two rows, `bi-boxes`, gated on business_type in ('cafe','restaurant')
    #    — mirrors log_stock_threshold_events in activity/signals.py. Until this
    #    lands, material stock.out events must STAY in the event feed or cafes
    #    lose the alert entirely.

    return items


def scope_events_for_user(qs, user):
    """
    Staff see: their own events + stock alerts (low/out).
    Owners/dev: see all.
    """
    if user.role == 'staff':
        return qs.filter(
            Q(actor=user) |
            Q(actor__isnull=True, verb__in=['stock.low', 'stock.out'])
        )
    return qs

def log_activity(business, actor, verb, target=None, description='',
                 metadata=None, important=False):
    """
    Single entry point for logging activities.
    Always called explicitly from views (not signals) so we control wording + actor.
    """
    
    return ActivityEvent.objects.create(
        business=business,
        actor=actor,
        verb=verb,
        target=target,
        description=description,
        metadata=metadata or {},
        is_important=important,
    )
    
def summarize_items(items, *, qty_attr='quantity', name_attr='name', max_show=1, prefix='+', sign_for=None):
    """
    Build '+5 Coke, +1 more' for activity descriptions.

    If sign_for callable is given, it overrides `prefix` per item:
      sign_for(item) -> '+' or '-'
    Use case: sale returns where some items are sellable (+ back to stock)
    and some are damaged (- to waste).
    """
    item_list = list(items)
    parts = []
    for it in item_list[:max_show]:
        qty = getattr(it, qty_attr, None)
        name = (
            getattr(it, name_attr, None)
            or getattr(getattr(it, 'material', None), 'name', None)
            or getattr(getattr(it, 'product', None), 'name', None)
            or 'Item'
        )
        # Service fees have no stock movement - neutral sign (no +/-)
        product = getattr(it, 'product', None)
        if product is not None and getattr(product, 'is_service', None):
            sign = ''
        else:
            sign = sign_for(it) if sign_for else prefix
        parts.append(f"{sign}{qty} {name}")
    summary = ", ".join(parts)
    extras = len(item_list) - max_show
    if extras > 0:
        summary += f", +{extras} more"
    return summary

def log_audit(business, actor, action, *, target=None, target_ref='',
              old_values=None, new_values=None, reason=''):
    """Permanent audit row. Mirror of log_activity but never pruned + carries before/after."""
    from .models import AuditLog
    target_model = ''
    target_id = None
    if target is not None:
        target_model = target.__class__.__name__
        target_id = target.pk
        target_ref = target_ref or getattr(target, 'reference', '') or ''
    return AuditLog.objects.create(
        business=business, actor=actor, action=action,
        target_model=target_model, target_id=target_id, target_ref=target_ref,
        old_values=old_values or {}, new_values=new_values or {}, reason=reason,
    )

def close_day(business, day, metrics):
    """Lazily freeze ONE past business-day's accrual books (idempotent + race-safe).
    `metrics` = the figures already computed live for that day (a summary_list row).
    Uses get_or_create so the FIRST close wins forever (pen, not pencil) — a later
    read never overwrites it. Returns (DailyClose, created).

    ★ total_cogs joined the snapshot 2026-07-13, when profit moved to a cost-of-goods-SOLD
      basis. Freezing it matters more than it looks: cost_price is a per-sale snapshot, but
      the RELIEF from a later return is read back through it, so a day's cost of sales must
      be pinned at close or a refund booked next month could quietly restate a sealed day.
    """
    from .models import DailyClose
    return DailyClose.objects.get_or_create(
        business=business, date=day,
        defaults={
            'total_revenue':       metrics.get('total_revenue', 0) or 0,
            'total_cogs':          metrics.get('total_cogs', 0) or 0,
            'total_material_cost': metrics.get('total_material_cost', 0) or 0,
            'total_salary_cost':   metrics.get('total_salary_cost', 0) or 0,
            'total_waste_cost':    metrics.get('total_waste_cost', 0) or 0,
            'total_expense_cost':  metrics.get('total_expense_cost', 0) or 0,
            'net_profit':          metrics.get('net_profit', 0) or 0,
        },
    )
