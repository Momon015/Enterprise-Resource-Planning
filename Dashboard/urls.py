from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    path('view/dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/set-basis/', views.set_dashboard_basis, name='set-dashboard-basis'),
]
