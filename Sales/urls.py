from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    
    path('clear/session/', views.clear_sale, name='clear-sale'),
    
    path('view/list/', views.sale_list, name='sale-list'),
    path('view/list/user/<str:username>/<int:sale_id>/detail/', views.sale_detail, name='sale-detail'),
    path('add-to-sale/<int:product_id>/', views.add_to_sales, name='add-to-sale'),
    path('view-sale/', views.view_sale, name='view-sale'),
    
    # path('add-employee/', views.add_daily_rate_to_sale, name='add-salary-to-sale'),
    
    path('view/session/sale-summary/', views.view_session_summary, name='view-session-summary'),
    path('view/session/confirm-summary/', views.confirm_view_summary, name='sale-confirm-summary'),
    path('view/sale/user/<str:username>/<int:sale_id>/summary/', views.view_sale_summary, name='sale-summary'),
    
    
    path('edit/unsold-quantity/<int:product_id>/', views.edit_unsold_quantity, name='sale-edit-unsold-quantity'),
    path('edit/cost-price/<int:product_id>/', views.edit_total_selling_price, name='sale-edit-selling-price'),
    path('edit/prepared-quantity/<int:product_id>/', views.edit_view_sale_quantity, name='sale-edit-quantity'),
    path('delete/prepared-quantity/<int:product_id>/', views.delete_view_sale_quantity, name='sale-delete-quantity'),
]