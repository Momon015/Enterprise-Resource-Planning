from django.core.cache import cache
from django.db.models.signals import post_save, post_delete
from django.utils import timezone


BUST_DEBOUNCE = 30   # seconds — burst of writes within this window = 1 bust

def bust_dashboard_cache(sender, instance, **kwargs):
    business = getattr(instance, 'business', None)
    if not business:
        return
    today = timezone.localdate().isoformat()
    cache_key = f'dashboard:metrics:{business.id}:{today}'
    throttle_key = f'{cache_key}:bust_throttle'

    # Only bust once per BUST_DEBOUNCE window per business.
    if cache.add(throttle_key, True, timeout=BUST_DEBOUNCE):
        cache.delete(cache_key)



def register():
    """Wire bust_dashboard_cache to every model that feeds the dashboard KPI block."""
    from Sales.models import Sale
    from Expense.models import Purchase, Waste, Expense
    from Employee.models import Shift

    for model in (Sale, Purchase, Waste, Expense, Shift):
        post_save.connect(
            bust_dashboard_cache, sender=model,
            dispatch_uid=f'dashboard_bust_save_{model.__name__}',
        )
        post_delete.connect(
            bust_dashboard_cache, sender=model,
            dispatch_uid=f'dashboard_bust_delete_{model.__name__}',
        )
