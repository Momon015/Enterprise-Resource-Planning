from . import views
from django.urls import path, include


# Create your urls here.

urlpatterns = [
    path('business/<str:business_slug>/search/', views.global_search, name='global-search'),
        
    path('<str:business_slug>/category/', include([
        path('list/', views.category_list, name='category-list'),
        path('create/', views.category_create, name='category-create'),
        path('<int:category_id>/<slug:slug>/update/', views.category_update, name='category-update'),
        path('<int:category_id>/<slug:slug>/delete/', views.category_delete, name='category-delete'),
    ])),

]
