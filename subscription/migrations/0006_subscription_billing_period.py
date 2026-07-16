from django.db import migrations, models


def backfill_period(apps, schema_editor):
    """Reconstruct each owner's billing term from the per-business dates it replaces.

    Until now the term end lived on BusinessPlan.expires_at, set by hand in admin. An
    owner's period end is the latest of those across their paid, non-trial, non-archived
    businesses; the start is the earliest plan_started_at in that same set. Owners with no
    paid business keep NULL/NULL — no term is running, and the biller must skip them.
    """
    Subscription = apps.get_model('subscription', 'Subscription')
    BusinessPlan = apps.get_model('subscription', 'BusinessPlan')

    for sub in Subscription.objects.all():
        paid = BusinessPlan.objects.filter(
            business__user_id=sub.user_id,
            business__is_active=True,
            is_trial=False,
        ).exclude(plan='free')

        ends = [bp.expires_at for bp in paid if bp.expires_at]
        if not ends:
            continue

        starts = [bp.plan_started_at for bp in paid if bp.plan_started_at]
        sub.current_period_end = max(ends)
        sub.current_period_start = min(starts) if starts else None
        sub.save(update_fields=['current_period_start', 'current_period_end'])


def drop_period(apps, schema_editor):
    Subscription = apps.get_model('subscription', 'Subscription')
    Subscription.objects.update(current_period_start=None, current_period_end=None)


class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0005_businessplan_plan_started_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscription',
            name='current_period_start',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='subscription',
            name='current_period_end',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_period, drop_period),
    ]
