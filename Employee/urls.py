from . import views
from django.urls import path

# Create your urls here.
    
urlpatterns = [
    # employee
    path('view/employees-list/', views.employee_list, name='employee-list'),
    path('view/archived-employees/', views.archived_employees, name='archived-employees'),
    path('view/<str:employee_id>/<str:slug>/employee-detail/', views.employee_detail, name='employee-detail'),
    path('update/<str:employee_id>/<str:slug>/employee-update/', views.employee_update, name='employee-update'),
    path('archive/<str:employee_id>/<str:slug>/employee-archive/', views.employee_archive, name='employee-archive'),
    path('restore/<str:employee_id>/<str:slug>/employee-restore/', views.restore_employee, name='restore-employee'),
    
    path('staff/<int:user_id>/approve/', views.approve_staff, name='approve-staff'),
    path('staff/<int:user_id>/decline/', views.decline_staff, name='decline-staff'),

    
    # shift logging (manual payroll path) — referenced by employee_list.html
    path('shifts/add-shift-employee/', views.shift_log_create, name='add-shift-employee'),

    path('hr/shifts/',                                  views.shift_dashboard,            name='shift-dashboard'),
    path('hr/shifts/clock-in/',                         views.clock_in,                   name='shift-clock-in'),
    path('hr/shifts/<int:shift_id>/clock-out/',         views.clock_out,                  name='shift-clock-out'),
    path('hr/shifts/<int:shift_id>/',                   views.shift_detail,               name='shift-detail'),
    path('hr/shifts/<int:shift_id>/edit-opening/',      views.shift_edit_opening_cash,    name='shift-edit-opening-cash'),
    path('hr/shifts/acknowledge/<int:change_id>/',      views.acknowledge_opening_cash,   name='acknowledge-opening-cash'),
    path('hr/shifts/<int:shift_id>/cash-payout/',       views.record_cash_payout,         name='record-cash-payout'),
    path('hr/shifts/payout/<int:payout_id>/acknowledge/', views.acknowledge_cash_payout,  name='acknowledge-cash-payout'),
    path('hr/shifts/<int:shift_id>/acknowledge-close/', views.acknowledge_shift_close,    name='acknowledge-shift-close'),
    path('hr/shifts/<int:shift_id>/resolve-dispute/', views.resolve_shift_dispute,        name='resolve-shift-dispute'),
    path('hr/handover/<int:handover_id>/review/', views.review_handover,                  name='review-handover'),

]

