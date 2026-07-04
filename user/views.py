from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import ListView, UpdateView, CreateView, DeleteView, FormView, DetailView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages

from django.utils import timezone
from datetime import timedelta
import random

from django.db import IntegrityError
from django.views.decorators.http import require_POST
from django.urls import reverse

from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth import update_session_auth_hash

from django.core.paginator import Paginator

from core.models import Category
from core.utils.email import send_email

from user.models import User, EmailOTP, BusinessProfile
from user.forms import RegisterForm, UpdateUserForm, StyledPasswordChangeForm, BusinessProfileForm, BusinessCashDrawerForm, BusinessFeaturesForm

from core.utils.owner import get_owner, get_queryset_for_user, permission_required

import json 
import pprint

from django.db import transaction
from django.contrib.auth.hashers import make_password

from Employee.models import Employee

from django.utils.text import slugify
from django.core.cache import cache

import datetime

from Employee.utils import is_opening_cash_locked, staff_seat_locked

from activity.utils import log_activity

# Create your views here.

def get_ip(request):
    ip = request.META.get('REMOTE_ADDR')
    return HttpResponse(f"Your IP is {ip}")


def landing(request):
    # if request.user.is_authenticated:
    #     return redirect('business-list')
    return render(request, 'landing_page.html')

def register_form(request):
    # Feature flag: public sign-up can be disabled for a private QA build.
    from django.conf import settings as dj_settings
    if not dj_settings.ALLOW_REGISTRATION:
        messages.info(request, "Registration is currently closed.")
        return redirect('login')

    page = 'register-form'

    if request.method == 'POST':
        
        # Honeypot — silently drop bot submissions
        if request.POST.get('website'):
            return redirect('login')
        
        # MANAGER: CLEANING UNVERIFIED USERS
        User.cleanup.unverified_users(minutes=5).delete() # override the 1 hr 
        
        form = RegisterForm(request.POST)

        if form.is_valid():
 
            username = form.cleaned_data['username'].lower()
            email = form.cleaned_data['email']
            raw_password = form.cleaned_data['password1']
            
            import re
            invite_code = re.sub(r'\D', '', form.cleaned_data.get('invite_code', ''))

            try:
                with transaction.atomic():
                    user = User(username=username, email=email, is_active=False)
                    user.set_password(raw_password)

                    if invite_code:
                        business = BusinessProfile.objects.get(invite_code=invite_code)

                        if not business.accepting_staff:
                            messages.error(request,
                                "This business isn't accepting new staff right now. "
                                "Ask your owner to turn on staff sign-up.")
                            return redirect('register-form')

                        business_plan = getattr(business, 'plan', None)
                        if business_plan and not business_plan.can_add_staff():
                            limit = business_plan.limits().get('max_staff')
                            messages.error(request,
                                f"This business's {business_plan.get_plan_display()} plan allows only "
                                f"{limit} staff account(s). Ask the owner to upgrade.")
                            return redirect('register-form')

                        request.session['business_id'] = str(business.id)
                        user.role = 'staff'
                        user.owner = business.user
                    else:
                        user.role = 'owner'

                    user.save()

                    otp = EmailOTP.generate_otp()
                    otp_obj = EmailOTP.objects.create(user=user, otp=otp)
                    send_email(user.email, otp)

            except BusinessProfile.DoesNotExist:
                messages.error(request,
                    "That invite code didn't match any business. Double-check with your owner.")
                return redirect('register-form')
            except IntegrityError:
                messages.error(request, "That username or email is already registered.")
                return redirect('register-form')

            request.session['user_id'] = user.id
            request.session['otp_id'] = otp_obj.id
            messages.success(request, "The OTP has been sent to your email.")
            return redirect('verify-otp')

    else:
        form = RegisterForm()   

    context = {'form': form, 'page': page}
    return render(request, 'user/register_and_login_form.html', context)

def verify_otp(request):
    user_id = request.session.get('user_id', None)
    otp_id = request.session.get('otp_id', None)
    
    if not user_id or not otp_id:
        messages.error(request, f"Please register again.")
        return redirect('register-form')
    
    user = get_object_or_404(User, id=user_id)
    
    try:
        otp_obj = EmailOTP.objects.get(id=otp_id, user=user)
    except EmailOTP.DoesNotExist:
        messages.error(request, "OTP is no longer valid.")
        request.session.pop('otp_id', None)
        return redirect('expired-otp')
    
    if otp_obj.is_expired():
        request.session.pop('otp_id', None)
        otp_obj.delete()
        messages.error(request, f"The OTP has been expired. Please request for a new OTP.")
        return redirect('expired-otp')
    

    print('otp_obj', otp_obj)
    if request.method == 'POST':
        entered_otp = request.POST.get('otp', None)
        
        if entered_otp == otp_obj.otp:
            try:
                with transaction.atomic():
                    otp_obj.is_verified = True
                    otp_obj.save(update_fields=['is_verified'])

                    if user.role == 'staff':
                        business_id = request.session.get('business_id', None)
                        if not business_id:
                            raise ValueError("Missing business session.")
                        business = BusinessProfile.objects.get(id=business_id)

                        # Email confirmed — hold for owner approval (stays inactive; can't log in yet).
                        user.pending_business = business
                        user.save(update_fields=['pending_business'])

                        # Notify the owner: important → bell red badge + activity feed.
                        log_activity(
                            business=business,
                            actor=user,
                            verb='staff.added',
                            target=user,
                            description=f"{user.name or user.username} signed up and is waiting for your approval.",
                            important=True,
                        )
                    else:
                        user.is_active = True
                        user.save(update_fields=['is_active'])
            except (BusinessProfile.DoesNotExist, ValueError):
                user.delete()
                messages.error(request, "Business not found. Please register again.")
                return redirect('register-form')

            for key in ('user_id', 'otp_id', 'business_id'):
                request.session.pop(key, None)

            if user.role == 'staff':
                # Not logged in — gated until the owner approves.
                return redirect('registration-pending')

            login(request, user)
            return redirect('business-profile-create')
        
        else:
            messages.error(request, "Invalid OTP. Please try again.")

            
            # clear sessions
            for key in ('user_id', 'otp_id', 'business_id'):
                request.session.pop(key, None)
            
            login(request, user)
            if request.user.role == 'owner':
                return redirect('business-profile-create')
            else:
                messages.success(request, f"Your account has been successfully created.")
                return redirect('user-profile', slug=user.username, user_id=user.id)
            
    return render(request, 'user/verify_otp.html')

def registration_pending(request):
    return render(request, 'user/registration_pending.html')

def resend_otp(request):
    user_id = request.session.get('user_id', None)
    
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, f"Please register again.")
        return redirect('register-form')
    
    if user.is_active:
        messages.error(request, f"Your account is already verified")
        return redirect('login')
    
    last_otp_sent = EmailOTP.objects.filter(user=user, is_verified=False).order_by('-created_at').first()
    
    if last_otp_sent and not last_otp_sent.is_expired():
        messages.warning(request, f"Your OTP is still valid. Please check your email.")
        return redirect('verify-otp')
    
    if last_otp_sent and last_otp_sent.is_expired():
        last_otp_sent.delete()
    
    # generate new OTP
    try:
        with transaction.atomic():
            otp = EmailOTP.generate_otp()
            otp_obj = EmailOTP.objects.create(otp=otp, user=user)
            send_email(user.email, otp)
    except Exception:
        messages.error(request, "Couldn't send OTP. Please try again.")
        return redirect('verify-otp')

    request.session['otp_id'] = otp_obj.id
    messages.success(request, "The new OTP has been sent to your email.")
    return redirect('verify-otp')

def verify_otp_expired(request):
    for key in ('user_id', 'otp_id', 'business_id'):
        request.session.pop(key, None)
    return render(request, 'user/verify_otp_expired.html')
    
def user_login(request):
    user = None
    page = 'login'

    if request.method == 'POST':
        
        # Honeypot — silently drop bot submissions
        if request.POST.get('website'):
            return redirect('login')   # or 'register-form'

        username = request.POST.get('username').lower().strip()
        password = request.POST.get('password')
        
        # Block staff whose seat is locked by the owner's plan downgrade (over seat cap)
        if staff_seat_locked(user):
            messages.error(request,
                "Your access is paused — the owner's plan no longer covers your staff seat. "
                "Ask them to upgrade or re-activate your seat.")
            return redirect('login')

        try:
            user_obj = User.objects.get(username=username)
        except User.DoesNotExist:
            user_obj = None
            messages.error(request, f"Username OR Password is incorrect. Please try again.")
            # print('user_obj', user_obj)
            
        if user_obj and user_obj.locked_until and timezone.now() > user_obj.locked_until: # checks if the user is not locked anymore to reset the attempts
            user_obj.reset_attempts()
            user_obj.save()    
            
        if user_obj and user_obj.is_locked(): # checks if user exists and checks if account is locked
            messages.error(request, f"Your account is temporarily locked for 10 mins. Please try again later.")
        else:
            user = authenticate(username=username, password=password)

        if user:
            # Block staff whose owner has deactivated their business account
            if user.role == 'staff' and user.owner  and not user.owner.is_active:
                messages.error(request, f"Your business account is currently inactive. Please contact your owner.")
                return redirect('login')
            
            login(request, user)
            if user.role == 'staff':
                business = BusinessProfile.objects.filter(employees__staff_user=request.user).first()
            else:
                business = BusinessProfile.objects.filter(user=request.user).first()
            user_obj.reset_attempts()
            user_obj.last_login = timezone.now()
            user_obj.save(update_fields=['last_login'])
            
            # No business yet → send owner to create one, staff to a safe page
            if business is None:
                if user.role == 'owner':
                    return redirect('business-profile-create')
                return redirect('login')

            bp = getattr(business, 'plan', None)
            if user.role == 'owner' and bp and bp.has_dashboard():
                return redirect('dashboard', business_slug=business.slug)
            else:
                return redirect('product-list', business_slug=business.slug)
        else:
            if user_obj and not user_obj.is_locked(): # checks if user exists and the user is not locked
                user_obj.register_failed_login()
                print('Total Attempts:',user_obj.failed_attempts)
                user_obj.save()
                messages.error(request, f"Username OR Password is incorrect. Please try again.")

            return redirect('login')
            
    context = {'page': page}
    return render(request, 'user/register_and_login_form.html', context)

@login_required(login_url='login')
def user_profile(request, user_id, slug):
    user = get_object_or_404(User, slug=slug, id=user_id)
    business = BusinessProfile.objects.filter(user=user).first()
    
    if user != request.user:
        return render(request, 'core/no_access.html', status=403)
    
    context = {'user': user, 'business': business}
    return render(request, 'user/user_profile.html', context)

@login_required(login_url='login')
@require_POST
def set_theme(request):
    """Save the user's light/dark preference. Called by the topbar toggle
    (fetch, ignores response) and the profile Appearance buttons (form POST)."""
    changed = []
    theme = request.POST.get('theme')
    if theme in ('light', 'dark'):
        request.user.theme = theme
        changed.append('theme')
    sidebar = request.POST.get('sidebar_theme')
    if sidebar in ('match', 'light', 'dark'):
        request.user.sidebar_theme = sidebar
        changed.append('sidebar_theme')
    if changed:
        request.user.save(update_fields=changed)
    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return JsonResponse({'ok': True, 'theme': request.user.theme})

@login_required(login_url='login')
def user_edit_profile(request, user_id, slug):
    user = get_object_or_404(User, slug=slug, id=user_id)

    if user != request.user:
            return render(request, 'core/no_access.html', status=403)
        
    if request.method == 'POST':
        
        # Honeypot
        if request.POST.get('website'):
            return redirect('user-profile', slug=user.slug, user_id=user_id)
        
        form = UpdateUserForm(request.POST, instance=user)
    
        if form.is_valid():
            user = form.save(commit=False)
            user.username = user.username.lower()
            user.name = user.name.title()
            user.first_name = user.first_name.title()
            user.last_name = user.last_name.title()
            user.save()
            request.session['active_business_slug'] = user.slug
            messages.success(request, f"Your profile has been updated.")
            return redirect('settings', business_slug=user.slug)
        
        else:
            print(form.errors)
            
    else:
        form = UpdateUserForm(instance=user)
    
    context = {'form': form, 'page': 'user-edit-profile'}
    return render(request, 'user/edit_user_profile_form.html', context)

@login_required(login_url='login')
def user_edit_password(request):
    is_hx = request.headers.get('HX-Request')

    if request.method == 'POST':
        if request.POST.get('website'):  # honeypot
            if is_hx:
                return HttpResponse(status=204)
            return redirect('settings', business_slug=request.user.slug)

        form = StyledPasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            user = form.save()
            request.session['active_business_slug'] = user.slug
            user.password_changed_at = timezone.now()
            user.save(update_fields=['password_changed_at'])
            logout(request)
            messages.success(request, "Your password has been updated.")
            if is_hx:
                resp = HttpResponse(status=204)
                resp['HX-Redirect'] = reverse('settings', kwargs={'business_slug': user.slug})
                return resp
            return redirect('settings', business_slug=user.slug)


        # invalid
        if is_hx:
            return render(request, 'core/partials/_password_modal.html', {'form': form})
        return render(request, 'user/edit_user_profile_form.html', {'form': form, 'page': 'user-edit-password'})

    form = StyledPasswordChangeForm(user=request.user)
    if is_hx:
        return render(request, 'core/partials/_password_modal.html', {'form': form})
    return render(request, 'user/edit_user_profile_form.html', {'form': form, 'page': 'user-edit-password'})


@login_required(login_url='login')
# def user_reset_password(request):
#     if request.method == 'POST':
#         form = PasswordResetForm()
        
#         if form.is_valid():
#             user = form.save()
#             messages.success(request, f"Password has succesfully reset.")
#             return redirect('user-profile', user.request.slug)
    
#     else:
#         form = PasswordResetForm()
        
#     context = {'form': form, 'page': 'user-reset-password'}
#     return render(request, 'user/edit_user_profile_form.html', context)

@login_required(login_url='login')
def user_deactivate(request, user_id, slug):
    user = get_object_or_404(User, slug=slug, id=user_id)
    if user != request.user:
        return render(request, 'core/no_access.html', status=403)
    
    if request.method == 'POST':
        user.is_active = False
        user.save()
        logout(request)
        messages.success(request, 'Your account has been deactivated.')
        return redirect('landing')

    context = {'user': user}
    return render(request, 'user/user_deactivate.html', context)

def user_logout(request):
    logout(request)
    return redirect('landing')

def business_list(request):
    user = request.user
    businesses = BusinessProfile.objects.filter(user=user)
    
    context = {'businesses': businesses}
    return render(request, 'user/business_list.html', context)
    
@login_required(login_url='login')
def business_profile_create(request):
    from subscription.models import BUNDLE_COUNT
    
    sub = request.user.subscription
    current_count = request.user.business_profiles.count()
    cap = BUNDLE_COUNT[sub.bundle]
    at_cap = current_count >= cap
    
    rate_key = f'biz_create:{request.user.id}'
    rate_limited = cache.get(rate_key) is not None
    
    if request.method == 'POST':
        form = BusinessProfileForm(request.POST)
        
        if at_cap:
            messages.info(
                request,
                f"You've reached your limit of {cap} business(es). Contact support to add more."
            )
            return redirect('user-profile', user_id=request.user.id, slug=request.user.slug)

        # If rate-limited, require the soft "confirm human" checkbox
        if rate_limited and not request.POST.get('confirm_human'):
            messages.warning(
                request,
                "Please wait a moment before adding another business. "
                "Confirm you're human and try again."
            )
            context = {'form': form, 'show_human_check': True}
            return render(request, 'user/business_profile_create.html', context)
        
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            profile.save()
            request.session['active_business_slug'] = profile.slug   # land in the new business
            cache.set(rate_key, True, timeout=60)
            messages.success(request, "Your business profile has been created successfully.")
            return redirect('user-profile', user_id=request.user.id, slug=request.user.slug)

        else:
            messages.error(request, "Cafe and Restaurant are coming soon.")
            return redirect('business-profile-create')

    else:
        form = BusinessProfileForm()

    context = {
        'form': form,
        'show_human_check': rate_limited,
        'at_cap': at_cap,
        'cap': cap,
        'current_count': current_count,
    }
    return render(request, 'user/business_profile_create.html', context)

@login_required(login_url='login')
def business_profile_detail(request, business_id, business_slug):

    business = get_object_or_404(BusinessProfile, user=request.user, id=business_id, slug=business_slug)
    
    context = {'business': business, 'section': 'user'}
    return render(request, 'user/business_profile_detail.html', context)

@login_required(login_url='login')
def business_profile_update(request, business_slug, business_id):
    current_user = request.user
    business = get_object_or_404(BusinessProfile, user=request.user, id=business_id, slug=business_slug)
    
    if request.method == 'POST':
        form = BusinessProfileForm(request.POST, instance=business)
        
        if form.is_valid():
            obj = form.save(commit=False)
            obj.business_name = obj.business_name.title()
            obj.save()
            request.session['active_business_slug'] = obj.slug
            
            return redirect('settings', business_slug=obj.user.slug)
    else:
        form = BusinessProfileForm(instance=business)
    
    context = {'form': form, 'business': business, 'section': 'user', 'current_user': current_user}
    return render(request, 'user/business_profile_update.html', context)

@login_required(login_url='login')
def regenerate_invite_code(request, business_id, business_slug):
    business = get_object_or_404(BusinessProfile, user=request.user, id=business_id, slug=business_slug)
    next_url = request.GET.get('next', '')

    # GET via HTMX → open the confirm modal
    if request.method == 'GET':
        if request.headers.get('HX-Request'):
            action = reverse('regenerate-invite-code',
                             kwargs={'business_id': business.id, 'business_slug': business.slug})
            return render(request, 'core/partials/_confirm_modal.html', {
                'cm_title': 'Generate new code?',
                'cm_subtitle': business.business_name,
                'cm_note': 'The current code stops working right away. Anyone who already has it will need the new one.',
                'cm_action': f"{action}?next={next_url}",
                'cm_label': 'Generate new code',
                'cm_tone': 'danger',
                'cm_icon': 'bi-arrow-repeat',
                'cm_btn_icon': 'bi-arrow-repeat',
            })
        return redirect('business-profile-detail', business_id=business.id, business_slug=business.slug)

    # POST → regenerate
    business.regenerate_invite_code()
    messages.success(request, "New invite code generated — the old one no longer works.")

    fallback = reverse('business-profile-detail',
                       kwargs={'business_id': business.id, 'business_slug': business.slug})
    dest = next_url if next_url.startswith('/') else fallback

    if request.headers.get('HX-Request'):
        resp = HttpResponse(status=204)
        resp['HX-Redirect'] = dest
        return resp
    return redirect(dest)

@login_required(login_url='login')
@require_POST
def toggle_accepting_staff(request, business_id, business_slug):
    business = get_object_or_404(BusinessProfile, user=request.user, id=business_id, slug=business_slug)
    business.accepting_staff = not business.accepting_staff
    business.save(update_fields=['accepting_staff'])
    messages.success(request,
        "Staff sign-up is now ON." if business.accepting_staff else "Staff sign-up is now OFF.")
    next_url = request.POST.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect('business-profile-detail', business_id=business.id, business_slug=business.slug)



@login_required(login_url='login')
def settings(request, business_slug):
    
    return render(request, 'user/settings.html')


@login_required(login_url='login')
def change_email_form(request):
    if request.method == 'POST':
        # Honeypot
        if request.POST.get('website'):
            return redirect('change-email-form')

        new_email = request.POST.get('new_email', '').strip().lower()
        current_password = request.POST.get('current_password', '')

        # Verify current password (confirms it's really the user)
        user = authenticate(username=request.user.username, password=current_password)
        if user is None:
            messages.error(request, "Incorrect password.")
            return redirect('change-email-form')

        if not new_email:
            messages.error(request, "Please enter a new email address.")
            return redirect('change-email-form')

        if new_email == (request.user.email or '').lower():
            messages.error(request, "That's already your current email.")
            return redirect('change-email-form')

        if User.objects.filter(email__iexact=new_email).exclude(pk=request.user.pk).exists():
            messages.error(request, "That email is already in use.")
            return redirect('change-email-form')

        # Generate and send OTP to the NEW email
        try:
            with transaction.atomic():
                EmailOTP.objects.filter(user=request.user, is_verified=False).delete()
                otp = EmailOTP.generate_otp()
                otp_obj = EmailOTP.objects.create(user=request.user, otp=otp)
                send_email(new_email, otp)
        except Exception:
            messages.error(request, "Couldn't send verification email. Please try again.")
            return redirect('change-email-form')

        request.session['change_email_pending'] = new_email
        request.session['change_email_otp_id'] = otp_obj.id
        messages.success(request, f"Verification code sent to {new_email}.")
        return redirect('change-email-verify')

    return render(request, 'user/change_email_form.html')


@login_required(login_url='login')
def change_email_verify(request):
    pending_email = request.session.get('change_email_pending')
    otp_id = request.session.get('change_email_otp_id')

    if not pending_email or not otp_id:
        messages.error(request, "Please start the change email process again.")
        return redirect('change-email-form')

    try:
        otp_obj = EmailOTP.objects.get(id=otp_id, user=request.user, is_verified=False)
    except EmailOTP.DoesNotExist:
        for k in ('change_email_pending', 'change_email_otp_id'):
            request.session.pop(k, None)
        messages.error(request, "Verification code is no longer valid.")
        return redirect('change-email-form')

    if otp_obj.is_expired():
        otp_obj.delete()
        for k in ('change_email_pending', 'change_email_otp_id'):
            request.session.pop(k, None)
        messages.error(request, "Verification code expired. Please try again.")
        return redirect('change-email-form')

    if request.method == 'POST':
        entered = request.POST.get('otp', '').strip()
        if entered == otp_obj.otp:
            with transaction.atomic():
                request.user.email = pending_email
                request.user.save(update_fields=['email'])
                otp_obj.is_verified = True
                otp_obj.save(update_fields=['is_verified'])
                
            business = request.user.business_profiles.first()
            for k in ('change_email_pending', 'change_email_otp_id'):
                request.session.pop(k, None)
            messages.success(request, "Email updated successfully.")
            return redirect('settings', business_slug=business.slug)
        else:
            messages.error(request, "Invalid code. Please try again.")
            
    
    context = {
        'pending_email': pending_email,
        'expires_at_iso': otp_obj.expires_at.isoformat() if hasattr(otp_obj, 'expires_at') else (otp_obj.created_at + datetime.timedelta(minutes=5)).isoformat(),
    }
    return render(request, 'user/change_email_verify.html', context)



@login_required(login_url='login')
def cash_drawer_settings(request, business_slug, business_id):
    business = get_object_or_404(BusinessProfile, user=request.user, id=business_id, slug=business_slug)

    plan = getattr(business, 'plan', None)
    if not plan or not plan.has_timecards():
        messages.warning(request, 'Cash drawer settings are available on Standard plan and up.')
        return redirect('settings', business_slug=request.user.slug)

    locked = is_opening_cash_locked(business)

    if request.method == 'POST':
        form = BusinessCashDrawerForm(request.POST, instance=business, locked=locked)
        if form.is_valid():
            form.save()
            messages.success(request, 'Cash drawer settings updated.')
            return redirect('settings', business_slug=request.user.slug)
    else:
        form = BusinessCashDrawerForm(instance=business, locked=locked)

    context = {'form': form, 'business': business, 'locked': locked, 'section': 'user'}
    return render(request, 'user/cash_drawer_settings.html', context)

@login_required(login_url='login')
def business_features(request, business_slug, business_id):
    business = get_object_or_404(BusinessProfile, user=request.user, id=business_id, slug=business_slug)

    if request.method == 'POST':
        form = BusinessFeaturesForm(request.POST, instance=business)
        if form.is_valid():
            form.save()
            messages.success(request, "Features updated.")
            return redirect('settings', business_slug=request.user.slug)
    else:
        form = BusinessFeaturesForm(instance=business)

    context = {'form': form, 'business': business, 'section': 'user'}
    return render(request, 'user/business_features.html', context)


