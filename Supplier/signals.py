from django.db.models.signals import pre_delete, post_save
from django.dispatch import receiver

from Supplier.models import Supplier, Material

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

    Material.all_objects.filter(supplier=instance).update(supplier=no_supplier)
    
@receiver(post_save, sender=Material)
def cascade_material_archive_to_product(sender, instance, **kwargs):
    """Retail/pharmacy: Material → Product cascade (one-way, Material drives).
    Archiving a Material archives its linked Product.
    Restoring a Material restores its linked Product."""
    from Product.models import Product
    if instance.is_active:
        Product.all_objects.filter(material=instance, is_active=False).update(is_active=True)
    else:
        Product.all_objects.filter(material=instance, is_active=True).update(is_active=False)
