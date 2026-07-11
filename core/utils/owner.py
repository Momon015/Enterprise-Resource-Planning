from functools import wraps
from django.shortcuts import redirect, render, get_object_or_404
from django.contrib import messages
from django.urls import reverse

from user.models import BusinessProfile, User

# utils/owner.py
def get_owner(user):
    if user.role in ('developer', 'owner'):
        return user
    return user.owner

def permission_required(action):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            business_slug = kwargs.get('business_slug')
        
            referer = request.META.get('HTTP_REFERER')
            
            if request.user.role == 'developer':
                if action in ('create', 'view', 'delete', 'update', 'save', 'add', 'read_only'):
                    messages.error(request, "Developer accounts have read-only access. Creating, editing, and deleting records is restricted.")
                    if referer:
                        return redirect(referer)
                    return redirect('product-list', business_slug=business_slug)
            if request.user.role == 'staff':
                if action == 'staff_view':
                    messages.error(request, "This section is owner-only. You don't have access to financial records and analytics.")
                    if referer:
                        return redirect(referer)
                    else:
                        return redirect('product-list', business_slug=business_slug)
                elif action in ('owner_delete', 'staff_add'):
                    messages.error(request, "Only the business owner can perform this action.")
                    if referer:
                        return redirect(referer)
                    else:
                        return redirect('product-list', business_slug=business_slug)
                elif action == 'owner_only':
                    messages.error(request, "This section is only available to the account owner.")
                    if referer:
                        return redirect(referer)
                    else:
                        return redirect('product-list', business_slug=business_slug)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator

def get_queryset_for_user(user, queryset):
    if user.role == 'developer':
        return queryset
    else:
        owner = get_owner(user)
        return queryset.filter(user=owner)


def user_account(func):
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        
        user_slug = kwargs.get('slug')
        
        if request.user.slug != user_slug:
            return render(request, 'core/no_access.html', status=403)
        
        return func(request, *args, **kwargs)
        
    return wrapper


def get_business_for_user(user, business_slug):
    owner = get_owner(user)
    business = get_object_or_404(BusinessProfile, user=owner, slug=business_slug)
    return business 


def filter_to_own_if_staff(user, queryset, owned_by_field='created_by'):
    """For transactional records — staff sees only ones they personally created.
    Owner/dev see everything. Use AFTER get_queryset_for_user."""
    if user.role == 'staff':
        return queryset.filter(**{owned_by_field: user})
    return queryset


def can_handle_receivables(user, business):
    """True if this user may see & collect customer debt for this business.
    Owner/dev always; staff only if their Employee record for this business has
    the owner-granted flag. Guards the receivables panel, page, and payment action."""
    if user.role in ('owner', 'developer'):
        return True
    from Employee.models import Employee   # local import — avoids app-load cycle
    return Employee.objects.filter(
        staff_user=user, business=business, can_handle_receivables=True,
    ).exists()


def can_handle_payables(user, business):
    """Twin of can_handle_receivables for supplier bills (payables)."""
    if user.role in ('owner', 'developer'):
        return True
    from Employee.models import Employee
    return Employee.objects.filter(
        staff_user=user, business=business, can_handle_payables=True,
    ).exists()