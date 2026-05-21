from django.db.models.signals import pre_delete
from django.dispatch import receiver

from Supplier.models import Supplier

# Create your signals here.

@receiver(pre_delete, sender=Supplier)
def reassign_to_no_supplier(sender, instance, **kwargs):
    """Before a Supplier is deleted, reassign all its Materials to the
    'No supplier' fallback for the same business."""
    # Don't recurse if the fallback itself is being deleted
    if instance.name == 'No Supplier':
        return

    # Lazy import
    from Supplier.models import Material

    try:
        no_supplier = Supplier.objects.get(
            business=instance.business,
            name='No Supplier',
            slug='no-supplier',
        )
    except Supplier.DoesNotExist:
        # Fallback doesn't exist for some reason — bail silently
        # (could log this if you want)
        return

    Material.objects.filter(supplier=instance).update(supplier=no_supplier)
    

