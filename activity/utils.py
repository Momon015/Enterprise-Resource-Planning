from activity.models import ActivityEvent
from django.db.models import Q

def scope_events_for_user(qs, user):
    """
    Staff see: their own events + stock alerts (low/out).
    Owners/dev: see all.
    """
    if user.role == 'staff':
        return qs.filter(
            Q(actor=user) |
            Q(actor__isnull=True, verb__in=['stock.low', 'stock.out'])
        )
    return qs

def log_activity(business, actor, verb, target=None, description='',
                 metadata=None, important=False):
    """
    Single entry point for logging activities.
    Always called explicitly from views (not signals) so we control wording + actor.
    """
    
    return ActivityEvent.objects.create(
        business=business,
        actor=actor,
        verb=verb,
        target=target,
        description=description,
        metadata=metadata or {},
        is_important=important,
    )