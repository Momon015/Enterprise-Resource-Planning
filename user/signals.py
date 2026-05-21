from django.db.models.signals import post_save
from django.dispatch import receiver

from user.models import BusinessProfile

from core.models import Category

from Supplier.models import Supplier 

# Create your signals here.

@receiver(post_save, sender=BusinessProfile)
def create_business_defaults(sender, instance, created, **kwargs):
    """Auto-create 'No supplier' and 'No category' for every new BusinessProfile."""
    if not created:
        return
    
    # Lazy imports to avoid circular dependencies
    from Supplier.models import Supplier
    from core.models import Category

    # Default supplier
    Supplier.objects.get_or_create(
        user=instance.user,
        business=instance,
        slug='no-supplier',
        name='No Supplier',
        
    )

    # Default category — one per category type
    for category_type, _label in Category.CATEGORY_TYPE_CHOICES:
        Category.objects.get_or_create(
            user=instance.user,
            business=instance,
            slug='no-category',
            name='No Category',
            category_type=category_type,
            
        )