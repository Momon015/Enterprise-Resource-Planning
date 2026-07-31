from django.conf import settings

from core.utils.owner import get_business_for_user, get_owner, can_handle_receivables, can_handle_payables
from user.models import BusinessProfile


def feature_flags(request):
    return {'ALLOW_REGISTRATION': settings.ALLOW_REGISTRATION}


def business_context(request):
    business = None
    user_businesses = []
    pending_acks = None

    if request.user.is_authenticated:
        # select_related('plan') so the sidebar/switcher reads each business's plan
        # without an extra query per row. For owners the SubscriptionExpiryMiddleware
        # already materialised this exact list this request — reuse it rather than
        # re-query. (Staff never hit that middleware, so they keep the direct query.)
        if request.user.role == 'staff':
            user_businesses = BusinessProfile.objects.filter(
                employees__staff_user=request.user).select_related('plan')
        else:
            user_businesses = getattr(request.user, '_owner_businesses', None)
            if user_businesses is None:
                user_businesses = request.user.business_profiles.select_related('plan')

        # All lookups below scan in Python, not `.filter()`/`.first()`, so they work whether
        # user_businesses is the middleware's list or a queryset — and reuse the single
        # evaluation the navbar switcher needs anyway, instead of firing extra slug queries.
        # 1. A business-scoped page → use its slug AND remember it
        slug = None
        if request.resolver_match:
            slug = request.resolver_match.kwargs.get('business_slug')
        if slug:
            # Reuse the instance the view/decorator already resolved this request (its
            # `plan` is cached on it) before scanning the switcher list.
            memo = getattr(request.user, '_business_cache', None)
            business = (memo or {}).get(slug) \
                or next((b for b in user_businesses if b.slug == slug), None)
            if business:
                request.session['active_business_slug'] = business.slug

        # 2. Account page (no slug) → fall back to the remembered business
        if not business:
            remembered = request.session.get('active_business_slug')
            if remembered:
                business = next((b for b in user_businesses if b.slug == remembered), None)

        # 3. Last resort → first business
        if not business:
            business = next(iter(user_businesses), None)

        # Staff acknowledgement alerts (mid-shift cash payouts + opening-cash changes)
        if business and request.user.role == 'staff':
            from Employee.utils import pending_acks_for_staff   # local import avoids circular load
            pending_acks = pending_acks_for_staff(request.user, business)

    # Debt-access flags for the whole UI (navbar lock badges, panels, buttons).
    # Owner/dev always; staff only where the owner granted the per-employee flag.
    can_view_receivables = False
    can_view_payables = False
    if business and request.user.is_authenticated:
        can_view_receivables = can_handle_receivables(request.user, business)
        can_view_payables = can_handle_payables(request.user, business)

    return {
        'current_business': business,
        'user_businesses': user_businesses,
        'pending_acks': pending_acks,
        'can_view_receivables': can_view_receivables,
        'can_view_payables': can_view_payables,
    }

def cart_counts(request):
    def _count(d):
        if not isinstance(d, dict):
            return 0
        return sum((v.get('quantity', 0) or 0) for v in d.values() if isinstance(v, dict))

    # Split by cart, not lumped together: standing on the sale cart is a reason to hide the
    # SALE button (you are already looking at it), not a reason to hide a purchase cart you
    # have items waiting in.
    sale_cart_pages = {
        'view-sale', 'view-session-summary', 'sale-confirm-summary', 'sale-summary',
    }
    purchase_cart_pages = {
        'view-cart', 'view-cart-summary', 'confirm-purchase-summary', 'view-purchase-summary',
    }
    url_name = request.resolver_match.url_name if request.resolver_match else None

    return {
        'sale_cart_count': _count(request.session.get('sale', {})),
        'purchase_cart_count': _count(request.session.get('cart', {})),
        'on_sale_cart_page': url_name in sale_cart_pages,
        'on_purchase_cart_page': url_name in purchase_cart_pages,
    }


