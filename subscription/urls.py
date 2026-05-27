from django.urls import path
from . import views

# Create your urls here.

from django.urls import path
from . import views

urlpatterns = [
    path('settings/', views.subscription_settings, name='subscription-settings'),
    path('settings/claim-founder/', views.claim_founder, name='subscription-claim-founder'),
    path('settings/change-plan/', views.change_business_plan, name='subscription-change-plan'),
    path('settings/manage/<str:model_key>/', views.manage_active, name='subscription-manage-active'),
    path('settings/pricing/', views.pricing, name='subscription-pricing'),
    path('settings/contact/', views.contact, name='subscription-contact'),
    
    path('settings/start-trial/', views.start_business_trial, name='subscription-start-trial'),

]
