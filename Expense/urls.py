from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    # purchase urls
    path('view/purchase-history/', views.purchase_history, name='purchase-list'),
    path('view/purchase-history/<int:purchase_id>/detail/', views.purchase_detail, name='purchase-detail'),
    
    # cart sessions
    path('add-to-cart/<int:id>/', views.add_to_cart, name='add-to-cart'),
    path('view-cart/', views.view_cart, name='view-cart'),
    path('view/checkout-summary/', views.view_cart_summary, name='view-cart-summary'),
    path('view/confirm/purchase-summary/', views.confirm_purchase_summary, name='confirm-purchase-summary'),
    path('view/<int:purchase_id>/purchase-summary/', views.view_purchase_summary, name='view-purchase-summary'),

    # edit material's quantity from session
    path('<int:id>/edit/quantity/', views.cart_edit_material, name='cart-edit-material'),
    
    # delete material from session
    path('<int:id>/delete/', views.cart_remove_materials, name='cart-remove-material'),
    
    # edit total price
    path('edit/price/<int:material_id>/', views.edit_total_price, name='edit-total-price'),
    
    # edit material's price from session
    path('', views.cart_discount_material, name='cart-discount-material'),
    
    # clear cart sessions
    path('view/cart/clear/', views.clear_cart, name='clear-cart'),
    
    # waste
    path('view/waste-list/', views.waste_list, name='expense-waste-list'),
    path('view/<str:waste_id>/material-waste/detail/', views.waste_material_detail, name='material-waste-detail'),
    path('product/waste/create/', views.waste_product_create, name='product-waste-create'),
    path('material/waste/create/', views.waste_material_create, name='material-waste-create'),
    
    # expense
    path('view/list/', views.expense_list, name='expense-list'),
    path('create/', views.expense_create, name='expense-create'),
    path('view/<str:date>/', views.expense_detail, name='expense-detail'),
    
    # misc_expense
    path('misc/create/', views.misc_expense_create, name='misc-expense-create'),
    path('misc/list/', views.misc_expense_list, name='misc-expense-list'),
    path('misc/<str:misc_expense_id>/detail/', views.misc_expense_detail, name='misc-expense-detail'),
    path('misc/<str:misc_expense_id>/update/', views.misc_expense_update, name='misc-expense-update'),
    path('misc/<str:misc_expense_id>/delete/', views.misc_expense_delete, name='misc-expense-delete'),
    

    path('return/<int:return_id>/', views.purchase_return_detail, name='purchase-return-detail'),
    path('purchase/<int:purchase_id>/payment/add/', views.add_purchase_payment, name='add-purchase-payment'),
    
    path('payables/', views.purchase_payables, name='purchase-payables'),
    path('purchase/<int:purchase_id>/payment/<int:payment_id>/success/',views.purchase_payment_recorded, name='purchase-payment-success'),

    path('returns/list/', views.purchase_return_list, name='purchase-return-list'),
    path('purchase/<int:purchase_id>/return/create/', views.purchase_return_create, name='purchase-return-create'),
    path('return/<int:return_id>/success/',views.purchase_return_recorded, name='purchase-return-success'),



]
