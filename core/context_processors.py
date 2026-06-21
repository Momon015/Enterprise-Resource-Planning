from django.conf import settings

from core.utils.owner import get_business_for_user, get_owner
from user.models import BusinessProfile


def feature_flags(request):
    return {'ALLOW_REGISTRATION': settings.ALLOW_REGISTRATION}


def business_context(request):
    business = None
    user_businesses = []
    pending_acks = None

    if request.user.is_authenticated:
        if request.user.role == 'staff':
            user_businesses = BusinessProfile.objects.filter(employees__staff_user=request.user)
        else:
            user_businesses = request.user.business_profiles.all()

        # 1. A business-scoped page → use its slug AND remember it
        slug = None
        if request.resolver_match:
            slug = request.resolver_match.kwargs.get('business_slug')
        if slug:
            business = user_businesses.filter(slug=slug).first()
            if business:
                request.session['active_business_slug'] = business.slug

        # 2. Account page (no slug) → fall back to the remembered business
        if not business:
            remembered = request.session.get('active_business_slug')
            if remembered:
                business = user_businesses.filter(slug=remembered).first()

        # 3. Last resort → first business
        if not business:
            business = user_businesses.first()

        # Staff acknowledgement alerts (mid-shift cash payouts + opening-cash changes)
        if business and request.user.role == 'staff':
            from Employee.utils import pending_acks_for_staff   # local import avoids circular load
            pending_acks = pending_acks_for_staff(request.user, business)

    return {
        'current_business': business,
        'user_businesses': user_businesses,
        'pending_acks': pending_acks,
    }



