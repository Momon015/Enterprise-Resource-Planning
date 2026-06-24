from django.contrib.auth import logout
from django.contrib import messages
from django.shortcuts import redirect

class SubscriptionExpiryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and request.user.role == 'owner':
            # Iterate each business's plan and auto-downgrade any that expired.
            # Per-business now, since each business holds its own expires_at.
            for biz in request.user.business_profiles.all():
                bp = getattr(biz, 'plan', None)
                if bp and bp.is_expired():
                    bp.downgrade_to_free()
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
        return self.get_response(request)