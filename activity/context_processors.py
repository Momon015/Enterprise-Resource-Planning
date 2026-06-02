from .models import ActivityEvent

def notification_badge(request):
    if not request.user.is_authenticated:
        return {}

    business_slug = request.resolver_match.kwargs.get('business_slug') \
        if request.resolver_match else None
    if not business_slug:
        return {}

    from user.models import BusinessProfile
    try:
        if request.user.role == 'owner':
            business = BusinessProfile.objects.get(user=request.user, slug=business_slug)
        else:
            business = BusinessProfile.objects.get(user=request.user.owner, slug=business_slug)
    except BusinessProfile.DoesNotExist:
        return {}

    unread = ActivityEvent.objects.filter(
        business=business, is_important=True, is_read=False
    )[:10]
    
    unread_list = list(unread)

    return {
        'notification_count': unread.count(),
        'notification_events': unread_list,
    }
