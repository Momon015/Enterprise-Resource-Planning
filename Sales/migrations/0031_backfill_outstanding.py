"""Backfill the denormalized `outstanding` on every existing Sale and Purchase.

`outstanding` used to be a computed property; it became a stored field so receivables /
payables can be a fast partial-indexed filter (WHERE outstanding > 0). Existing rows were
created before anything wrote the column, so they all sit at the default 0 — which would
make every unpaid debt invisible. This recomputes each row from its real payments/returns,
exactly as Sale/Purchase.recompute_outstanding() does at runtime.

Payments and returns are summed in SEPARATE passes on purpose: annotating both Sum()s in one
query multiplies the rows (each payment × each return) and silently doubles the totals.
"""
from decimal import Decimal

from django.db import migrations
from django.db.models import Sum


def _sum_by(model, group_field, value_field):
    return {
        row[group_field]: (row['t'] or Decimal('0'))
        for row in model.objects.values(group_field).annotate(t=Sum(value_field))
    }


def backfill(apps, schema_editor):
    Sale = apps.get_model('Sales', 'Sale')
    SalesPayment = apps.get_model('Sales', 'SalesPayment')
    SalesReturn = apps.get_model('Sales', 'SalesReturn')

    paid = _sum_by(SalesPayment, 'sale', 'amount')
    credit = _sum_by(SalesReturn, 'original_sale', 'refund_credit')

    batch = []
    for s in Sale.objects.all().iterator(chunk_size=2000):
        if s.is_void or s.status != 'completed':
            value = Decimal('0')
        else:
            value = ((s.total_revenue or Decimal('0'))
                     - paid.get(s.id, Decimal('0')) - credit.get(s.id, Decimal('0')))
        s.outstanding = value
        batch.append(s)
        if len(batch) >= 2000:
            Sale.objects.bulk_update(batch, ['outstanding'])
            batch = []
    if batch:
        Sale.objects.bulk_update(batch, ['outstanding'])

    Purchase = apps.get_model('Expense', 'Purchase')
    PurchasePayment = apps.get_model('Expense', 'PurchasePayment')
    PurchaseReturn = apps.get_model('Expense', 'PurchaseReturn')

    p_paid = _sum_by(PurchasePayment, 'purchase', 'amount')
    p_credit = _sum_by(PurchaseReturn, 'original_purchase', 'refund_credit')

    batch = []
    for p in Purchase.objects.all().iterator(chunk_size=2000):
        if p.is_void:
            value = Decimal('0')
        else:
            value = ((p.total_cost or Decimal('0'))
                     - p_paid.get(p.id, Decimal('0')) - p_credit.get(p.id, Decimal('0')))
        p.outstanding = value
        batch.append(p)
        if len(batch) >= 2000:
            Purchase.objects.bulk_update(batch, ['outstanding'])
            batch = []
    if batch:
        Purchase.objects.bulk_update(batch, ['outstanding'])


def noop(apps, schema_editor):
    # Reverse leaves the (now-correct) values in place — nothing to undo.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('Sales', '0030_remove_sale_sale_receivables_idx_and_more'),
        ('Expense', '0012_purchase_outstanding_purchase_total_quantity_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
