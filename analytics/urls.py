from django.urls import path

from . import views

urlpatterns = [
    path('sales/',   views.sales_analytics,   name='sales-analytics'),
    path('sales/body/', views.sales_analytics_body, name='sales-analytics-body'),
    path('sales/top/', views.top_sellers_detail, name='top-sellers-detail'),
    path('expense/', views.expense_analytics, name='expense-analytics'),
    path('expense/supplier/<int:supplier_id>/', views.supplier_spend_detail, name='supplier-spend-detail'),
    path('profit/',  views.profit_analytics,  name='profit-analytics'),
    path('profit/body/', views.profit_analytics_body, name='profit-analytics-body'),
    # Still parked: the `MOVED TO ANALYTICS` blocks at the foot of dashboard.html and the
    # Dashboard's weekly/monthly comparison — both belong on the trend pages now.
]
