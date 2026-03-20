from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    # materials urls
    path('materials-list/', views.material_list, name='material-list'),
    path('materials/create/', views.material_create, name='material-create'),
    path('materials/view/<slug:slug>/', views.material_detail, name='material-detail'),
    path('materials/update/<slug:slug>/', views.material_update, name='material-update'),
    path('materials/delete/<slug:slug>/', views.material_delete, name='material-delete'),
    
    # save items
    path('view-cart/save/preset/', views.save_items, name='material-save-items'),
    path('view/presets/', views.preset_list, name='material-preset-list'),
    path('view/<int:preset_id>/preset/', views.preset_detail, name='material-preset-detail'),
    path('edit/<int:preset_id>/preset/', views.edit_preset, name='material-edit-preset'),
    path('view/preset/<int:preset_id>/delete/', views.delete_preset, name='material-delete-preset'),
    
    # adding preset to cart
    path('view/<int:preset_id>/apply-preset/', views.adding_preset_to_cart, name='material-add-preset-to-cart'),
    
    # supplier 
    path('supplier-list/', views.supplier_list, name='supplier-list'),
    path('supplier/create/', views.supplier_create, name='supplier-create'),
    # path('supplier/view/<int:supplier_id>/', views.supplier_detail, name='supplier-detail')
    path('supplier/update/<int:supplier_id>/', views.supplier_update, name='supplier-update'),
    path('supplier/delete/<int:supplier_id>/', views.supplier_delete, name='supplier-delete'),

]