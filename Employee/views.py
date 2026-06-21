
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404
from django.views.generic import ListView, UpdateView, CreateView, DeleteView, FormView, DetailView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.contrib import messages

from django.utils import timezone
from datetime import timedelta
import random

from django.views.decorators.http import require_POST
from django.urls import reverse


from django.contrib.auth import update_session_auth_hash

from Employee.models import (Employee, Shift, ShiftEmployee, 
        OpeningCashChange, OpeningCashOverride, CashPayout, DrawerSession, Handover)


from Employee.forms import EmployeeForm, EmployeeFilterForm
from Employee.utils import get_opening_cash_for_today

from Inventory.models import Stock
from Product.models import Product
from core.models import StatusModel

from Sales.models import Sale, SaleEmployee

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.core.exceptions import ValidationError
from urllib.parse import urlencode
from django.views.decorators.http import require_POST

from django.core.paginator import Paginator

from django.db.models import Q, F, Value, CharField
from datetime import date, datetime
import calendar
from django.db.models import Sum, Avg, Max, OuterRef, Subquery

from user.models import User

from core.utils.owner import get_owner, permission_required, get_queryset_for_user, get_business_for_user, filter_to_own_if_staff

from django.contrib.messages import get_messages

from subscription.decorators import capacity_required

from activity.models import ActivityEvent
from activity.utils import log_activity, scope_events_for_user

# logging
import logging


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def employee_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    employees = get_queryset_for_user(request.user, Employee.objects.all()).filter(business=business).order_by('name')
    
    form = EmployeeFilterForm(request.GET or None)
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        
        if search:
            employees = employees.filter(name__icontains=search)
            
    avg_daily_rate = employees.average_daily_rate()
    total_daily_rate = employees.total_daily_rate()
    
    month_start = timezone.localdate().replace(day=1)
    monthly_payroll = ShiftEmployee.objects.filter(
        shift__business=business,
        shift__date__gte=month_start,
    ).aggregate(t=Sum('daily_rate'))['t'] or 0
    
        
    pagination = Paginator(employees, 5)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)

    context = {
        'page_obj': page_obj, 
        'total_daily_rate': total_daily_rate,
        'avg_daily_rate': avg_daily_rate,
        'monthly_payroll': monthly_payroll,
        'section': 'employee'
        }
    return render(request, 'Employee/employee_list.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def employee_detail(request, business_slug, employee_id, slug):
    business = get_business_for_user(request.user, business_slug)
    employee = get_object_or_404(Employee, business=business, id=employee_id, slug=slug)

    today = timezone.localdate()
    month_start = today.replace(day=1)

    shifts_qs = (
        employee.shift_employees
        .filter(shift__date__gte=month_start, shift__date__lte=today)
        .select_related('shift')
        .order_by('-shift__date')
    )
    days_worked = shifts_qs.count()
    monthly_wage = shifts_qs.aggregate(t=Sum('daily_rate'))['t'] or 0

    paginator = Paginator(shifts_qs, 5)
    page_obj = paginator.get_page(request.GET.get('page'))

    context = {
        'employee': employee,
        'page_obj': page_obj,
        'days_worked': days_worked,
        'monthly_wage': monthly_wage,
        'month_label': today.strftime('%B %Y'),
        'section': 'employee',
    }
    return render(request, 'Employee/employee_detail.html', context)


    context = {
        'employee': employee,
        'shifts': shifts,
        'days_worked': days_worked,
        'monthly_wage': monthly_wage,
        'month_label': today.strftime('%B %Y'),
        'section': 'employee',
    }
    return render(request, 'Employee/employee_detail.html', context)


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def employee_update(request, business_slug, employee_id, slug):
    business = get_business_for_user(request.user, business_slug)
    employee = get_object_or_404(Employee, business=business, id=employee_id, slug=slug)
    
    if request.method == 'POST':
        form = EmployeeForm(request.POST, instance=employee)
        
        if form.is_valid():
            obj = form.save(commit=False)
            obj.save()
            messages.success(request, f"{obj.name}'s daily rate has been updated.")
            return redirect('employee-list', business_slug=business.slug)
        else:
            print(form.errors)
    else:
        form = EmployeeForm(instance=employee)
        
    context = {'form': form, 'employee': employee, 'section': 'employee'}
    return render(request, 'Employee/employee_update.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')   # owner
@permission_required('read_only')      # dev
def employee_archive(request, business_slug, employee_id, slug):
    business = get_business_for_user(request.user, business_slug)
    employee = get_object_or_404(Employee, business=business, id=employee_id, slug=slug)

    if request.method == 'POST':
        with transaction.atomic():
            employee.status = 'inactive'
            employee.save(update_fields=['status'])
            # keep login state in sync — archiving signs them out
            if employee.staff_user_id:
                staff = employee.staff_user
                staff.is_active = False
                staff.save(update_fields=['is_active'])

        log_activity(business, request.user, 'employee.archived',
                     target=employee, description=f"{employee.name} archived")
        messages.success(request, f"{employee.name} archived and signed out. You can restore them anytime.")
        return redirect('employee-list', business_slug=business.slug)

    context = {'employee': employee, 'section': 'employee'}
    return render(request, 'Employee/employee_archive.html', context)


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only')      # dev
def archived_employees(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    employees = Employee.all_objects.filter(business=business, status='inactive').order_by('-id')
    return render(request, 'Employee/archived_employees.html', {
        'employees': employees,
        'business': business,
        'section': 'employee',
    })


@login_required(login_url='login')
@permission_required('staff_delete')   # owner
@permission_required('add')            # dev
def restore_employee(request, business_slug, employee_id, slug):
    business = get_business_for_user(request.user, business_slug)
    employee = get_object_or_404(Employee.all_objects, business=business, id=employee_id, slug=slug, status='inactive')


    if request.method == 'POST':
        with transaction.atomic():
            employee.status = 'active'
            employee.save(update_fields=['status'])
            # restoring lets them log in again
            if employee.staff_user_id:
                staff = employee.staff_user
                staff.is_active = True
                staff.save(update_fields=['is_active'])

        log_activity(business, request.user, 'employee.restored',
                     target=employee, description=f"{employee.name} restored")
        messages.success(request, f"{employee.name} restored. They can log in again.")
    return redirect('archived-employees', business_slug=business.slug)


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def shift_log_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    amount = 0
    if request.method == 'POST':
        selected_employee_ids = request.POST.getlist('selected_ids', [])
        date = request.POST.get('date')
        
        if not selected_employee_ids:
            messages.warning(request, 'Please select atleast one employee.')
        else:
            
            try:
                shift = Shift.objects.create(
                    user=business.user,
                    business=business,
                    amount=0,
                    date=date,
                    created_by=request.user,
                )
                
                from datetime import date as date_type
                emp_date = date_type.fromisoformat(date)
                
                if emp_date > date_type.today():
                    messages.error(request, 'Expense date cannot be in the future.')
                    employees = Employee.objects.filter(business=business)
                    return render(request, 'Employee/shift_log_create.html',{
                        'employees': employees,
                        'section': 'expense',
                    })
            except (ValueError, TypeError):
                messages.error(request, 'Invalid date. Please select a valid date.')
                employees = Employee.objects.filter(business=business)
                return render(request, 'Employee/shift_log_create.html', {
                    'employees': employees,
                    'section': 'expense',
                })
                
            for employee_id in selected_employee_ids:
                employee = get_object_or_404(Employee, business=business, id=employee_id)
                daily_rate = request.POST.get(f"daily_rate_{employee.id}")
                
                if not daily_rate:
                    daily_rate = employee.daily_rate
                
                amount += Decimal(daily_rate)
                
                ShiftEmployee.objects.create(
                    employee=employee,
                    shift=shift,
                    name=employee.name,
                    daily_rate=Decimal(daily_rate),
                    
                )
                
            shift.amount = amount
            shift.save()
            messages.success(request, f"Today's shift has been recorded. Please check the expense record.")
            return redirect('expense-list', business_slug=business.slug)
                
    employees = Employee.objects.filter(business=business)
    
    context = {'employees': employees, 'section': 'employees'}
    return render(request, 'Employee/shift_log_create.html', context)

@login_required(login_url='login')
def shift_dashboard(request, business_slug):
    """Single URL, role-based content."""
    business = get_business_for_user(request.user, business_slug)
    plan = getattr(business, 'plan', None)

    if not plan or not plan.has_timecards():
        messages.warning(request, 'Timecards available on Standard plan and up.')
        return redirect('product-list', business_slug=business_slug)

    today = timezone.localdate()
    current = f"{today.year}-0{today.month}"
    if request.user.role == 'owner':
        active_shifts = ShiftEmployee.objects.filter(
            shift__business=business, shift__date=today,
            clock_in__isnull=False, clock_out__isnull=True,
        ).select_related('employee').order_by('clock_in')   # full list, not paginated

        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        select_month = request.GET.get('select_month')
        
        recent_qs = ShiftEmployee.objects.filter(
            shift__business=business, clock_in__isnull=False,
        ).select_related('employee').order_by('-clock_in')
        
        if start_date:
            recent_qs = recent_qs.filter(shift__date__gte=start_date)
        if end_date:
            recent_qs = recent_qs.filter(shift__date__lte=end_date)
        if select_month:
            try:
                parsed_year, parsed_month = map(int, select_month.split('-'))
                recent_qs = recent_qs.filter(shift__date__month=parsed_month, shift__date__year=parsed_year)
            except ValueError:
                pass
        recent_shifts = Paginator(recent_qs, 7).get_page(request.GET.get('page'))

        all_employees = Employee.objects.filter(business=business)
        
        mismatch_count = Handover.objects.filter(
            drawer_session__business=business, reviewed=False
        ).exclude(claimed_amount=F('counted_amount')).count()


        return render(request, 'Employee/shift_dashboard_owner.html', {
            'business': business,
            'active_shifts': active_shifts,
            'recent_shifts': recent_shifts,
            'all_employees': all_employees,
            'enable_cash_reconciliation': business.enable_cash_reconciliation and plan.has_cash_reconciliation(),
            'current': current,
            'mismatch_count': mismatch_count,
            'section': 'shift',
        })

    # Staff view
    employee = Employee.objects.filter(staff_user=request.user, business=business).first()
    if not employee:
        messages.error(request, 'You are not registered as an employee of this business.')
        return redirect('product-list', business_slug=business_slug)

    active_shift = ShiftEmployee.objects.filter(
        employee=employee, shift__business=business,
        clock_in__isnull=False, clock_out__isnull=True,
    ).first()

    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    select_month = request.GET.get('select_month')
    
    current = f"{today.year}-0{today.month}"
    recent_qs = ShiftEmployee.objects.filter(
        employee=employee, clock_in__isnull=False,
    ).order_by('-clock_in')
    
    if start_date:
        recent_qs = recent_qs.filter(shift__date__gte=start_date)
    if end_date:
        recent_qs = recent_qs.filter(shift__date__lte=end_date)
    if select_month:
        try:
            parsed_year, parsed_month = map(int, select_month.split('-'))
            recent_qs = recent_qs.filter(shift__date__month=parsed_month, shift__date__year=parsed_year)
        except ValueError:
            pass

    recent_shifts = Paginator(recent_qs, 5).get_page(request.GET.get('page'))

    opening_info = get_opening_cash_for_today(business)
    
    # Shared-drawer take-over? An open session whose holder already clocked out.
    pending_handover = None
    drawer_busy = None
    if employee.is_cashier and business.shared_cash_drawer and active_shift is None:
        open_session = DrawerSession.objects.filter(
            business=business, date=today, status='open'
        ).first()
        if open_session and open_session.current_holder_id:
            if open_session.current_holder.clock_out is not None:
                pending_handover = open_session
            else:
                drawer_busy = open_session.current_holder



    return render(request, 'Employee/shift_dashboard_staff.html', {
        'business': business,
        'employee': employee,
        'active_shift': active_shift,
        'recent_shifts': recent_shifts,
        'opening_info': opening_info,
        'current': current,
        'pending_handover': pending_handover,
        'drawer_busy': drawer_busy,
        'section': 'shift',
    })

@login_required(login_url='login')
def clock_in(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    plan = getattr(business, 'plan', None)

    if not plan or not plan.has_timecards():
        messages.warning(request, 'Timecards available on Standard plan and up.')
        return redirect('product-list', business_slug=business_slug)

    if request.user.role == 'owner':
        messages.warning(request, 'You are already timed in.')
        return redirect('shift-dashboard', business_slug=business_slug)

    employee = Employee.objects.filter(staff_user=request.user, business=business).first()
    if not employee:
        messages.error(request, 'You are not registered as an employee of this business.')
        return redirect('product-list', business_slug=business_slug)

    # Block double clock-in
    active = ShiftEmployee.objects.filter(
        employee=employee, shift__business=business,
        clock_in__isnull=False, clock_out__isnull=True,
    ).first()
    if active:
        messages.warning(request, 'You are already timed in.')
        return redirect('shift-detail', business_slug=business_slug, shift_id=active.id)

    if request.method != 'POST':
        return redirect('shift-dashboard', business_slug=business_slug)

    # Cashier shifts confirm or recount the drawer; attendance-only shifts skip all cash.
    is_cashier = employee.is_cashier
    today = timezone.localdate()

    # Shared-drawer HANDOVER? An open session whose holder already clocked out = take-over.
    handover_from = None
    recount_cash = None
    if is_cashier and business.shared_cash_drawer:
        open_session = DrawerSession.objects.filter(
            business=business, date=today, status='open'
        ).first()
        if open_session and open_session.current_holder_id:
            holder = open_session.current_holder
            if holder.clock_out is None:
                # Drawer is actively held by another cashier — they must hand over first.
                messages.error(request, f"{holder.name} is using the cash drawer. They need to hand it over before you can take over.")
                return redirect('shift-dashboard', business_slug=business_slug)
            handover_from = holder


    # A take-over needs a recount; a fresh open needs the float confirmation.
    if is_cashier:
        if handover_from is not None:
            try:
                recount_cash = Decimal(request.POST.get('recount_cash') or '')
            except (InvalidOperation, ValueError):
                messages.error(request, 'Please enter the cash you counted to take over the drawer.')
                return redirect('shift-dashboard', business_slug=business_slug)
        elif request.POST.get('confirm_opening_cash') != 'on':
            messages.error(request, 'Please confirm the starting cash amount before timing in.')
            return redirect('shift-dashboard', business_slug=business_slug)

    # Find/create parent Shift for today
    shift, _ = Shift.objects.get_or_create(
        business=business, date=today, user=business.user,
        defaults={'amount': Decimal('0')},
    )

    # Snapshot opening cash only for cashiers (attendance-only shifts start at 0)
    opening_cash = Decimal('0')
    opening_bills = Decimal('0')
    opening_coins = Decimal('0')
    drawer_session = None
    if is_cashier:
        if handover_from is not None:
            # Taking over: opening = the incoming cashier's physical recount (cash only).
            drawer_session = handover_from.drawer_session
            opening_cash = recount_cash
            if business.track_coins_separately:
                opening_bills = recount_cash
                opening_coins = Decimal('0')
        else:
            opening_info = get_opening_cash_for_today(business)
            opening_cash = opening_info['amount']
            if business.track_coins_separately:
                opening_bills = business.default_opening_bills
                opening_coins = business.default_opening_coins
                opening_cash = opening_bills + opening_coins
            # Shared drawer: first cashier of the day opens the session at the float.
            if business.shared_cash_drawer:
                drawer_session = DrawerSession.objects.filter(
                    business=business, date=today, status='open'
                ).first()
                if drawer_session is None:
                    drawer_session = DrawerSession.objects.create(
                        business=business, date=today, opening_cash=opening_cash,
                    )

    shift_emp = ShiftEmployee.objects.create(
        shift=shift,
        employee=employee,
        name=employee.name,
        daily_rate=employee.daily_rate or Decimal('0'),
        clock_in=timezone.now(),
        is_cashier=is_cashier,
        opening_cash=opening_cash,
        opening_bills=opening_bills,
        opening_coins=opening_coins,
        staff_confirmed_opening=is_cashier,
        staff_confirmed_opening_at=timezone.now() if is_cashier else None,
        drawer_session=drawer_session,
    )

    # Record the handover + flag any mismatch.
    if handover_from is not None and drawer_session is not None:
        handover = Handover.objects.create(
            drawer_session=drawer_session,
            from_shift=handover_from,
            to_shift=shift_emp,
            claimed_amount=handover_from.counted_cash or Decimal('0'),
            counted_amount=recount_cash,
        )
        if handover.has_mismatch:
            sign = '+' if handover.variance > 0 else ''
            messages.warning(
                request,
                f"Your recount is {sign}₱{handover.variance:.2f} vs what {handover_from.name} handed over — flagged for the owner."
            )

    if drawer_session is not None:
        drawer_session.current_holder = shift_emp
        drawer_session.save(update_fields=['current_holder'])

    messages.success(
        request,
        f'Timed in at {timezone.localtime(shift_emp.clock_in).strftime("%I:%M %p")}.'
    )
    return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

@login_required(login_url='login')
def clock_out(request, business_slug, shift_id):
    business = get_business_for_user(request.user, business_slug)
    plan = getattr(business, 'plan', None)
    shift_emp = get_object_or_404(
        ShiftEmployee, id=shift_id, shift__business=business
    )

    if not shift_emp.is_active:
        messages.error(request, 'This shift is already closed — cash taken out can only be recorded during an active shift.')
        return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

    # Staff: only their own shift
    if request.user.role == 'staff':
        if shift_emp.employee.staff_user_id != request.user.id:
            messages.error(request, 'Not authorized.')
            return redirect('shift-dashboard', business_slug=business_slug)
        
    # Owner closing a staff's shift on their behalf (staff forgot to time out)
    owner_closing = (
        request.user.role == 'owner'
        and shift_emp.employee
        and shift_emp.employee.staff_user_id != request.user.id
    )

    needs_reconciliation = (
        shift_emp.is_cashier
        and plan and plan.has_cash_reconciliation()
        and business.enable_cash_reconciliation
    )

    if request.method == 'POST':
        # Owner closing on behalf must give a reason
        close_reason = request.POST.get('close_reason', '').strip()
        if owner_closing and not close_reason:
            messages.error(request, 'Please give a reason for timing out this staff member on their behalf.')
            return redirect('shift-clock-out', business_slug=business_slug, shift_id=shift_emp.id)

        # Default: time out now. When the owner closes on behalf, they can set the
        # ACTUAL time the staff left — avoids over-counting hours if closed late.
        clock_out_time = timezone.now()
        if owner_closing:
            actual_date = request.POST.get('actual_date', '').strip()
            actual_time = request.POST.get('actual_time', '').strip()
            if actual_date and actual_time:
                from django.utils.dateparse import parse_datetime
                parsed = parse_datetime(f'{actual_date}T{actual_time}')
                if parsed is None:
                    messages.error(request, 'Invalid actual time-out — please re-pick the date and time.')
                    return redirect('shift-clock-out', business_slug=business_slug, shift_id=shift_emp.id)
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed)
                if parsed <= shift_emp.clock_in:
                    messages.error(request, 'Actual time-out must be after the time-in.')
                    return redirect('shift-clock-out', business_slug=business_slug, shift_id=shift_emp.id)
                if parsed > timezone.now():
                    messages.error(request, "Actual time-out can't be in the future.")
                    return redirect('shift-clock-out', business_slug=business_slug, shift_id=shift_emp.id)
                clock_out_time = parsed

        shift_emp.clock_out = clock_out_time


        if owner_closing:
            shift_emp.closed_by = request.user
            shift_emp.close_reason = close_reason
            shift_emp.close_acknowledged = False
            shift_emp.edited_by = request.user
            shift_emp.edited_at = timezone.now()

        # When the owner closes on behalf, the count is OPTIONAL — only record it if
        # they ticked "I counted the drawer". Otherwise leave counts unset (None) so we
        # don't fabricate a ₱0 drawer = a false shortage against the absent staff.
        owner_counted = request.POST.get('owner_counted') == 'on'
        record_count = needs_reconciliation and (not owner_closing or owner_counted)

        if record_count:
            try:
                if business.track_coins_separately:
                    shift_emp.counted_bills = Decimal(request.POST.get('counted_bills') or '0')
                    shift_emp.counted_coins = Decimal(request.POST.get('counted_coins') or '0')
                else:
                    shift_emp.counted_cash = Decimal(request.POST.get('counted_cash') or '0')
                shift_emp.counted_gcash = Decimal(request.POST.get('counted_gcash') or '0')
                shift_emp.counted_bank  = Decimal(request.POST.get('counted_bank')  or '0')
            except (InvalidOperation, ValueError):
                messages.error(request, 'Invalid amount in one of the count fields.')
                return redirect('shift-clock-out', business_slug=business_slug, shift_id=shift_emp.id)

            shift_emp.closing_note = request.POST.get('closing_note', '').strip()

        shift_emp.save()  # auto-sums bills+coins into counted_cash
        
        # Shared drawer: close it for the day, or keep it open to hand over.
        if shift_emp.drawer_session_id and shift_emp.drawer_session.is_open:
            if request.POST.get('drawer_action') != 'handover':
                session = shift_emp.drawer_session
                session.status = 'closed'
                session.closed_at = timezone.now()
                session.save(update_fields=['status', 'closed_at'])


        if owner_closing and needs_reconciliation and not record_count:
            messages.success(request, "Shift closed on the staff's behalf. No cash count was recorded.")
        elif needs_reconciliation and shift_emp.total_variance:
            sign = '+' if shift_emp.total_variance > 0 else ''
            messages.warning(
                request,
                f'Shift closed. Total difference: {sign}₱{shift_emp.total_variance:.2f}.'
            )
        else:
            messages.success(request, 'Shift closed successfully.')

        return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)


    return render(request, 'Employee/clock_out.html', {
        'business': business,
        'shift_emp': shift_emp,
        'needs_reconciliation': needs_reconciliation,
        'owner_closing': owner_closing,
        'section': 'shift',
    })
    
@login_required(login_url='login')
def shift_detail(request, business_slug, shift_id):
    business = get_business_for_user(request.user, business_slug)
    shift_emp = get_object_or_404(
        ShiftEmployee, id=shift_id, shift__business=business
    )

    # Staff: own shifts only
    if request.user.role == 'staff':
        if shift_emp.employee.staff_user_id != request.user.id:
            messages.error(request, 'Not authorized.')
            return redirect('shift-dashboard', business_slug=business_slug)

    plan = getattr(business, 'plan', None)
    show_reconciliation = (
        shift_emp.is_cashier
        and plan and plan.has_cash_reconciliation()
        and business.enable_cash_reconciliation
    )

    opening_changes = shift_emp.opening_cash_changes.all()

    return render(request, 'Employee/shift_detail.html', {
        'business': business,
        'shift_emp': shift_emp,
        'show_reconciliation': show_reconciliation,
        'opening_changes': opening_changes,
        'section': 'shift',
    })
    
@login_required(login_url='login')
def shift_edit_opening_cash(request, business_slug, shift_id):
    business = get_business_for_user(request.user, business_slug)

    if request.user.role != 'owner':
        messages.error(request, 'Only owners can update starting cash.')
        return redirect('shift-detail', business_slug=business_slug, shift_id=shift_id)

    shift_emp = get_object_or_404(ShiftEmployee, id=shift_id, shift__business=business)

    if not shift_emp.is_active:
        messages.error(request, 'This shift is already closed — starting cash can no longer be updated.')
        return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

    if request.method == 'POST':
        new_amount_str = request.POST.get('new_amount', '0').strip()
        note = request.POST.get('note', '').strip()

        if not note:
            messages.error(request, 'A reason is required when changing starting cash.')
            return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

        try:
            new_amount = Decimal(new_amount_str)
        except (InvalidOperation, ValueError):
            messages.error(request, 'Invalid amount.')
            return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

        if new_amount == shift_emp.opening_cash:
            messages.info(request, 'No change to starting cash.')
            return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

        # Append change to audit log
        OpeningCashChange.objects.create(
            shift=shift_emp,
            old_amount=shift_emp.opening_cash,
            new_amount=new_amount,
            note=note,
            changed_by=request.user,
        )

        # Apply change immediately (variance recomputes)
        shift_emp.opening_cash = new_amount
        if business.track_coins_separately:
            # For simplicity, dump full new amount into bills; owner can refine later
            shift_emp.opening_bills = new_amount
            shift_emp.opening_coins = Decimal('0')
        shift_emp.edited_by = request.user
        shift_emp.edited_at = timezone.now()
        shift_emp.save()

        messages.success(
            request,
            f'Starting cash updated to ₱{new_amount}. Staff will be asked to acknowledge.'
        )
        return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

    return render(request, 'Employee/shift_edit_opening.html', {
        'business': business,
        'shift_emp': shift_emp,
        'section': 'shift',
    })
    
@login_required(login_url='login')
def acknowledge_opening_cash(request, business_slug, change_id):
    business = get_business_for_user(request.user, business_slug)
    change = get_object_or_404(
        OpeningCashChange, id=change_id, shift__shift__business=business
    )

    # Only the staff whose shift this is can acknowledge
    if change.shift.employee.staff_user_id != request.user.id:
        messages.error(request, 'Not authorized.')
        return redirect('shift-dashboard', business_slug=business_slug)

    if change.is_expired:
        messages.warning(request, 'This change has expired and can no longer be acknowledged.')
        return redirect('shift-detail', business_slug=business_slug, shift_id=change.shift.id)

    if request.method == 'POST':
        change.acknowledged = True
        change.acknowledged_at = timezone.now()
        change.save(update_fields=['acknowledged', 'acknowledged_at'])
        messages.success(request, 'Change acknowledged.')

    return redirect('shift-detail', business_slug=business_slug, shift_id=change.shift.id)

@login_required(login_url='login')
def record_cash_payout(request, business_slug, shift_id):
    business = get_business_for_user(request.user, business_slug)

    if request.user.role != 'owner':
        messages.error(request, 'Only owners can record cash taken out.')
        return redirect('shift-detail', business_slug=business_slug, shift_id=shift_id)

    shift_emp = get_object_or_404(ShiftEmployee, id=shift_id, shift__business=business)

    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', '0'))
        except (InvalidOperation, ValueError):
            messages.error(request, 'Invalid amount.')
            return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

        if amount <= 0:
            messages.error(request, 'Amount must be greater than 0.')
            return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

        purpose = request.POST.get('purpose', 'owner_drawing')
        note    = request.POST.get('note', '').strip()

        payout = CashPayout.objects.create(
            shift=shift_emp, amount=amount, purpose=purpose,
            note=note, created_by=request.user,
        )

        if payout.is_business_expense:
            _book_payout_as_expense(business, request.user, payout)
            messages.success(request, f'₱{amount} recorded and added to Expenses. The cash count is reduced automatically.')
        else:
            messages.success(request, f'Cash taken out (₱{amount}) recorded. Staff will be asked to acknowledge.')

    return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

@login_required(login_url='login')
def _book_payout_as_expense(business, user, payout):
    """Auto-create an Expense for a business-purpose drawer withdrawal (Option 2: 'Cash Drawer' category)."""
    from Expense.models import Expense, ExpenseItem, MiscExpense
    from core.models import Category

    category, _ = Category.objects.get_or_create(
        business=business, category_type='expense', name='Cash Drawer',
        defaults={'user': business.user, 'created_by': user},
    )
    label = payout.note or 'Cash drawer withdrawal'
    misc = MiscExpense.objects.create(
        user=business.user, created_by=user, business=business,
        name=label, amount=payout.amount, category=category,
    )
    expense = Expense.objects.create(
        user=business.user, created_by=user, business=business,
        total_amount=payout.amount, date=timezone.localdate(),
    )
    ExpenseItem.objects.create(
        expense=expense, misc_expense=misc,
        amount=payout.amount, name=label, category=category.name,
    )

@login_required(login_url='login')
def acknowledge_cash_payout(request, business_slug, payout_id):
    business = get_business_for_user(request.user, business_slug)
    payout = get_object_or_404(
        CashPayout, id=payout_id, shift__shift__business=business
    )

    if payout.shift.employee.staff_user_id != request.user.id:
        messages.error(request, 'Not authorized.')
        return redirect('shift-dashboard', business_slug=business_slug)

    if payout.is_expired:
        messages.warning(request, 'This entry has expired and can no longer be acknowledged.')
        return redirect('shift-detail', business_slug=business_slug, shift_id=payout.shift.id)

    if request.method == 'POST':
        payout.acknowledged = True
        payout.acknowledged_at = timezone.now()
        payout.save(update_fields=['acknowledged', 'acknowledged_at'])
        messages.success(request, 'Cash taken out acknowledged.')

    return redirect('shift-detail', business_slug=business_slug, shift_id=payout.shift.id)

@login_required(login_url='login')
def acknowledge_shift_close(request, business_slug, shift_id):
    business = get_business_for_user(request.user, business_slug)
    shift_emp = get_object_or_404(
        ShiftEmployee, id=shift_id, shift__business=business
    )

    # Only the staff whose shift this is can acknowledge
    if shift_emp.employee.staff_user_id != request.user.id:
        messages.error(request, 'Not authorized.')
        return redirect('shift-dashboard', business_slug=business_slug)

    # Nothing to acknowledge unless the owner actually closed this shift
    if not shift_emp.closed_by_id:
        return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

    if request.method == 'POST':
        if not shift_emp.close_acknowledged:
            if request.POST.get('response') == 'disagree':
                reason = request.POST.get('close_dispute_reason', '').strip()
                if not reason:
                    messages.error(request, 'Please pick a reason for flagging this for review.')
                    return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)
                shift_emp.close_dispute_reason = reason
                shift_emp.close_dispute_note = request.POST.get('close_dispute_note', '').strip()
                shift_emp.close_acknowledged = True
                shift_emp.close_acknowledged_at = timezone.now()
                shift_emp.save(update_fields=[
                    'close_acknowledged', 'close_acknowledged_at',
                    'close_dispute_reason', 'close_dispute_note',
                ])
                messages.success(request, 'Flagged for review — the owner will see your reason.')
            else:
                shift_emp.close_acknowledged = True
                shift_emp.close_acknowledged_at = timezone.now()
                shift_emp.save(update_fields=['close_acknowledged', 'close_acknowledged_at'])
                messages.success(request, 'Thanks — you confirmed the owner closed your shift.')

    return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

@login_required(login_url='login')
def resolve_shift_dispute(request, business_slug, shift_id):
    business = get_business_for_user(request.user, business_slug)

    if request.user.role != 'owner':
        messages.error(request, 'Only owners can resolve a flagged shift.')
        return redirect('shift-detail', business_slug=business_slug, shift_id=shift_id)

    shift_emp = get_object_or_404(ShiftEmployee, id=shift_id, shift__business=business)

    if request.method == 'POST':
        if shift_emp.close_dispute_unresolved:
            shift_emp.close_dispute_resolved = True
            shift_emp.save(update_fields=['close_dispute_resolved'])
            messages.success(request, 'Marked as resolved.')

    return redirect('shift-detail', business_slug=business_slug, shift_id=shift_emp.id)

@login_required(login_url='login')
def review_handover(request, business_slug, handover_id):
    business = get_business_for_user(request.user, business_slug)
    if request.user.role != 'owner':
        messages.error(request, 'Only owners can review a handover.')
        return redirect('shift-dashboard', business_slug=business_slug)

    handover = get_object_or_404(Handover, id=handover_id, drawer_session__business=business)

    if request.method == 'POST' and not handover.reviewed:
        handover.reviewed = True
        handover.reviewed_at = timezone.now()
        handover.save(update_fields=['reviewed', 'reviewed_at'])
        messages.success(request, 'Handover marked as reviewed.')

    shift_id = handover.to_shift_id or handover.from_shift_id
    return redirect('shift-detail', business_slug=business_slug, shift_id=shift_id)

