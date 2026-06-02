"""
URL configuration for SalesAndInventorySystem project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

from user import views as user_views

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # landing page
    path('', user_views.landing, name='landing'),
    path('admin/', admin.site.urls),
    path('user/', include('user.urls')),
    path('', include('core.urls')),
    
    path('business/<str:business_slug>/', include([
        path('products/', include('Product.urls')),
        path('expenses/', include('Expense.urls')),
        path('inventory/', include('Inventory.urls')),
        path('sales/', include('Sales.urls')),
        path('summary/', include('DailySummary.urls')),
        path('dashboard/', include('Dashboard.urls')),
        path('supplier/', include('Supplier.urls')),
        path('subscription/', include('subscription.urls')),
        path('activity/', include('activity.urls')),
    ])),
    
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)