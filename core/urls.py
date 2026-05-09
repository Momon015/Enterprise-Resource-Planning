from . import views
from django.urls import path, include


# Create your urls here.

urlpatterns = [
    path('<str:business_slug>/category/', include([
        path('list/', views.category_list, name='category-list'),
        path('create/', views.category_create, name='category-create'),
        path('<int:category_id>/<slug:slug>/update/', views.category_update, name='category-update'),
        path('<int:category_id>/<slug:slug>/delete/', views.category_delete, name='category-delete'),
    ]))
]
