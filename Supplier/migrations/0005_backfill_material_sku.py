from django.db import migrations


def _max_suffix(skus):
    top = 0
    for sku in skus:
        if not sku or '-' not in sku:
            continue
        try:
            top = max(top, int(sku.rsplit('-', 1)[-1]))
        except ValueError:
            continue
    return top


def backfill_material_sku(apps, schema_editor):
    """Give every pre-existing material a MAT- SKU.

    Numbers continue ABOVE the highest suffix already used by either series for the
    business, so a backfilled MAT- can never collide with an existing PRD- (and a later
    product mirroring a backfilled material stays consistent). Mirrors core/utils/sku.
    """
    Material = apps.get_model('Supplier', 'Material')
    Product = apps.get_model('Product', 'Product')

    business_ids = set(
        Material.objects.filter(sku='')
        .exclude(business__isnull=True)
        .values_list('business_id', flat=True)
    )
    for biz_id in business_ids:
        n = max(
            _max_suffix(Product.objects.filter(business_id=biz_id).values_list('sku', flat=True)),
            _max_suffix(Material.objects.filter(business_id=biz_id).values_list('sku', flat=True)),
        )
        for mat in Material.objects.filter(business_id=biz_id, sku='').order_by('id'):
            n += 1
            mat.sku = f"MAT-{n:04d}"
            mat.save(update_fields=['sku'])


def noop(apps, schema_editor):
    # SKUs are harmless to leave in place on reverse — nothing keys off their absence.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('Supplier', '0004_material_barcode_material_sku_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_material_sku, noop),
    ]
