"""Shared per-business item numbering for Product (PRD-) and Material (MAT-) SKUs.

Retail is material ≡ product 1:1: buying a material auto-creates a linked product, and the
pair shares the trailing number — MAT-0007 ↔ PRD-0007 (the "mirror"). To stop a STANDALONE
product (a service, or a manually-created good) from ever landing on a number a mirror will
reuse, both series draw from ONE number space per business. So the next number is the
highest suffix seen across BOTH models, +1.

Not an accountable number (unlike the BIR serials in core/models.AbstractDocumentSequence),
so there is no select_for_update here — the per-business unique constraints on `sku` are the
backstop if two saves race.
"""


def _max_sku_suffix(skus):
    """Highest trailing integer across an iterable of SKU strings ('PRD-0007' → 7)."""
    top = 0
    for sku in skus:
        if not sku or '-' not in sku:
            continue
        try:
            top = max(top, int(sku.rsplit('-', 1)[-1]))
        except ValueError:
            continue
    return top


def next_item_number(business):
    """Next shared item number for `business`, scanned across Product + Material.

    Uses `all_objects` on both so archived/inactive rows still hold their number — freeing
    a number would let a later item reuse it and collide with history.
    """
    from Product.models import Product
    from Supplier.models import Material

    p = _max_sku_suffix(
        Product.all_objects.filter(business=business).values_list('sku', flat=True)
    )
    m = _max_sku_suffix(
        Material.all_objects.filter(business=business).values_list('sku', flat=True)
    )
    return max(p, m) + 1
