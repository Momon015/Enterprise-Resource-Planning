from django.db.models.signals import post_init, post_save
from django.dispatch import receiver

from subscription.models import Subscription, BusinessPlan

@receiver(post_init, sender=BusinessPlan)
def snapshot_business_plan_state(sender, instance, **kwargs):
    instance._snapshot_plan = instance.plan


@receiver(post_save, sender=BusinessPlan)
def sync_locks_on_business_plan_change(sender, instance, created, **kwargs):
    """Per-business sync — fires when an individual business's plan changes.
    This is the path used by the new subscription settings UI."""
    if created:
        instance._snapshot_plan = instance.plan
        return

    if getattr(instance, '_snapshot_plan', None) == instance.plan:
        return

    instance._sync_all_locks()
    instance._snapshot_plan = instance.plan
