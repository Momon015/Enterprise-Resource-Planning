from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    path('view/list/', views.product_list, name='product-list'),
    path('create/', views.product_create, name='product-create'),
    
    path('view/<str:product_id>/<slug:product_slug>/detail/', views.product_detail, name='product-detail'),
    path('view/<str:product_id>/<slug:product_slug>/update/', views.product_update, name='product-update'),
    path('view/<str:product_id>/<slug:product_slug>/delete/', views.product_delete, name='product-delete'),
    
    path('add/product/preset/', views.add_product_to_preset, name='product-add-to-preset'),
    path('view/preset-list/', views.list_product_preset, name='product-preset-list'),
    path('view/preset/<int:preset_id>/<str:preset_slug>/detail/', views.detail_product_preset, name='product-preset-detail'),
    path('view/preset/<int:preset_id>/<str:preset_slug>/update/', views.edit_product_preset, name='product-edit-preset'),
    path('view/preset/<int:preset_id>/<str:preset_slug>/delete/', views.delete_product_preset, name='product-delete-preset'),
    path('add/<int:preset_id>/<str:preset_slug>/preset-to-sale/', views.product_add_preset_to_sale, name='product-preset-add-to-sale'),
    
    
    # restoring solo and batch quantities 
    path('restore/<int:product_id>/quantity/', views.restore_product_quantity, name='product-restore-quantity'),
    path('restore/batch-quantity/', views.restore_batch_product, name='product-batch-restore-quantity'),
    
]


