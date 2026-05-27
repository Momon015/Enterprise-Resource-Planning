from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError

from subscription.models import (
    Subscription, PLAN_LIMITS, BUNDLE_COUNT, LOCKABLE_LIMIT_KEYS,
)
from core.utils.owner import get_business_for_user

# Create your views here.

@login_required(login_url='login')
def pricing(request, business_slug):
    """In-app pricing page — same content as landing, with current plan highlighted."""
    business = get_business_for_user(request.user, business_slug)
    bp = getattr(business, 'plan', None)
    sub = getattr(business.user, 'subscription', None)
    
    context = {
        'business': business,
        'current_plan': bp.plan,
        'current_bundle': sub.bundle if sub else 'triple',
        'is_founder': sub.is_founder if sub else False,
    }
    return render(request, 'subscription/pricing.html', context)

@login_required(login_url='login')
def contact(request, business_slug):
    from django.core.mail import EmailMultiAlternatives
    from django.conf import settings
    
    """In-app contact form — owner emails support."""
    business = get_business_for_user(request.user, business_slug)
    
    if request.method == 'POST':
        # Honeypot
        if request.POST.get('website'):
            return redirect('subscription-contact', business_slug=business.slug)
        
        subject = request.POST.get('subject', '').strip()
        message_body = request.POST.get('message', '').strip()
        
        if not subject or not message_body:
            messages.error(request, 'Subject and message are required.')
            return redirect('subscription-contact', business_slug=business.slug)
        
        # Build the email to support
        support_email = getattr(settings, 'SUPPORT_EMAIL', settings.EMAIL_HOST_USER)
        body = {
            f"From: {request.user.username} ({request.user.email})\n"
            f"Business: {business.business_name} (slug: {business.slug})\n"
            f"User role: {request.user.role}\n\n"
            f"Subject: {subject}\n\n"
            f"--- Message ---\n{message_body}\n"
        
        }
        try:
            email = EmailMultiAlternatives(
                subject=f"[Swift ERP Contact] {subject}",
                body=body,
                from_email=settings.EMAIL_HOST_USER,
                to=[support_email],
                reply_to=[request.user.email] if request.user.email else None,
            )
            email.send()
            messages.success(request, "Your message has been sent. We'll get back to you shortly.")
            return redirect('subscription-contact', business_slug=business.slug)
     
        except Exception:
            messages.error(request, "Couldn't send your message. Please try again or email us directly.")
            return redirect('subscription-contact', business_slug=business.slug)

    return render(request, 'subscription/contact.html', {'business': business})
        
        
# Maps URL slugs to actual model classes for set_active_items
def _resolve_model(model_key):
    from Product.models import Product, ProductPreset
    from Supplier.models import Material, MaterialPreset, Supplier
    from Expense.models import Employee

    return {
        'product':         Product,
        'material':        Material,
        'supplier':        Supplier,
        'employee':        Employee,
        'product_preset':  ProductPreset,
        'material_preset': MaterialPreset,
    }.get(model_key)


@login_required(login_url='login')
def subscription_settings(request, business_slug):
    """Show all the owner's businesses with their per-business plans + usage."""
    business = get_business_for_user(request.user, business_slug)
    owner = business.user
    sub = getattr(owner, 'subscription', None)

    if sub is None:
        messages.warning(request, 'No subscription found for this account.')
        return redirect('dashboard', business_slug=business.slug)

    from Product.models import Product, ProductPreset
    from Supplier.models import Material, MaterialPreset, Supplier
    from Expense.models import Employee
    from subscription.models import (
        BusinessPlan, PLAN_CHOICES, BUNDLE_COUNT,
        FOUNDER_BASE, REGULAR_BASE, PLAN_LIMITS,
    )

    # Build per-business cards
    businesses_data = []
    for biz in owner.business_profiles.all().order_by('id'):
        bp = getattr(biz, 'plan', None)
        if bp is None:
            continue

        limits = bp.limits()
        usage_rows = [
            {
                'label': 'Products', 'key': 'product',
                'used': Product.objects.filter(business=biz).count(),
                'limit': limits['max_products'],
                'locked': Product.objects.filter(business=biz, is_locked=True).count(),
            },
            {
                'label': 'Materials', 'key': 'material',
                'used': Material.objects.filter(business=biz).count(),
                'limit': limits['max_materials'],
                'locked': Material.objects.filter(business=biz, is_locked=True).count(),
            },
            {
                'label': 'Suppliers', 'key': 'supplier',
                'used': Supplier.objects.filter(business=biz).count(),
                'limit': limits['max_suppliers'],
                'locked': Supplier.objects.filter(business=biz, is_locked=True).count(),
            },
            {
                'label': 'Staff', 'key': 'employee',
                'used': Employee.objects.filter(business=biz).count(),
                'limit': limits['max_staff'],
                'locked': Employee.objects.filter(business=biz, is_locked=True).count(),
            },
            {
                'label': 'Product presets', 'key': 'product_preset',
                'used': ProductPreset.objects.filter(business=biz).count(),
                'limit': limits['max_product_presets'],
                'locked': ProductPreset.objects.filter(business=biz, is_locked=True).count(),
            },
            {
                'label': 'Material presets', 'key': 'material_preset',
                'used': MaterialPreset.objects.filter(business=biz).count(),
                'limit': limits['max_material_presets'],
                'locked': MaterialPreset.objects.filter(business=biz, is_locked=True).count(),
            },
        ]
        total_locked = sum(row['locked'] for row in usage_rows)

        businesses_data.append({
            'business': biz,
            'plan': bp,
            'usage_rows': usage_rows,
            'total_locked': total_locked,
        })

    # Per-plan base pricing for the picker buttons (single-business price)
    base_table = FOUNDER_BASE if sub.is_founder else REGULAR_BASE
    plan_options = []
    for plan_key, plan_label in PLAN_CHOICES:
        plan_options.append({
            'key': plan_key,
            'name': plan_label,
            'monthly_per_biz': base_table.get(plan_key, 0),
            'has_dashboard': PLAN_LIMITS[plan_key]['dashboard'],
        })

    context = {
        'sub': sub,
        'business': business,
        'businesses': businesses_data,
        'plan_options': plan_options,
        'total_monthly': sub.get_monthly_price(),
        'total_yearly': sub.get_yearly_price(),
        'bundle_count': BUNDLE_COUNT.get(sub.bundle, 1),
        'business_count': owner.business_profiles.count(),
    }
    return render(request, 'subscription/settings.html', context)

@login_required(login_url='login')
@require_POST
def change_business_plan(request, business_slug):
    """Change the plan of a specific business owned by request.user.
    POST data: target_business_id, new_plan."""
    business = get_business_for_user(request.user, business_slug)
    target_biz_id = request.POST.get('target_business_id')
    new_plan = request.POST.get('new_plan')

    if not target_biz_id or new_plan not in ('free', 'standard', 'premium', 'pro'):
        messages.error(request, 'Invalid plan change request.')
        return redirect('subscription-settings', business_slug=business.slug)

    from user.models import BusinessProfile
    try:
        target_biz = request.user.business_profiles.get(id=target_biz_id)
    except BusinessProfile.DoesNotExist:
        messages.error(request, 'Business not found.')
        return redirect('subscription-settings', business_slug=business.slug)

    target_bp = getattr(target_biz, 'plan', None)
    if target_bp is None:
        messages.error(request, 'No plan record found for this business.')
        return redirect('subscription-settings', business_slug=business.slug)

    if target_bp.plan == new_plan:
        messages.info(request, f"{target_biz.business_name} is already on {new_plan.title()}.")
        return redirect('subscription-settings', business_slug=business.slug)

    # Trial switching — Premium ↔ Pro without resetting expiry
    if target_bp.is_trial and new_plan in ('premium', 'pro'):
        target_bp.plan = new_plan
        target_bp.save(update_fields=['plan'])
        messages.success(request, f"Switched {target_biz.business_name} trial to {new_plan.title()}.")
        return redirect('subscription-settings', business_slug=business.slug)

    try:
        if new_plan == 'free':
            target_bp.downgrade_to_free()
            messages.success(request, f"{target_biz.business_name} downgraded to Free.")
        else:
            target_bp.upgrade_to(new_plan)
            messages.success(request, f"{target_biz.business_name} upgraded to {new_plan.title()}.")
    except (ValueError, ValidationError) as e:
        messages.error(request, str(e))

    return redirect('subscription-settings', business_slug=business.slug)

@login_required(login_url='login')
@require_POST
def start_business_trial(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    target_biz_id = request.POST.get('target_business_id')
    plan = request.POST.get('plan')

    if plan not in ('premium', 'pro'):
        messages.error(request, 'Invalid trial plan.')
        return redirect('subscription-settings', business_slug=business.slug)

    from user.models import BusinessProfile
    try:
        target_biz = request.user.business_profiles.get(id=target_biz_id)
    except BusinessProfile.DoesNotExist:
        messages.error(request, 'Business not found.')
        return redirect('subscription-settings', business_slug=business.slug)

    target_bp = getattr(target_biz, 'plan', None)
    if target_bp is None:
        messages.error(request, 'No plan record for this business.')
        return redirect('subscription-settings', business_slug=business.slug)

    try:
        target_bp.start_trial(plan, days=14)
        messages.success(
            request,
            f"14-day {plan.title()} trial started for {target_biz.business_name}!"
        )
    except (ValueError, ValidationError) as e:
        messages.error(request, str(e))

    return redirect('subscription-settings', business_slug=business.slug)

@login_required(login_url='login')
@require_POST
def claim_founder(request, business_slug):
    """Owner submits a founder invite code to lock lifetime pricing."""
    business = get_business_for_user(request.user, business_slug)
    code = request.POST.get('founder_code', '').strip()

    if not code:
        messages.error(request, 'Please enter a founder code.')
        return redirect('subscription-settings', business_slug=business.slug)

    success = Subscription.claim_founder_invite(request.user, code)
    if success:
        messages.success(request, 'Founder code claimed — lifetime pricing locked in!')
    else:
        messages.error(request, 'Invalid or already-claimed founder code.')

    return redirect('subscription-settings', business_slug=business.slug)


@login_required(login_url='login')
@require_POST
def set_active_items(request, business_slug, model_key):
    """Owner picks which items stay active under the current plan's cap.
    POST data: keep_ids = list of item IDs to keep unlocked."""
    business = get_business_for_user(request.user, business_slug)
    sub = getattr(business.user, 'subscription', None)

    if sub is None:
        messages.error(request, 'No subscription found.')
        return redirect('dashboard', business_slug=business.slug)

    Model = _resolve_model(model_key)
    if Model is None:
        messages.error(request, f"Unknown item type '{model_key}'.")
        return redirect('subscription-settings', business_slug=business.slug)

    keep_ids = [int(i) for i in request.POST.getlist('keep_ids') if i.isdigit()]

    try:
        sub.set_active_items(Model, business, keep_ids)
        messages.success(request, f'{Model._meta.verbose_name_plural.title()} updated successfully.')
    except ValidationError as e:
        messages.error(request, e.messages[0] if hasattr(e, 'messages') else str(e))
    except Exception as e:
        messages.error(request, f'Could not update: {e}')

    return redirect('subscription-settings', business_slug=business.slug)

@login_required(login_url='login')
def manage_active(request, business_slug, model_key):
    """GET: render picker form. POST: process selection — unlock chosen items,
    lock the rest. Per-business — reads cap from BusinessPlan."""
    business = get_business_for_user(request.user, business_slug)
    bp = getattr(business, 'plan', None)

    if bp is None:
        messages.error(request, 'No plan found for this business.')
        return redirect('dashboard', business_slug=business.slug)

    Model = _resolve_model(model_key)
    if Model is None:
        messages.error(request, f"Unknown item type '{model_key}'.")
        return redirect('subscription-settings', business_slug=business.slug)

    cap_key = LOCKABLE_LIMIT_KEYS.get(Model.__name__)
    cap = bp.limits().get(cap_key) if cap_key else None

    if cap is None:
        messages.info(request, f"This business has no limit on {Model._meta.verbose_name_plural}.")
        return redirect('subscription-settings', business_slug=business.slug)

    # POST: process selection
    if request.method == 'POST':
        keep_ids = [int(i) for i in request.POST.getlist('keep_ids') if i.isdigit()]
        try:
            bp.set_active_items(Model, keep_ids)
            locked_now = Model.objects.filter(business=business, is_locked=True).count()
            messages.success(
                request,
                f'{Model._meta.verbose_name_plural.title()} updated — {len(keep_ids)} active, {locked_now} locked.'
            )
            return redirect('subscription-settings', business_slug=business.slug)
        except ValidationError as e:
            messages.error(request, e.messages[0] if hasattr(e, 'messages') else str(e))
        except Exception as e:
            messages.error(request, f'Could not update: {e}')

    # GET: render form with paginated items
    from django.core.paginator import Paginator
    items = Model.objects.filter(business=business).order_by('-updated_at')
    paginator = Paginator(items, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'sub': getattr(business.user, 'subscription', None),
        'business': business,
        'model_key': model_key,
        'model_label': Model._meta.verbose_name_plural.title(),
        'page_obj': page_obj,
        'cap': cap,
        'total_items': items.count(),
        'currently_active': items.filter(is_locked=False).count(),
        'currently_locked': items.filter(is_locked=True).count(),
        'initial_active_ids': list(items.filter(is_locked=False).values_list('id', flat=True)),
    }
    return render(request, 'subscription/manage_active.html', context)


