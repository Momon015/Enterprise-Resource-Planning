from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from Inventory.models import Stock
from activity.utils import log_activity

from core.constants import LOW_STOCK_THRESHOLD, NO_STOCK_THRESHOLD 

# Create your signals here. 

@receiver(pre_save, sender=Stock)
def capture_old_quantity(sender, instance, **kwargs):
    """
    Attach previous quantity to the instance so post_save can detect
    a threshold *crossing* (not just fire on every save).
    """
    if not instance.pk:
        instance._old_quantity = None
        return
    try:
        old = Stock.objects.get(pk=instance.pk)
        instance._old_quantity = old.quantity
    except Stock.DoesNotExist:
        instance._old_quantity = None


@receiver(post_save, sender=Stock)
def log_stock_threshold_events(sender, instance, created, **kwargs):
    """
    Fire `stock.out` or `stock.low` events ONLY when a threshold is crossed.
    Both are marked is_important=True so they show in the notification bell.
    """
    if not instance.business:
        return

    new_qty = instance.quantity
    old_qty = getattr(instance, '_old_quantity', None)
    material_name = instance.material.name if instance.material else (instance.name or 'Stock')

    # Crossed into OUT-OF-STOCK (was > 0 or new, now 0)
    if new_qty == NO_STOCK_THRESHOLD and (old_qty is None or old_qty > NO_STOCK_THRESHOLD):
        log_activity(
            business=instance.business,
            actor=None,  # system event — no human actor
            verb='stock.out',
            target=instance,
            description=f"{material_name} is out of stock",
            metadata={'quantity': 0},
            important=True,
        )
        return  # don't double-log low on the same save

    # Crossed into LOW (was above threshold or new, now 1-49)
    if 1 <= new_qty <= LOW_STOCK_THRESHOLD:
        was_above = old_qty is None or old_qty > LOW_STOCK_THRESHOLD
        if was_above:
            log_activity(
                business=instance.business,
                actor=None,
                verb='stock.low',
                target=instance,
                description=f"{material_name} is very low ({new_qty} left)",
                metadata={'quantity': new_qty, 'threshold': LOW_STOCK_THRESHOLD},
                important=True,
            )
