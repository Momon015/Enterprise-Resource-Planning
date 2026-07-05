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
    """Bell fires on stock.out / stock.critical for MATERIAL stock. 'Low' is NOT
    belled (passive). Material alerts are cafe/restaurant only (raw ingredients);
    retail/pharmacy track stock at the PRODUCT level (log_product_stock_events)."""
    if not instance.business:
        return
    if instance.business.business_type not in ('cafe', 'restaurant'):
        return

    threshold = getattr(instance, 'low_stock_threshold', LOW_STOCK_THRESHOLD)
    critical = max(1, round(threshold * 0.2))
    material_name = instance.material.name if instance.material else (instance.name or 'Stock')

    def band(q):
        if q is None:
            return None
        if q == 0:
            return 'out'
        if q <= critical:
            return 'critical'
        if q <= threshold:
            return 'low'
        return 'ok'

    new_b = band(instance.quantity)
    old_b = band(getattr(instance, '_old_quantity', None))

    if new_b == 'out' and old_b != 'out':
        log_activity(
            business=instance.business, actor=None, verb='stock.out',
            target=instance, description=f"{material_name} is out of stock",
            metadata={'quantity': 0}, important=True,
        )
    elif new_b == 'critical' and old_b not in ('critical', 'out'):
        log_activity(
            business=instance.business, actor=None, verb='stock.critical',
            target=instance,
            description=f"{material_name} is critically low ({instance.quantity} left) — restock now",
            metadata={'quantity': instance.quantity, 'threshold': critical}, important=True,
        )

            

from Product.models import Product
@receiver(pre_save, sender=Product)
def capture_old_margin(sender, instance, **kwargs):
    """Stash margin status + prepared_quantity before save so post_save can detect crossings."""
    if not instance.pk:
        instance._old_margin_status = None
        instance._old_prepared_quantity = None
        return
    try:
        old = Product.objects.select_related('category').get(pk=instance.pk)
        instance._old_margin_status = old.margin_status
        instance._old_prepared_quantity = old.prepared_quantity
    except Product.DoesNotExist:
        instance._old_margin_status = None
        instance._old_prepared_quantity = None



@receiver(post_save, sender=Product)
def log_product_stock_events(sender, instance, created, **kwargs):
    """Bell fires on stock.out / stock.critical for GOODS products (all business
    types). 'Low' is NOT belled — it's a passive dashboard (Needs Attention) signal."""
    if not instance.business:
        return
    if instance.is_service or not instance.is_active:
        return  # goods only (mirrors Product.goods)

    new_status = instance.stock_status_for(instance.prepared_quantity)
    old_status = instance.stock_status_for(getattr(instance, '_old_prepared_quantity', None))
    name = instance.name

    if new_status == 'out' and old_status != 'out':
        log_activity(
            business=instance.business, actor=None, verb='stock.out',
            target=instance, description=f"{name} is out of stock",
            metadata={'quantity': 0}, important=True,
        )
    elif new_status == 'critical' and old_status not in ('critical', 'out'):
        qty = instance.prepared_quantity
        log_activity(
            business=instance.business, actor=None, verb='stock.critical',
            target=instance, description=f"{name} is critically low ({qty} left) — restock now",
            metadata={'quantity': qty, 'threshold': instance.critical_stock_threshold},
            important=True,
        )

