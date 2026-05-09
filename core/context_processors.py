from core.utils.owner import get_business_for_user, get_owner
from user.models import BusinessProfile

def business_context(request):
    business = None
    user_businesses = []

    if request.user.is_authenticated:
        owner = get_owner(request.user)
        if request.user.role == 'staff':
             user_businesses = BusinessProfile.objects.filter(employees__staff_user=request.user)
        else:
            # Get all businesses for the dropdown
            user_businesses = request.user.business_profiles.all()
            
        # Try to get the active one from the URL
        slug = None
        if request.resolver_match:
            slug = request.resolver_match.kwargs.get('business_slug')
        
        if slug:
            business = user_businesses.filter(slug=slug).first()
        
        # THE MISSING PIECE: Auto-select if no slug in URL
        if not business:
            business = user_businesses.first()
            
    return {
        'current_business': business,
        'user_businesses': user_businesses
    }

