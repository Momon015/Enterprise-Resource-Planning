from django.db.models.signals import pre_save, post_save
from django.dispatch import receiver

from Inventory.models import Stock
from activity.utils import log_activity, log_margin_drop

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
    """Stash margin status + prepared_quantity + cost price before save so post_save
    can detect crossings. cost_price is what tells the margin alert below WHY the
    margin moved, which is the difference between an alert and noise."""
    if not instance.pk:
        instance._old_margin_status = None
        instance._old_prepared_quantity = None
        instance._old_cost_price = None
        return
    try:
        old = Product.objects.select_related('category').get(pk=instance.pk)
        instance._old_margin_status = old.margin_status
        instance._old_prepared_quantity = old.prepared_quantity
        instance._old_cost_price = old.cost_price
    except Product.DoesNotExist:
        instance._old_margin_status = None
        instance._old_prepared_quantity = None
        instance._old_cost_price = None



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


@receiver(post_save, sender=Product)
def log_margin_drop_on_cost_rise(sender, instance, created, **kwargs):
    """Bell `product.margin_low` when a rising COST pushes the margin below target.

    Lives here rather than in the purchase view (where it was rebuilt 2026-07-14
    after the original signal was deleted by an unrelated stock rework): the view
    covered only ONE of the paths that move cost_price, so a cost rise from any
    other route — a correction, an import, the admin — stayed silent. A signal
    cannot be forgotten by the next view that touches cost.

    KEYED ON "COST ROSE", not on "margin got worse". A margin drops for two very
    different reasons and only one of them deserves a bell:
      • cost rose      — a supplier raised their price and the weighted-average
                         crept up with NOBODY looking. This is the silent one.
      • owner cut the  — they are staring at the product form, which already shows
        selling price   a live green/amber/red badge and a suggested price. Belling
                        them about the number under their cursor is noise.
    Comparing cost against its own previous value separates the two cleanly, with
    no need to know which view is calling.

    log_margin_drop() itself only fires on a CROSSING, so a chronically thin
    product does not re-alert on every delivery.

    actor=None on purpose — no request here, and the cause genuinely is the
    supplier, not whoever happened to receive the delivery. It also keeps the
    event off the module panels and the Activity page (scope_events_for_user),
    which is right: re-pricing is the owner's call.

    ⚠ NOT off the BELL — activity/context_processors.py has its own filter and
    does not scope by viewer, so staff still see this one in the dropdown. Harmless
    today (staff already see cost and the margin badge on the product list, by
    design), but it is an owner to-do that staff cannot act on. Scope the bell and
    this note goes away.
    """
    if created or not instance.business:
        return
    if instance.is_service or not instance.is_active:
        return                                    # margin is a goods concept

    old_cost = getattr(instance, '_old_cost_price', None)
    if old_cost is None or instance.cost_price <= old_cost:
        return                                    # cost held or fell — nothing silent happened

    log_margin_drop(
        instance.business, None, instance,
        getattr(instance, '_old_margin_status', None),
    )

