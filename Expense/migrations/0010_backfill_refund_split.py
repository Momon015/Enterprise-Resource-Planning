"""Backfill PurchaseReturn.refund_cash / refund_credit (2026-07-12).

Existing rows carry only refund_method + refund_total, and the method was whatever the
form was left on — which is how an unpaid ₱430 PO ended up with an ₱85 "cash refund"
from a supplier it had never paid a peso to.

So this does NOT copy the old method across. It REPLAYS each purchase's returns in order
under the rule that should have applied all along: knock the debt down first, and only
what's left over can be cash. Rows that were already coherent land on the same numbers;
the incoherent ones get repaired.
"""

from decimal import Decimal

from django.db import migrations

ZERO = Decimal('0')


def backfill(apps, schema_editor):
    Purchase = apps.get_model('Expense', 'Purchase')

    for purchase in Purchase.objects.all():
        returns = list(purchase.returns.order_by('date', 'id'))
        if not returns:
            continue

        total = purchase.total_cost or ZERO
        paid  = sum((p.amount or ZERO) for p in purchase.payments.all())

        # Credit refunds reduce the balance, so the window shrinks as we replay.
        credit_so_far = ZERO
        for r in returns:
            outstanding = total - paid - credit_so_far
            if outstanding < ZERO:
                outstanding = ZERO

            amount = r.refund_total or ZERO
            credit = min(amount, outstanding)
            cash   = amount - credit

            r.refund_credit = credit
            r.refund_cash   = cash
            if cash > ZERO and credit > ZERO:
                r.refund_method = 'mixed'
            else:
                r.refund_method = 'credit' if credit > ZERO else 'cash'
            r.save(update_fields=['refund_cash', 'refund_credit', 'refund_method'])

            credit_so_far += credit


def unbackfill(apps, schema_editor):
    """Nothing to undo — the split columns go away with the schema migration."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('Expense', '0009_purchasereturn_refund_cash_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
