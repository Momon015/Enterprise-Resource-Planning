from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404
from django.views.generic import ListView, UpdateView, CreateView, DeleteView, FormView, DetailView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages

from django.utils import timezone
from datetime import timedelta
import random

from django.views.decorators.http import require_POST
from django.urls import reverse

from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth import update_session_auth_hash

from django.core.paginator import Paginator

from core.models import Category
from core.utils.email import send_email

from user.models import User, EmailOTP, BusinessProfile
from user.forms import RegisterForm, UpdateUserForm, StyledPasswordChangeForm, BusinessProfileForm

from core.utils.owner import get_owner, get_queryset_for_user, permission_required

import json 
import pprint

from django.db import transaction
from django.contrib.auth.hashers import make_password

from Expense.models import Employee

from django.utils.text import slugify

# Create your views here.

def get_ip(request):
    ip = request.META.get('REMOTE_ADDR')
    return HttpResponse(f"Your IP is {ip}")


def landing(request):
    # if request.user.is_authenticated:
    #     return redirect('business-list')
    return render(request, 'landing_page.html')

def register_form(request):
    page = 'register-form'
    
    if request.method == 'POST':
        # MANAGER: CLEANING UNVERIFIED USERS
        User.cleanup.unverified_users(minutes=5) # override the 1 hr 
        
        form = RegisterForm(request.POST)

        if form.is_valid():
 
            username = form.cleaned_data['username'].lower()
            email = form.cleaned_data['email']
            raw_password = form.cleaned_data['password1']
            
            # staff
            owner_username = form.cleaned_data.get('owner_username', '').lower().strip()
            owner_business = form.cleaned_data.get('owner_business', '')

            try:
                with transaction.atomic():
                    # build user (no save yet)
                    user = User(username=username, email=email, is_active=False)
                    user.set_password(raw_password)

                    if owner_username and owner_business:
                        business = BusinessProfile.objects.get(
                            user__role='owner',
                            user__username=owner_username,
                            business_name=owner_business,
                        )
                        request.session['business_id'] = str(business.id)
                        user.role = 'staff'
                        user.owner = business.user
                    else:
                        user.role = 'owner'
                    user.save()  # single write

                    otp = EmailOTP.generate_otp()
                    otp_obj = EmailOTP.objects.create(user=user, otp=otp)

                    send_email(user.email, otp)  # if this raises, atomic rolls back

            except BusinessProfile.DoesNotExist:
                messages.error(request, "Business name or Owner username not found.")
                return redirect('register-form')
            # except Exception:
            #     messages.error(request, "Couldn't send verification email. Please try again.")
            #     return redirect('register-form')

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
                    user.is_active = True
                    user.save()

                    otp_obj.is_verified = True
                    otp_obj.save()

                    if user.role == 'staff':
                        business_id = request.session.get('business_id', None)
                        if not business_id:
                            raise ValueError("Missing business session.")

                        business = BusinessProfile.objects.get(id=business_id)
                        Employee.objects.create(
                            user=business.user,
                            business=business,
                            staff_user=user,
                            name=user.name or user.username,
                            daily_rate=0,
                        )
            except (BusinessProfile.DoesNotExist, ValueError):
                # transaction rolled back — user is still inactive, OTP unverified
                user.delete()
                messages.error(request, "Business name or Owner name not found.")
                return redirect('register-form')
            
            # clear sessions
            for key in ('user_id', 'otp_id', 'business_id'):
                request.session.pop(key, None)
            
            login(request, user)
            if request.user.role == 'owner':
                return redirect('business-profile-create')
            else:
                messages.success(request, f"Your account has been successfully created.")
                return redirect('user-profile', slug=user.username, user_id=user.id)
        else:
            messages.error(request, "Invalid OTP. Please try again.")
            
    return render(request, 'user/verify_otp.html')

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
        username = request.POST.get('username').lower().strip()
        password = request.POST.get('password')

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
            login(request, user)
            if user.role == 'staff':
                business = BusinessProfile.objects.filter(employees__staff_user=request.user).first()
            else:
                business = BusinessProfile.objects.filter(user=request.user).first()
            user_obj.reset_attempts()
            user_obj.last_login = timezone.now()
            user_obj.save(update_fields=['last_login'])
            if user.role == 'owner':
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
def user_edit_profile(request, user_id, slug):
    user = get_object_or_404(User, slug=slug, id=user_id)

    if user != request.user:
            return render(request, 'core/no_access.html', status=403)
        
    if request.method == 'POST':
        form = UpdateUserForm(request.POST, instance=user)
    
        if form.is_valid():
            user = form.save(commit=False)
            user.username = user.username.lower()
            user.name = user.name.title()
            user.first_name = user.first_name.title()
            user.last_name = user.last_name.title()
            user.save()
            messages.success(request, f"Your profile has been updated.")
            return redirect('user-profile', slug=user.slug, user_id=user_id)
        
        else:
            print(form.errors)
            
    else:
        form = UpdateUserForm(instance=user)
    
    context = {'form': form, 'page': 'user-edit-profile'}
    return render(request, 'user/edit_user_profile_form.html', context)

@login_required(login_url='login')
def user_edit_password(request):
    if request.method == 'POST':
        form = StyledPasswordChangeForm(user=request.user, data=request.POST)
        
        if form.is_valid():
            user = form.save()
            user.password_changed_at = timezone.now()
            user.save(update_fields=['password_changed_at'])
            update_session_auth_hash(request, user) # keeps the user logged in
            messages.success(request, f"Your Password has succesfully updated.")
            return redirect('user-profile', user_id=user.id, slug=user.slug)
        else:
            print(form.errors)
        
    else:
        form = StyledPasswordChangeForm(user=request.user)
    
    context = {'form': form, 'page': 'user-edit-password'}
    return render(request, 'user/edit_user_profile_form.html', context)

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
    # print('user', request.user)
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
    

def business_profile_create(request):
    if request.method == 'POST':
        form = BusinessProfileForm(request.POST)
        
        if form.is_valid():
            profile = form.save(commit=False)
            profile.user = request.user
            profile.save()
            messages.success(request, f"Your business profile has been created successfully.")
            return redirect('user-profile', user_id=request.user.id, slug=request.user.slug)
        else:
            messages.error(request, f"Cafe and Restaurant are coming soon.")
            return redirect('business-profile-create')

    else:
        form = BusinessProfileForm()
        
    context = {'form': form}
    return render(request, 'user/business_profile_create.html', context)

@login_required(login_url='login')
def business_profile_detail(request, business_id, slug):

    business = get_object_or_404(BusinessProfile, user=request.user, id=business_id, slug=slug)
    
    context = {'business': business, 'section': 'user'}
    return render(request, 'user/business_profile_detail.html', context)

@login_required(login_url='login')
def business_profile_update(request, business_id, slug):
    current_user = request.user
    business = get_object_or_404(BusinessProfile, user=request.user, id=business_id, slug=slug)
    
    if request.method == 'POST':
        form = BusinessProfileForm(request.POST, instance=business)
        
        if form.is_valid():
            obj = form.save(commit=False)
            obj.business_name = obj.business_name.title()
            obj.save()
            messages.success(request, f"Your business name changed successfully.")
            return redirect('user-profile', user_id=obj.user.id, slug=obj.user.slug)
    else:
        form = BusinessProfileForm(instance=business)
    
    context = {'form': form, 'business': business, 'section': 'user', 'current_user': current_user}
    return render(request, 'user/business_profile_update.html', context)

