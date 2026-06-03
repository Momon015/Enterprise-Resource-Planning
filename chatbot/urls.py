from . import views
from django.urls import path, include

# Create your urls here.

urlpatterns = [
    path('chatbot/', views.ask_chatbot, name='ask-chatbot'),
]
