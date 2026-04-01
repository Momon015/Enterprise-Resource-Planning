from . import views
from django.urls import path

# Create your urls here.

urlpatterns = [
    # materials urls
    path('materials-list/', views.material_list, name='material-list'),
    path('materials/create/', views.material_create, name='material-create'),
    path('materials/user/<str:username>/view/<slug:slug>/', views.material_detail, name='material-detail'),
    path('materials/user/<str:username>/update/<slug:slug>/', views.material_update, name='material-update'),
    path('materials/user/<str:username>/delete/<slug:slug>/', views.material_delete, name='material-delete'),
    
    # save items
    path('view-cart/save/preset/', views.save_items, name='material-save-items'),
    path('view/presets/', views.preset_list, name='material-preset-list'),
    path('view/detail/user/<str:username>/<int:preset_id>/preset/', views.preset_detail, name='material-preset-detail'),
    path('edit/update/user/<str:username>/<int:preset_id>/preset/', views.edit_preset, name='material-edit-preset'),
    path('view/delete/user/<str:username>/<int:preset_id>/preset/', views.delete_preset, name='material-delete-preset'),
    
    # adding preset to cart
    path('view/user<str:username>/<int:preset_id>/apply-preset/', views.adding_preset_to_cart, name='material-add-preset-to-cart'),
    
    # supplier 
    path('list/', views.supplier_list, name='supplier-list'),
    path('create/', views.supplier_create, name='supplier-create'),
    # path('supplier/view/<int:supplier_id>/', views.supplier_detail, name='supplier-detail')
    path('update/user/<str:username>/<int:supplier_id>/', views.supplier_update, name='supplier-update'),
    path('delete/user/<str:username>/<int:supplier_id>/', views.supplier_delete, name='supplier-delete'),

]