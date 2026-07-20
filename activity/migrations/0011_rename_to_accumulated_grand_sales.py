"""Rename the BIR odometer models to match RMO 24-2023's own wording.

Sec IV(5)(a) calls the required feature "Accumulated Grand Total Sales", so the models
now read that way. The pre-existing `grand_total_*` context variables in DailySummary,
Expense and Inventory are a DIFFERENT quantity (period sums and a stock valuation) and
are deliberately left alone — the owner's call, 2026-07-20.

Written by hand rather than generated. makemigrations cannot tell a rename from a
delete-plus-create without being asked interactively, and the non-interactive answer
is DeleteModel + CreateModel — which would drop the odometer table. RenameModel keeps
the rows.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('activity', '0010_grandtotalcounter_grandtotalentry'),
    ]

    operations = [
        migrations.RenameModel(
            old_name='GrandTotalCounter',
            new_name='AccumulatedGrandSalesCounter',
        ),
        migrations.RenameModel(
            old_name='GrandTotalEntry',
            new_name='AccumulatedGrandSalesEntry',
        ),
        # related_name is Python-level only — no SQL runs for these, but the migration
        # state has to agree with the models or every later makemigrations re-detects them.
        migrations.AlterField(
            model_name='accumulatedgrandsalescounter',
            name='business',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='accumulated_grand_sales_counters',
                to='user.businessprofile',
            ),
        ),
        migrations.AlterField(
            model_name='accumulatedgrandsalesentry',
            name='business',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='accumulated_grand_sales_entries',
                to='user.businessprofile',
            ),
        ),
    ]
