from activity.models import ActivityEvent

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