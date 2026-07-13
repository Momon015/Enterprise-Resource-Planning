"""Backfill SalesReturn.refund_cash / refund_credit (2026-07-12).

Mirror of Expense/0010. Existing rows carry only refund_method + refund_total, and on the
sales side the method was effectively forced to 'cash' (store credit was paused, so the
form offered nothing else) — meaning a return against an UNPAID utang sale was recorded as
handing the customer cash they had never paid us.

So this does NOT copy the old method across. It REPLAYS each sale's returns in order under
the rule that should have applied all along: knock the customer's balance down first, and
only what's left over can be cash.
"""

from decimal import Decimal

from django.db import migrations

ZERO = Decimal('0')


def backfill(apps, schema_editor):
    Sale = apps.get_model('Sales', 'Sale')

    for sale in Sale.objects.all():
        returns = list(sale.returns.order_by('date', 'id'))
        if not returns:
            continue

        total = sale.total_revenue or ZERO
        paid  = sum((p.amount or ZERO) for p in sale.payments.all())

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
        ('Sales', '0012_salesreturn_refund_cash_salesreturn_refund_credit_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
