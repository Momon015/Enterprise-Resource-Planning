from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    
    path('', views.landing, name='landing'),
    path('<str:business_slug>/settings/', views.settings, name='settings'),

    
    path('registration/', views.register_form, name='register-form'),
    path('registration/verify-otp/', views.verify_otp, name='verify-otp'),
    path('registration/pending/', views.registration_pending, name='registration-pending'),
    path('registration/resend-otp/', views.resend_otp, name='resend-otp'),
    path('registration/expired-otp/', views.verify_otp_expired, name='expired-otp'),
    
    path('login/', views.user_login, name='login'),
    path('logout/', views.user_logout, name='logout'),
    
    path('settings/profile/<int:user_id>/<slug:slug>/', views.user_profile, name='user-profile'),
    path('settings/deactivate/<int:user_id>/<slug:slug>/', views.user_deactivate, name='user-deactivate'),
    path('settings/theme/', views.set_theme, name='set-theme'),
    
    path('settings/edit/profile/password/', views.user_edit_password, name='user-edit-password'),
    # path('settings/edit/profile/reset/password/', views.user_reset_password, name='user-reset-password'),
    path('settings/edit/profile/<int:user_id>/<slug:slug>/', views.user_edit_profile, name='user-edit-profile'),
    
    # change email
    path('settings/change-email/', views.change_email_form, name='change-email-form'),
    path('settings/change-email/verify/', views.change_email_verify, name='change-email-verify'),

    
    # business profile
    path('settings/business/list/', views.business_list, name='business-list'),
    path('settings/business-profile/create/', views.business_profile_create, name='business-profile-create'),
    path('settings/business/<int:business_id>/<slug:business_slug>/business-profile/detail/', views.business_profile_detail, name='business-profile-detail'),
    path('settings/business/<int:business_id>/<slug:business_slug>/business-profile/update/', views.business_profile_update, name='business-profile-update'),

    path('settings/business/<int:business_id>/<slug:business_slug>/cash-drawer/', views.cash_drawer_settings, name='cash-drawer-settings'),
    path('settings/business/<int:business_id>/<slug:business_slug>/features/', views.business_features, name='business-features'),
    path('settings/business/<int:business_id>/<slug:business_slug>/invite-code/regenerate/', views.regenerate_invite_code, name='regenerate-invite-code'),
    path('settings/business/<int:business_id>/<slug:business_slug>/staff-signup/toggle/', views.toggle_accepting_staff, name='toggle-accepting-staff'),

    path('settings/business/archived/', views.archived_businesses, name='archived-businesses'),
    path('settings/business/<int:business_id>/<slug:business_slug>/archive/', views.business_archive, name='business-archive'),
    path('settings/business/<int:business_id>/<slug:business_slug>/restore/', views.business_restore, name='business-restore'),


]
