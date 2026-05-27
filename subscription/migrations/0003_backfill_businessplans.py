# Backfill BusinessPlan for every existing BusinessProfile, using the
# owner's current Subscription.plan as the starting tier. Idempotent.

from django.db import migrations


def backfill_business_plans(apps, schema_editor):
    BusinessProfile = apps.get_model('user', 'BusinessProfile')
    Subscription    = apps.get_model('subscription', 'Subscription')
    BusinessPlan    = apps.get_model('subscription', 'BusinessPlan')

    for biz in BusinessProfile.objects.all():
        # Try to find owner's subscription to inherit plan; default to 'free'
        try:
            sub = Subscription.objects.get(user=biz.user)
            plan = sub.plan
        except Subscription.DoesNotExist:
            plan = 'free'

        BusinessPlan.objects.get_or_create(
            business=biz,
            defaults={
                'plan': plan,
                'is_active': True,
            },
        )


def reverse_backfill(apps, schema_editor):
    """No-op reverse — don't delete BusinessPlan rows on rollback because
    they may have been mutated independently (different plan, expires_at, etc.)."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('subscription', '0002_businessplan'),
        ('user', '__latest__'),
    ]

    operations = [
        migrations.RunPython(backfill_business_plans, reverse_backfill),
    ]
