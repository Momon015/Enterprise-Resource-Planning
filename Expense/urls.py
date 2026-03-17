from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    # purchase urls
    path('view/purchase-history/', views.purchase_history, name='purchase-list'),
    path('view/<int:purchase_id>/purchase-history/', views.purchase_detail, name='purchase-detail'),
    
    # cart sessions
    path('add-to-cart/<int:id>/', views.add_to_cart, name='add-cart'),
    path('view-cart/', views.view_cart, name='view-cart'),
    path('view/checkout-summary/', views.view_cart_summary, name='view-checkout-summary'),
    path('view/confirm/purchase-summary/', views.confirm_purchase_summary, name='purchase-summary'),
    path('view/purchase-summary/<int:purchase_id>/', views.view_purchase_summary, name='view-purchase-summary'),

    # edit material's quantity from session
    path('edit/<int:id>/', views.cart_edit_material, name='cart-edit-material'),
    
    # delete material from session
    path('delete/<int:id>/', views.cart_remove_materials, name='cart-remove-material'),
    
    # edit material's price from session
    path('', views.cart_discount_material, name='cart-discount-material'),
    
    # edit total price
    path('edit/price/<int:material_id>/', views.edit_total_price, name='sale-edit-total-price'),

    # clear cart sessions
    path('view/cart/clear/', views.clear_cart, name='clear-cart'),
    
    # employee
    path('view/employees-list/', views.employee_list, name='employee-list'),
    path('create/employee-detail/', views.employee_create, name='employee-create'),
    path('view/<str:employee_id>/employee-detail/', views.employee_detail, name='employee-detail'),
    path('update/<str:employee_id>/employee-detail/', views.employee_update, name='employee-update'),
    path('delete/<str:employee_id>/employee-detail/', views.employee_delete, name='employee-delete'),
]
