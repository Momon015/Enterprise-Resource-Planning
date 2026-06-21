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
    threshold = getattr(instance, 'low_stock_threshold', LOW_STOCK_THRESHOLD)
    if 1 <= new_qty <= threshold:
        was_above = old_qty is None or old_qty > threshold
        if was_above:
            log_activity(
                business=instance.business,
                actor=None,
                verb='stock.low',
                target=instance,
                description=f"{material_name} is very low ({new_qty} left)",
                metadata={'quantity': new_qty, 'threshold': threshold},
                important=True,
            )

from Product.models import Product

@receiver(pre_save, sender=Product)
def capture_old_margin(sender, instance, **kwargs):
    """Stash margin status before save so post_save can detect a crossing."""
    if not instance.pk:
        instance._old_margin_status = None
        return
    try:
        old = Product.objects.select_related('category').get(pk=instance.pk)
        instance._old_margin_status = old.margin_status
    except Product.DoesNotExist:
        instance._old_margin_status = None


@receiver(post_save, sender=Product)
def log_margin_low_event(sender, instance, created, **kwargs):
    """Fire `product.margin_low` ONLY when a product newly crosses under the 10% floor —
    covers both supplier cost-hikes and owner price edits."""
    if not instance.business:
        return
    new_status = instance.margin_status
    old_status = getattr(instance, '_old_margin_status', None)
    if new_status == 'critical' and old_status != 'critical':
        log_activity(
            business=instance.business,
            actor=None,  # system-detected, like stock.out
            verb='product.margin_low',
            target=instance,
            description=f"{instance.name} margin critically low at {instance.current_margin:.0f}%",
            metadata={'margin': float(instance.current_margin),
                      'target': instance.effective_target_margin},
            important=True,
        )
