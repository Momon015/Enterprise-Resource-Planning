from django.db import migrations, models


def backfill_plan_started_at(apps, schema_editor):
    """Seed the new term anchor for rows that predate it.

    Free plans have no running term, so they stay NULL. For paid/trial rows the best
    available estimate is started_at (the row's birthday) — and on the existing data it
    is also the correct one, because every paid business here was upgraded on the same
    day it was created. New rows get a real anchor from upgrade_to()/start_trial().
    """
    BusinessPlan = apps.get_model('subscription', 'BusinessPlan')
    BusinessPlan.objects.exclude(plan='free').update(
        plan_started_at=models.F('started_at')
    )


def unset_plan_started_at(apps, schema_editor):
    BusinessPlan = apps.get_model('subscription', 'BusinessPlan')
    BusinessPlan.objects.update(plan_started_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0004_alter_cancellationinvoice_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='businessplan',
            name='plan_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_plan_started_at, unset_plan_started_at),
    ]
