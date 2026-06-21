from . import views
from django.urls import path, include


# Create your urls here.

urlpatterns = [
    path('all/', views.view_all_activity, name='view-all-activity'),
    path('read/all/', views.mark_all_read, name='mark-all-read'),
    path('read/<int:event_id>/', views.mark_one_read, name='mark-one-read'),
    path('event/<int:event_id>/open/', views.click_event, name='activity-click'),

]
