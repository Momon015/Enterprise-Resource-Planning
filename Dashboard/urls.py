from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [

    path('view/dashboard/', views.dashboard, name='dashboard')
]
