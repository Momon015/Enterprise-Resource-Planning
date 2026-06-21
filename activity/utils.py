from activity.models import ActivityEvent
from django.db.models import Q

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
