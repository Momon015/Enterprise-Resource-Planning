from .models import ActivityEvent
from .utils import attention_items

def notification_badge(request):
    if not request.user.is_authenticated:
        return {}

    business_slug = request.resolver_match.kwargs.get('business_slug') \
        if request.resolver_match else None
    if not business_slug:
        return {}

    # Resolve the owner via the SHARED helper. This used to be hand-rolled as
    # `if role == 'owner' … else user.owner`, which silently broke the DEVELOPER
    # role: a developer owns their business directly but has no `.owner`, so the
    # lookup raised DoesNotExist and the whole bell rendered empty — no badge, no
    # events, no error. get_owner() maps both 'owner' and 'developer' to self.
    from user.models import BusinessProfile
    from core.utils.owner import get_owner
    try:
        business = BusinessProfile.objects.get(
            user=get_owner(request.user), slug=business_slug
        )
    except (BusinessProfile.DoesNotExist, AttributeError):
        return {}

    # PRODUCT stock events are dropped from the bell — the pinned block above already
    # says "N Products • Out of Stock", live, so belling them too says it twice (and the
    # event lies once it's restocked, while the pin self-clears). They're still LOGGED
    # and still on "See all activities" — this hides them from the dropdown only.
    #
    # Filtered by verb + TARGET TYPE, not verb alone: material stock (cafe/restaurant)
    # fires the SAME verbs from log_stock_threshold_events and has NO pinned row yet,
    # so those must keep belling until phase 2 pins materials.
    from Product.models import Product
    from django.contrib.contenttypes.models import ContentType

    # Never bell you about your OWN action. Voids and returns are `important` only
    # when a non-owner did them (see activity.utils.needs_owner_review) — but staff
    # share this bell, so without this the staff member who voided the sale gets a
    # notification telling them they voided the sale. The owner still sees it.
    unread = ActivityEvent.objects.filter(
        business=business, is_important=True, is_read=False
    ).exclude(
        actor=request.user,
    ).exclude(
        verb__startswith='stock.',
        target_type=ContentType.objects.get_for_model(Product),
    )[:10]

    unread_list = list(unread)

    return {
        # FEED — events (things that happened). Read/unread, drives the badge.
        # count from the already-fetched list: the qs is sliced [:10], so .count()
        # could never exceed 10 anyway — it was a second query for the same number.
        'notification_count': len(unread_list),
        'notification_events': unread_list,

        # PINNED — state (things that are true right now). Never read/unread,
        # never counted in the badge (or the badge could never reach zero).
        'pinned_items': attention_items(business),
    }
