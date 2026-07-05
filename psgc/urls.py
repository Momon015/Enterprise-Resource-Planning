from django.urls import path
from . import views

# Create your urls here.

urlpatterns = [
    path('regions/',   views.regions,   name='psgc-regions'),
    path('provinces/', views.provinces, name='psgc-provinces'),
    path('cities/',    views.cities,    name='psgc-cities'),
    path('barangays/', views.barangays, name='psgc-barangays'),
]
