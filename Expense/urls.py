from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    # purchase urls
    path('view/purchase-history/', views.purchase_history, name='purchase-list'),
    path('view/<str:username>/<int:purchase_id>/purchase-history/', views.purchase_detail, name='purchase-detail'),
    
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
    # path('create/employee-detail/', views.employee_create, name='employee-create'),
    path('view/<str:employee_id>/employee-detail/', views.employee_detail, name='employee-detail'),
    path('update/<str:employee_id>/employee-detail/', views.employee_update, name='employee-update'),
    path('delete/<str:employee_id>/employee-detail/', views.employee_delete, name='employee-delete'),
    
    path('view/waste-list/', views.waste_list, name='expense-waste-list'),
    path('view/<str:username>/<str:waste_id>/waste/', views.waste_material_detail, name='expense-waste-detail'),
    path('create/product/waste/', views.waste_product_create, name='product-waste-create'),
    path('create/material/waste/', views.waste_material_create, name='material-waste-create'),
    
    
    # expense
    path('list/', views.expense_list, name='expense-list'),
    path('create/', views.expense_create, name='expense-create'),
    path('user/<str:username>/view/<str:expense_id>/', views.expense_detail, name='expense-detail'),
    
    path('misc/create/', views.misc_expense_create, name='misc-expense-create'),
    path('misc/list/', views.misc_expense_list, name='misc-expense-list'),
    path('misc/<str:username>/view/<str:misc_expense_id>/', views.misc_expense_detail, name='misc-expense-detail'),
    path('misc/<str:username>/update/<str:misc_expense_id>/', views.misc_expense_update, name='misc-expense-update'),
    path('misc/<str:username>/delete/<str:misc_expense_id>/', views.misc_expense_delete, name='misc-expense-delete'),
    
]
