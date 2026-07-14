from django.urls import path

from . import views

urlpatterns = [
    path('sales/',   views.sales_analytics,   name='sales-analytics'),
    path('expense/', views.expense_analytics, name='expense-analytics'),
    path('profit/',  views.profit_analytics,  name='profit-analytics'),
    # Still parked: the `MOVED TO ANALYTICS` blocks at the foot of dashboard.html and the
    # Dashboard's weekly/monthly comparison — both belong on the trend pages now.
]
