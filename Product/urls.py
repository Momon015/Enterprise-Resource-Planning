from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    path('list/', views.product_list, name='product-list'),
    path('create/', views.product_create, name='product-create'),
    
    path('detail/user/<str:username>/<slug:product_slug>/', views.product_detail, name='product-detail'),
    path('update/user/<str:username>/<slug:product_slug>/', views.product_update, name='product-update'),
    path('delete/user/<str:username>/<slug:product_slug>/', views.product_delete, name='product-delete'),
    
    path('add/product/preset/', views.add_product_to_preset, name='product-add-to-preset'),
    path('view/product/preset-list/', views.list_product_preset, name='product-preset-list'),
    path('view/product/preset/user/<str:username>/<int:preset_id>/detail/', views.detail_product_preset, name='product-preset-detail'),
    path('view/product/preset/user/<str:username>/<int:preset_id>/update/', views.edit_product_preset, name='product-edit-preset'),
    path('view/product/preset/user/<str:username>/<int:preset_id>/delete/', views.delete_product_preset, name='product-delete-preset'),
    path('add/product/user/<str:username>/<int:preset_id>/preset-to-sale/', views.product_add_preset_to_sale, name='product-preset-add-to-sale'),
    
    
    # restoring solo and batch quantities 
    path('restore/product/<int:product_id>/quantity/', views.restore_product_quantity, name='product-restore-quantity'),
    path('restore/batch-quantity/', views.restore_batch_product, name='product-batch-restore-quantity'),
    
]


