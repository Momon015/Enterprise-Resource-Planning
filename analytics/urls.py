from django.urls import path

from . import views

urlpatterns = [
    path('sales/',   views.sales_analytics,   name='sales-analytics'),
    path('expense/', views.expense_analytics, name='expense-analytics'),
    # Profit Analytics lands here next (IA phase 3) — and when it does, the parked
    # `MOVED TO ANALYTICS` blocks and the Dashboard's weekly/monthly comparison get
    # cleaned up in that same pass.
]
