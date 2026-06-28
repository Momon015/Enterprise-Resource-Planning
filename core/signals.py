from django.db.models.signals import pre_delete
from django.dispatch import receiver

from core.models import Category

# Create your signals here.

# ── KPI cache bust-on-write ──────────────────────────────────────────────
from django.db.models.signals import post_save, post_delete
from core.utils.kpis import bust_kpis

def _bust_kpis_on_write(sender, instance, **kwargs):
    bust_kpis(getattr(instance, 'business', None))

def _register_kpi_bust():
    from Sales.models import Sale
    from Expense.models import Purchase, Waste, Expense
    from Product.models import Product
    from Supplier.models import Material, Supplier

    for model in (Sale, Purchase, Waste, Expense, Product, Material, Supplier):
        post_save.connect(_bust_kpis_on_write, sender=model,
                          dispatch_uid=f'kpi_bust_save_{model.__name__}')
        post_delete.connect(_bust_kpis_on_write, sender=model,
                            dispatch_uid=f'kpi_bust_delete_{model.__name__}')

_register_kpi_bust()

@receiver(pre_delete, sender=Category)
def reassign_to_no_category(sender, instance, **kwargs):
    """Before a Category is deleted, reassign all related items to the
    'No category' fallback for the same business AND same category_type."""
    if instance.name == 'No Category':
        return  # don't recurse

    try:
        no_category = Category.objects.get(
            business=instance.business,
            name='No Category',
            category_type=instance.category_type,

        )
    except Category.DoesNotExist:
        return

    # Reassign based on what the category is for
    if instance.category_type == 'product':
        from Product.models import Product
        Product.objects.filter(category=instance).update(category=no_category)
    elif instance.category_type == 'material':
        from Supplier.models import Material
        Material.objects.filter(category=instance).update(category=no_category)
    elif instance.category_type == 'expense':
        from Expense.models import MiscExpense
        MiscExpense.objects.filter(category=instance).update(category=no_category)

