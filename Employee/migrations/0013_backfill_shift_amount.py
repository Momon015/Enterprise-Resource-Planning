from django.db import migrations
from django.db.models import Sum


def backfill_shift_amount(apps, schema_editor):
    """Fill Shift.amount from the ShiftEmployee rows that were always the real payroll.

    Every existing Shift reads 0: clock-in created it with defaults={'amount': 0} and
    nothing ever went back to total it, while readers summed shift_employees__daily_rate
    and got the truth. This migration ships in the same commit as the reader swap — flip
    them to Shift.amount without this and every historical payroll silently becomes ₱0.
    """
    Shift = apps.get_model('Employee', 'Shift')
    for shift in Shift.objects.annotate(payroll=Sum('shift_employees__daily_rate')).iterator():
        total = shift.payroll or 0
        if shift.amount != total:
            shift.amount = total
            shift.save(update_fields=['amount'])


def unbackfill(apps, schema_editor):
    """Deliberately a no-op.

    The reverse of "make the column true" is not "make it 0 again" — the pre-migration
    zeros were the bug, and reintroducing them would hand a wrong number to any code
    still reading the column. Nothing is lost by leaving the values: they're derived, so
    the old shift_employees__daily_rate sum reproduces them exactly.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('Employee', '0012_employee_can_handle_payables_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_shift_amount, unbackfill),
    ]
