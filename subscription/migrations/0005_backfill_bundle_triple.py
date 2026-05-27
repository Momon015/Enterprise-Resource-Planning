from django.db import migrations


def set_triple(apps, schema_editor):
    Subscription = apps.get_model('subscription', 'Subscription')
    Subscription.objects.update(bundle='triple')


def revert_to_single(apps, schema_editor):
    Subscription = apps.get_model('subscription', 'Subscription')
    Subscription.objects.update(bundle='single')


class Migration(migrations.Migration):
    dependencies = [
        ('subscription', '0004_remove_subscription_expires_at_and_more'),  # ← name of the migration from step 1
    ]
    operations = [
        migrations.RunPython(set_triple, revert_to_single),
    ]
