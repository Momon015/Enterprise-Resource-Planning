from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.core.exceptions import ValidationError

from subscription.models import (
    Subscription, PLAN_LIMITS, BUNDLE_COUNT, LOCKABLE_LIMIT_KEYS,
)
from core.utils.owner import get_business_for_user

from django.core.mail import EmailMultiAlternatives
from django.conf import settings

from decimal import Decimal, ROUND_HALF_UP

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
    """In-app contact form — owner emails support."""
    business = get_business_for_user(request.user, business_slug)
    is_hx = request.headers.get('HX-Request')

    if request.method == 'POST':
        if request.POST.get('website'):  # honeypot
            if is_hx:
                return HttpResponse(status=204)
            return redirect('subscription-contact', business_slug=business.slug)

        subject = request.POST.get('subject', '').strip()
        message_body = request.POST.get('message', '').strip()

        def form_error(msg):
            if is_hx:
                return render(request, 'core/partials/_contact_modal.html', {
                    'business': business, 'error': msg,
                    'subject_val': subject, 'message_val': message_body,
                })
            messages.error(request, msg)
            return redirect('subscription-contact', business_slug=business.slug)

        if not subject or not message_body:
            return form_error('Subject and message are required.')

        support_email = getattr(settings, 'SUPPORT_EMAIL', settings.EMAIL_HOST_USER)
        body = (
            f"From: {request.user.username} ({request.user.email})\n"
            f"Business: {business.business_name} (slug: {business.slug})\n"
            f"User role: {request.user.role}\n\n"
            f"Subject: {subject}\n\n"
            f"--- Message ---\n{message_body}\n"
        )
        try:
            email = EmailMultiAlternatives(
                subject=f"[Swift ERP Contact] {subject}",
                body=body,
                from_email=settings.EMAIL_HOST_USER,
                to=[support_email],
                reply_to=[request.user.email] if request.user.email else None,
            )
            email.send()
        except Exception:
            return form_error("Couldn't send your message. Please try again or email us directly.")

        if is_hx:
            return render(request, 'core/partials/_contact_sent_modal.html', {})
        messages.success(request, "Your message has been sent. We'll get back to you shortly.")
        return redirect('subscription-contact', business_slug=business.slug)

    if is_hx:
        return render(request, 'core/partials/_contact_modal.html', {'business': business})
    return render(request, 'subscription/contact.html', {'business': business})

# Maps URL slugs to actual model classes for set_active_items
def _resolve_model(model_key):
    from Product.models import Product, ProductPreset
    from Supplier.models import Material, MaterialPreset, Supplier
    from Employee.models import Employee

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
        return redirect('settings', business_slug=business.slug)

    from Product.models import Product, ProductPreset
    from Supplier.models import Material, MaterialPreset, Supplier
    from Employee.models import Employee
    from subscription.models import (
        BusinessPlan, PLAN_CHOICES, BUNDLE_COUNT,
        FOUNDER_BASE, REGULAR_BASE, PLAN_LIMITS,
    )
    
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
                'used': Product.goods.filter(business=biz).count(),
                'archived': Product.all_objects.filter(business=biz, is_active=False, is_service=False).count(),
                'limit': limits['max_products'],
                'locked': Product.goods.filter(business=biz, is_locked=True).count(),
            },
            {
                'label': 'Materials', 'key': 'material',
                'used': Material.objects.filter(business=biz).count(),
                'archived': Material.all_objects.filter(business=biz, status='inactive').count(),
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

        plan_choices = []
        for plan_key, plan_label in PLAN_CHOICES:
            plan_choices.append({
                'key': plan_key,
                'name': plan_label,
                'monthly_per_biz': base_table.get(plan_key, 0),
                'is_current': bp.plan == plan_key,
                'disabled': not bp.can_self_switch_to(plan_key) and bp.plan != plan_key,
            }) 
        
        businesses_data.append({
            'business': biz,
            'plan': bp,
            'usage_rows': usage_rows,
            'total_locked': total_locked,
            'plan_choices': plan_choices,
        })

    yearly_total = sub.get_yearly_price()
    yearly_as_monthly = (yearly_total / 12).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) if yearly_total else Decimal('0')

    from user.models import BusinessProfile
    context = {
        'sub': sub,
        'business': business,
        'businesses': businesses_data,
        'plan_options': plan_options,
        'total_monthly': sub.get_monthly_price(),
        'total_yearly': yearly_total,
        'total_yearly_monthly': yearly_as_monthly,   # ← new
        'bundle_count': BUNDLE_COUNT.get(sub.bundle, 1),
        # Slots used = active + archived (archived still occupies a bundle slot)
        'business_count': BusinessProfile.all_objects.filter(user=owner).count(),
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

    # Block self-serve upgrades to paid plans (trial switches already handled above)
    if not target_bp.can_self_switch_to(new_plan):
        messages.warning(
            request,
            "Contact support to upgrade this business to a paid plan."
        )
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
    
    # GET (htmx) → render the plan-choice modal
    if request.method == 'GET':
        if request.headers.get('HX-Request'):
            return render(request, 'core/partials/_trial_modal.html', {
                'business': business,
                'target_business_id': request.GET.get('target_business_id') or business.id,
            })
        return redirect('subscription-settings', business_slug=business.slug)

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
def trial_modal(request, business_slug):
    """Read-only — renders the plan-choice modal. Starting the trial is POST → start_business_trial."""
    business = get_business_for_user(request.user, business_slug)
    if request.user.role != 'owner':
        return HttpResponse(status=403)
    return render(request, 'core/partials/_trial_modal.html', {
        'business': business,
        'target_business_id': request.GET.get('target_business_id') or business.id,
    })

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

@login_required(login_url='login')
def cancel_business_confirm(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    target_biz_id = request.POST.get('target_business_id') or request.GET.get('target_business_id')

    from user.models import BusinessProfile
    try:
        target_biz = request.user.business_profiles.get(id=target_biz_id)
    except (BusinessProfile.DoesNotExist, ValueError, TypeError):
        messages.error(request, 'Business not found.')
        return redirect('subscription-settings', business_slug=business.slug)

    target_bp = getattr(target_biz, 'plan', None)
    sub = getattr(request.user, 'subscription', None)

    if target_bp is None or sub is None:
        messages.error(request, 'No active subscription found.')
        return redirect('subscription-settings', business_slug=business.slug)


    if target_bp.plan == 'free' or target_bp.pending_cancellation:
        messages.info(request, "Nothing to cancel for this business.")
        return redirect('subscription-settings', business_slug=business.slug)

    refund_due = target_bp.compute_refund_due() if sub.billing_cycle == 'yearly' else Decimal('0')
    months_used = target_bp.months_used_on_plan()
    cycle_end = target_bp.expires_at or sub.current_period_end

    # Cancelling the base-tier business promotes a survivor from surcharge to base rate,
    # so a business the owner is KEEPING can get more expensive. Never spring that on
    # them: it goes in the confirm modal AND in the confirmation email.
    reprice = sub.reprice_preview(target_bp)

    if request.method == 'POST' and request.POST.get('confirm') == 'yes':
        try:
            invoice = target_bp.request_cancellation()
        except (ValueError, ValidationError) as e:
            messages.error(request, str(e))
            return redirect('subscription-settings', business_slug=business.slug)

        _send_cancellation_emails(request.user, target_biz, invoice, reprice)
        messages.success(
            request,
            f"Cancellation confirmed. {target_biz.business_name} ends on {cycle_end.strftime('%b %d, %Y')}."
        )
        return redirect('subscription-settings', business_slug=business.slug)

    # return render(request, 'subscription/cancel_confirm.html', {
    #     'business': business,
    #     'target_biz': target_biz,
    #     'target_bp': target_bp,
    #     'balance_due': balance_due,
    #     'months_used': months_used,
    #     'cycle_end': cycle_end,
    #     'sub': sub,
    # })
    context = {
        'business': business, 'target_biz': target_biz, 'target_bp': target_bp,
        'refund_due': refund_due, 'months_used': months_used,
        'cycle_end': cycle_end, 'sub': sub, 'reprice': reprice,
    }
    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/_cancel_modal.html', context)
    return render(request, 'subscription/cancel_confirm.html', context)



@login_required(login_url='login')
def resume_business_plan(request, business_slug):
    """Undo a scheduled cancellation — the owner changed their mind before the
    cycle ended. POST only; voids the pending refund and keeps the plan."""
    business = get_business_for_user(request.user, business_slug)
    target_biz_id = request.POST.get('target_business_id')

    from user.models import BusinessProfile
    try:
        target_biz = request.user.business_profiles.get(id=target_biz_id)
    except (BusinessProfile.DoesNotExist, ValueError, TypeError):
        messages.error(request, 'Business not found.')
        return redirect('subscription-settings', business_slug=business.slug)

    target_bp = getattr(target_biz, 'plan', None)
    if target_bp is None:
        messages.error(request, 'No active subscription found.')
        return redirect('subscription-settings', business_slug=business.slug)

    if request.method != 'POST':
        return redirect('subscription-settings', business_slug=business.slug)

    try:
        target_bp.resume_cancellation()
    except (ValueError, ValidationError) as e:
        messages.error(request, str(e))
        return redirect('subscription-settings', business_slug=business.slug)

    messages.success(
        request,
        f"{target_biz.business_name} is back on {target_bp.get_plan_display()} — cancellation undone."
    )
    return redirect('subscription-settings', business_slug=business.slug)


def _send_cancellation_emails(owner, target_biz, invoice, reprice=None):
    support_email = getattr(settings, 'SUPPORT_EMAIL', settings.EMAIL_HOST_USER)
    cycle_end_str = invoice.cycle_end_at.strftime('%b %d, %Y')
    due_str = invoice.due_at.strftime('%b %d, %Y')

    if invoice.refund_amount > 0:
        refund_line = (
            f"Because you paid for the year upfront, the {invoice.months_used} month(s) "
            f"you actually used are re-priced at the standard monthly rate and we refund "
            f"the rest: ₱{invoice.refund_amount}. Expect it by {due_str}.\n\n"
        )
    else:
        refund_line = "No refund is due for this cancellation.\n\n"

    # The owner already saw this in the confirm modal. Repeat it here so the price change
    # is in writing, in their inbox, dated — before the bill arrives rather than after.
    reprice_line = ''
    if reprice:
        plural = 'businesses go' if len(reprice) > 1 else 'business goes'
        rows = '\n'.join(
            f"  • {bp.business.business_name} ({bp.get_plan_display()}): "
            f"₱{old_price:,.0f}/mo → ₱{new_price:,.0f}/mo"
            for bp, old_price, new_price in reprice
        )
        reprice_line = (
            f"HEADS UP — this was your main plan, so your other {plural} back to the "
            f"regular price on {cycle_end_str}:\n\n{rows}\n\n"
            f"The lower rate was a discount for running them alongside your main plan. "
            f"Keep '{target_biz.business_name}' and nothing changes.\n\n"
        )

    owner_body = (
        f"Hi {owner.username},\n\n"
        f"Your cancellation for '{target_biz.business_name}' is confirmed.\n\n"
        f"• Plan ends on: {cycle_end_str} (your data stays accessible until then)\n"
        f"• Months used: {invoice.months_used}\n\n"
        f"{refund_line}"
        f"{reprice_line}"
        f"Thanks for giving paKITA a try. You're always welcome back.\n\n"
        f"— paKITA"
    )
    try:
        EmailMultiAlternatives(
            subject=f"[paKITA] Cancellation confirmed — {target_biz.business_name}",
            body=owner_body,
            from_email=settings.EMAIL_HOST_USER,
            to=[owner.email] if owner.email else [],
        ).send()
    except Exception:
        pass

    billing = (
        owner.subscription.get_billing_cycle_display()
        if getattr(owner, 'subscription', None) else 'n/a'
    )
    support_body = (
        f"Cancellation logged.\n\n"
        f"Owner: {owner.username} ({owner.email})\n"
        f"Business: {target_biz.business_name} (slug: {target_biz.slug})\n"
        f"Plan: {invoice.plan_at_cancel.title()}\n"
        f"Billing: {billing}\n"
        f"Cycle ends: {cycle_end_str}\n"
        f"Months used: {invoice.months_used}\n"
        f"Refund due: ₱{invoice.refund_amount} (status: {invoice.get_status_display()})\n"
        f"Invoice ID: {invoice.id}\n"
        + (f"\nBundle reprice — survivors move to the base rate:\n" + '\n'.join(
               f"  {bp.business.business_name}: ₱{old_price:,.0f} → ₱{new_price:,.0f}/mo"
               for bp, old_price, new_price in reprice
           ) + "\n" if reprice else "")
        + (f"\n⚠ REFUND PENDING — issue ₱{invoice.refund_amount} to the owner, "
           f"then mark invoice {invoice.id} as refunded.\n"
           if invoice.status == 'pending' else "")
    )
    try:
        EmailMultiAlternatives(
            subject=f"[paKITA Admin] Cancellation — {target_biz.business_name}",
            body=support_body,
            from_email=settings.EMAIL_HOST_USER,
            to=[support_email],
        ).send()
    except Exception:
        pass


@login_required(login_url='login')
def export_data(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    # Owner-only — staff shouldn't export account-wide data
    if request.user.role != 'owner':
        messages.error(request, "Only the business owner can export data.")
        return redirect('product-list', business_slug=business.slug)

    fmt = request.GET.get('format')
    if fmt in ('csv', 'xlsx'):
        from core.utils.exports import export_csv_zip, export_excel
        try:
            if fmt == 'csv':
                fname, data = export_csv_zip(business)
                content_type = 'application/zip'
            else:
                fname, data = export_excel(business)
                content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        except ImportError:
            messages.error(request, "Excel export needs the openpyxl library. Use CSV, or contact support.")
            return redirect('subscription-export', business_slug=business.slug)

        response = HttpResponse(data, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{fname}"'
        return response

    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/_export_modal.html', {'business': business})
    return render(request, 'subscription/export_data.html', {'business': business})

