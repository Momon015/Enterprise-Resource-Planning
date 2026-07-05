from django.contrib.auth import logout
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from datetime import timedelta

from Employee.utils import staff_seat_locked

def _notify_plan_expiry(biz, bp):
    """Emit ONE bell notif when a trial/paid plan is within 3 days of expiry.
    Deduped by a 4-day window so it fires at most once per approach-to-expiry."""
    from activity.models import ActivityEvent
    from activity.utils import log_activity

    if not bp.expires_at:
        return
    days_left = (bp.expires_at - timezone.now()).days
    if days_left < 0 or days_left > 3:
        return  # only inside the 0–3 day window (not already expired)

    verb = 'trial.ending' if bp.is_trial else 'plan.expiring'
    already = ActivityEvent.objects.filter(
        business=biz, verb=verb,
        created_at__gte=timezone.now() - timedelta(days=4),
    ).exists()
    if already:
        return

    label = 'free trial' if bp.is_trial else f"{bp.get_plan_display()} plan"
    when = 'today' if days_left == 0 else f"in {days_left} day{'' if days_left == 1 else 's'}"
    action = 'Upgrade to keep your features.' if bp.is_trial else 'Renew to avoid losing features.'
    log_activity(
        business=biz, actor=biz.user, verb=verb,
        description=f"Your {label} ends {when}. {action}",
        metadata={'expires_on': bp.expires_at.date().isoformat(), 'days_left': days_left},
        important=True,
    )


class SubscriptionExpiryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.user.role == 'owner':
            from activity.utils import log_activity
            # Per-business: auto-downgrade expired plans, and warn ones expiring soon.
            for biz in request.user.business_profiles.all():
                bp = getattr(biz, 'plan', None)
                if not bp:
                    continue
                if bp.is_expired():
                    was_trial = bp.is_trial
                    label = 'free trial' if was_trial else f"{bp.get_plan_display()} plan"
                    bp.downgrade_to_free()
                    log_activity(
                        business=biz, actor=biz.user, verb='plan.expired',
                        description=f"Your {label} {'ended' if was_trial else 'expired'} — you're now on Free.",
                        important=True,
                    )
                else:
                    _notify_plan_expiry(biz, bp)
        return self.get_response(request)


class InactiveOwnerLogoutOwnerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and user.role == 'staff':
            owner = user.owner
            if owner and not owner.is_active:
                logout(request)
                messages.error(request,
                    "Your business account is currently inactive. Please contact the owner.")
                return redirect('login')

            if staff_seat_locked(user):
                logout(request)
                messages.error(request,
                    "Your access is paused — the owner's plan no longer covers your staff seat. "
                    "Ask them to upgrade or re-activate your seat.")
                return redirect('login')
        return self.get_response(request)
