from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    
    path('', views.landing, name='landing'),
    
    path('register-form/', views.register_form, name='register-form'),
    path('register-form/verify-otp/', views.verify_otp, name='verify-otp'),
    path('register-form/resend-otp/', views.resend_otp, name='resend-otp'),
    path('register-form/expired-otp/', views.verify_otp_expired, name='expired-otp'),
    
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    
    
    
    path('profile/<slug:slug>/', views.user_profile, name='user-profile'),
    path('delete/<slug:slug>/', views.user_delete, name='user-delete'),
    
    path('edit/profile/password/', views.user_edit_password, name='user-edit-password'),
    # path('edit/profile/reset/password/', views.user_reset_password, name='user-reset-password'),
    path('edit/profile/<slug:slug>/', views.user_edit_profile, name='user-edit-profile'),

]
