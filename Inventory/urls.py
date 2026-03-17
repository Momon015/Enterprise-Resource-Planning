from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    
    path('view/inventory-stock/', views.view_inventory_stock, name='view-inventory-stock'),
    
    
]