from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages

from core.utils.owner import get_business_for_user

OWNER_REDIRECT_MAP = {
    'product':         'product-list',
    'material':        'material-list',
    'supplier':        'supplier-list',
    'staff':           'employee-list',
    'sale':            'sale-list',
    'purchase':        'purchase-list',
    'waste':           'expense-waste-list',
    'expense':         'expense-list',
    'product_preset':  'product-preset-list',
    'material_preset': 'material-preset-list',
}

STAFF_REDIRECT_MAP = {
    'product':         'product-list',
    'material':        'material-list',
    'supplier':        'supplier-list',
    'product_preset':  'product-preset-list',
    'material_preset': 'material-preset-list',
    # sale/purchase/waste/expense → fall through to dashboard for now.
    # Add here once staff can see their own records.
}

MONTHLY_CAPS = {'sale', 'purchase', 'waste', 'expense'}


def _resolve_target(user, capacity_key):
    """Pick a safe redirect URL based on user role."""
    if user.role == 'owner':
        return OWNER_REDIRECT_MAP.get(capacity_key, 'dashboard')
    return STAFF_REDIRECT_MAP.get(capacity_key, 'product-list')


def capacity_required(capacity_key):
    """
    Gates a view by the business's plan capacity.

    Usage:
        @login_required(login_url='login')
        @permission_required('create')
        @capacity_required('product')
        def product_create(request, business_slug):
            ...

    If over cap: shows a toast and redirects to a role-appropriate page.
    Monthly caps (sale/purchase/waste/expense) include the reset date in the message.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            business_slug = kwargs.get('business_slug')
            if not business_slug:
                return view_func(request, *args, **kwargs)

            business = get_business_for_user(request.user, business_slug)
            bp = getattr(business, 'plan', None)
            target = _resolve_target(request.user, capacity_key)

            if bp is None:
                messages.warning(request, 'No plan found for this business.')
                return redirect(target, business_slug=business.slug)

            method = getattr(bp, f'can_add_{capacity_key}', None)
            if method is None:
                messages.error(request, f"Unknown capacity '{capacity_key}'.")
                return redirect(target, business_slug=business.slug)

            if not method():
                plural = {
                    'staff':           'max_staff',
                    'material_preset': 'max_material_presets',
                    'product_preset':  'max_product_presets',
                }.get(capacity_key, f'max_{capacity_key}s')

                limit = bp.limits().get(plural)
                friendly = capacity_key.replace('_', ' ')

                if capacity_key in MONTHLY_CAPS:
                    from subscription.models import BusinessPlan
                    reset = BusinessPlan.next_calendar_reset()
                    reset_str = reset.strftime('%b ') + str(reset.day)  # "Nov 1"
                    messages.warning(
                        request,
                        f"You've reached {limit}/{limit} {friendly}(s) for this month. "
                        f"Resets {reset_str}, or upgrade this business to add more."
                    )
                else:
                    messages.warning(
                        request,
                        f"This business is on {bp.get_plan_display()} which allows only {limit} "
                        f"{friendly}(s). Upgrade this business to add more."
                    )

                return redirect(target, business_slug=business.slug)

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator

def feature_required(feature_check):
    """
    Gate a view by a per-plan feature.
    Usage:
        @feature_required('has_dashboard')
        def dashboard(request, business_slug): ...

    feature_check is the name of a method on BusinessPlan (no args) that
    returns True/False — has_dashboard, has_weekly_summary, etc.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            business_slug = kwargs.get('business_slug')
            if not business_slug:
                return view_func(request, *args, **kwargs)

            business = get_business_for_user(request.user, business_slug)
            bp = getattr(business, 'plan', None)
            target = _resolve_target(request.user, 'product')

            if bp is None:
                messages.warning(request, 'No plan found for this business.')
                return redirect(target, business_slug=business.slug)

            check = getattr(bp, feature_check, None)
            if not callable(check):
                messages.error(request, f"Unknown feature check '{feature_check}'.")
                return redirect(target, business_slug=business.slug)

            if not check():
                messages.warning(
                    request,
                    f"Dashboard requires a higher plan. Upgrade this business to unlock it."
                )
                return redirect(target, business_slug=business.slug)

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator

