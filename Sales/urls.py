from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    
    path('clear/session/', views.clear_sale, name='clear-sale'),
    # sessions
    path('view/list/', views.sale_list, name='sale-list'),
    path('view/list/<int:sale_id>/detail/', views.sale_detail, name='sale-detail'),
    path('add-to-sale/<int:product_id>/', views.add_to_sales, name='add-to-sale'),
    path('view-sale/', views.view_sale, name='view-sale'),
    
    # path('add-employee/', views.add_daily_rate_to_sale, name='add-salary-to-sale'),
    
    path('view/session/sale-summary/', views.view_session_summary, name='view-session-summary'),
    path('view/confirm-summary/', views.confirm_view_summary, name='sale-confirm-summary'),
    path('view/sale/<int:sale_id>/summary/', views.view_sale_summary, name='sale-summary'),
    
    # reciept
    path('view/sale/<int:sale_id>/receipt/', views.sale_receipt, name='sale-receipt'),
    
    path('edit/unsold-quantity/<int:product_id>/', views.edit_unsold_quantity, name='sale-edit-unsold-quantity'),
    path('edit/cost-price/<int:product_id>/', views.edit_total_selling_price, name='sale-edit-selling-price'),
    path('edit/prepared-quantity/<int:product_id>/', views.edit_view_sale_quantity, name='sale-edit-quantity'),
    path('delete/prepared-quantity/<int:product_id>/', views.delete_view_sale_quantity, name='sale-delete-quantity'),

    
    # Add sales return
    path('sale/<int:sale_id>/return/create/', views.sales_return_create, name='sales-return-create'),
    path('returns/list/', views.sales_return_list, name='sales-return-list'),
    path('return/<int:return_id>/', views.sales_return_detail, name='sales-return-detail'),
    path('return/<int:return_id>/success/',views.return_recorded,name='sales-return-success'),

    # Add sales payment
    path('receivables/', views.sales_receivables, name='sales-receivables'),
    path('sale/<int:sale_id>/payment/add/', views.add_sales_payment, name='add-sales-payment'),
    path('sale/<int:sale_id>/payment/<int:payment_id>/success/', views.payment_recorded, name='sale-payment-success'),

    
    

]