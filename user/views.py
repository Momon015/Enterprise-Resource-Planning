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

from user.models import User, EmailOTP
from user.forms import RegisterForm, UpdateUserForm, StyledPasswordChangeForm

import json 
import pprint

from django.db import transaction
from django.contrib.auth.hashers import make_password

from Expense.models import Employee

# Create your views here.

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
            owner_username = form.cleaned_data.get('owner_username', '').lower().strip()
            print(owner_username)
            user = User.objects.create(username=username, is_active=False)
            user.set_password(raw_password)
            user.email = email
            
            if owner_username:
                try:
                    owner = User.objects.get(username=owner_username, role='owner')
                    user.role = 'staff'
                    user.owner = owner
                except User.DoesNotExist:
                    user.delete()
                    messages.error(request, f"Owner username not found. Please check and try again later.")
                    return redirect('register-form')
            else:
                user.role = 'owner'
            
            user.save()
            # save user ID
            request.session['user_id'] = user.id
            
            # generate OTP
            otp = EmailOTP.generate_otp()
            
            # save otp_obj in DB
            otp_obj = EmailOTP.objects.create(user=user, otp=otp)
            request.session['otp_id'] = otp_obj.id
            
            # send email
            send_email(user.email, otp)
            
            messages.success(request, f"The OTP has been sent to your email.")
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
    
    print('otp_obj', otp_obj)

    if request.method == 'POST':
        entered_otp = request.POST.get('otp', None)
        
        if otp_obj.is_expired():
            request.session.pop('otp_id', None)
            otp_obj.delete()
            messages.error(request, f"The OTP has been expired. Please request for a new OTP.")
            return redirect('expired-otp')
        else:

            if entered_otp == otp_obj.otp:
                try:
                    user = User.objects.get(id=user_id)
                except User.DoesNotExist:
                    messages.error(request, f"Please register again.")
                    return redirect('register-form')
                
                # save is_active manually
                user.is_active=True
                user.save()
                
                otp_obj.is_verified=True
                otp_obj.save()
                
                if user.role == 'staff':
                    Employee.objects.create(
                        user=user.owner,
                        staff_user=user,
                        name=user.username,
                        daily_rate=0,
                    )
                
                # clear sessions
                for key in ('user_id', 'otp_id'):
                    request.session.pop(key, None)
                
                login(request, user)
                messages.success(request, f"Your account has been successfully created.")
                if user.role == 'staff':
                    return redirect('product-list')
                else:
                    return redirect('user-profile', slug=user.username)
            
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
    
    last_otp_sent = EmailOTP.objects.filter(user=user, is_verified=False).first()
    
    if last_otp_sent and not last_otp_sent.is_expired():
        messages.warning(request, f"Your OTP is still valid. Please check your email.")
        return render(request, 'user/verify_otp.html')
    
    if last_otp_sent and last_otp_sent.is_expired:
        last_otp_sent.delete()
    
    # generate new OTP
    otp = EmailOTP.generate_otp()
    print('otp', otp)
    otp_obj = EmailOTP.objects.create(otp=otp, user=user)
    request.session['otp_id'] = otp_obj.id
    
    send_email(user.email, otp)

    messages.success(request, f"The new OTP has been sent to your email.")
    return redirect('verify-otp')

def verify_otp_expired(request):
    
    return render(request, 'user/verify_otp_expired.html')
    
def user_login(request):
    user = None
    page = 'login'
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        try:
            user_obj = User.objects.get(username=username)
        except User.DoesNotExist:
            user_obj = None
            messages.error(request, f"Username OR Password is incorrect. Please try again.")
            print('user_obj', user_obj)
            
        if user_obj and user_obj.locked_until and timezone.now() > user_obj.locked_until: # checks if the user is not locked anymore to reset the attempts
            user_obj.reset_attempts()
            user_obj.save()    
            
        if user_obj and user_obj.is_locked(): # checks if user exists and checks if account is locked
            messages.error(request, f"Your account is temporarily locked for 10 mins. Please try again later.")
        else:
            user = authenticate(username=username, password=password)

        if user:
            login(request, user)
            user_obj.reset_attempts()
            user_obj.last_login = timezone.now()
            user_obj.save(update_fields=['last_login'])
            if user.role == 'owner':
                return redirect('material-list')
            else:
                return redirect('product-list')
        else:
            if user_obj and not user_obj.is_locked(): # checks if user exists and the user is not locked
                user_obj.register_failed_login()
                print('Total Attempts:',user_obj.failed_attempts)
                user_obj.save()
                messages.error(request, f"Username OR Password is incorrect. Please try again.")

            return redirect('login')
            
    context = {'page': page}
    return render(request, 'user/register_and_login_form.html', context)
    
def user_profile(request, slug):
    user = get_object_or_404(User, slug=slug)
    
    
    context = {'user': user}
    return render(request, 'user/user_profile.html', context)

def user_edit_profile(request, slug):
    user = get_object_or_404(User, slug=slug)
    
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
            return redirect('user-profile', slug=slug)
        
        else:
            print('form errors: ', form.errors)
            
    else:
        form = UpdateUserForm(instance=user)
    
    context = {'form': form, 'page': 'user-edit-profile'}
    return render(request, 'user/edit_user_profile_form.html', context)

def user_edit_password(request):
    if request.method == 'POST':
        form = StyledPasswordChangeForm(user=request.user, data=request.POST)
        
        if form.is_valid():
            user = form.save()
            user.password_changed_at = timezone.now()
            user.save(update_fields=['password_changed_at'])
            update_session_auth_hash(request, user) # keeps the user logged in
            messages.success(request, f"Your Password has succesfully updated.")
            return redirect('user-profile', request.user.slug)
        else:
            print('form errors:', form.errors)
        
    else:
        form = StyledPasswordChangeForm(user=request.user)
    
    context = {'form': form, 'page': 'user-edit-password'}
    return render(request, 'user/edit_user_profile_form.html', context)

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

def user_delete(request, slug):
    user = get_object_or_404(User, slug=slug)
    
    if request.method == 'POST':
        user.delete()
        return redirect('product-list')

    context = {'user': user}
    return render(request, 'user/user_delete.html', context)
    
def user_logout(request):
    logout(request)
    return redirect('product-list')