from . import views
from django.urls import path, include

# Create your urls here.


urlpatterns = [
    # materials urls

    path('material-list/', views.material_list, name='material-list'),
    path('material/create/', views.material_create, name='material-create'),
    path('material/<str:id>/<slug:slug>/view/', views.material_detail, name='material-detail'),
    path('material/<str:id>/<slug:slug>/update/', views.material_update, name='material-update'),
    path('material/<str:id>/<slug:slug>/archive/', views.material_archive, name='material-archive'),
    
    # archive
    path('materials/archived/', views.archived_materials, name='archived-materials'),
    path('materials/archived/<int:material_id>/restore/', views.restore_material, name='restore-material'),
    
    # save items
    path('view-cart/save/preset/', views.save_items, name='material-save-items'),
    path('view/preset-list/', views.preset_list, name='material-preset-list'),
    path('<int:id>/<str:slug>/view/preset-detail/', views.preset_detail, name='material-preset-detail'),
    path('<int:id>/<str:slug>/view/preset-update/', views.edit_preset, name='material-edit-preset'),
    path('<int:id>/<str:slug>/view/preset-delete/', views.delete_preset, name='material-delete-preset'),
    
    path('presets/<int:id>/items/<int:item_id>/remove/', views.remove_preset_item, name='material-preset-remove-item'),

    # adding preset to cart
    path('view/<int:preset_id>/apply-preset/', views.adding_preset_to_cart, name='material-add-preset-to-cart'),
    
    # supplier 
    path('view/supplier-list/', views.supplier_list, name='supplier-list'),
    path('view/supplier-create/', views.supplier_create, name='supplier-create'),
    # path('supplier/view/<int:supplier_id>/', views.supplier_detail, name='detail')
    path('supplier/<int:supplier_id>/<str:slug>/view/supplier-update/', views.supplier_update, name='supplier-update'),
    path('supplier/<int:supplier_id>/<str:slug>/view/supplier-archive/', views.supplier_archive, name='supplier-archive'),

    # archive
    path('archived/', views.archived_suppliers, name='archived-suppliers'),
    path('archived/<int:supplier_id>/restore/', views.restore_supplier, name='restore-supplier'),

]