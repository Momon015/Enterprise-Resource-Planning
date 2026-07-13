from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, Http404, HttpResponseForbidden
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

from django.contrib.auth.forms import PasswordChangeForm, PasswordResetForm
from django.contrib.auth import update_session_auth_hash

from Sales.models import (
    Sale, SaleItem, SaleEmployee, SalesPayment, SalesReturn, SalesReturnItem)
from Sales.forms import SaleForm, SaleFilterForm, SalesReturnFilterForm

from Product.models import Product
from Product.forms import ProductForm

from Expense.models import Purchase, PurchaseItem, Waste, WasteItem, Expense, MiscExpense
from Employee.models import Employee, Shift, ShiftEmployee
from Employee.forms import EmployeeForm
from Employee.utils import void_window_open

from Inventory.models import Stock
from Expense.models import Waste, WasteItem
from core.models import StatusModel

from decimal import Decimal

from django.db import transaction
from django.core.exceptions import ValidationError
from urllib.parse import urlencode
from django.views.decorators.http import require_POST

from django.core.paginator import Paginator

from datetime import date, datetime
import calendar
from django.db.models import Sum, Avg, Max, Q, F
from django.db.models.functions import Coalesce

from decimal import Decimal, InvalidOperation

from core.utils.owner import get_owner, permission_required, get_queryset_for_user, get_business_for_user, filter_to_own_if_staff, can_handle_receivables
from core.utils.cart import prune_stale_cart_lines
from core.utils.returns import refund_method_for, split_refund

from user.models import User
from django.contrib.messages import get_messages

from subscription.decorators import capacity_required

from activity.models import ActivityEvent
from activity.utils import log_activity, scope_events_for_user, summarize_items, log_audit

from django.views.decorators.clickjacking import xframe_options_sameorigin

# logging
import logging

# Create your views here.

PENDING_SALE_CAP = 5   # max concurrent 'pending' drafts per business (verify-soon queue, not storage)

def _finalize_sale(request, sale_obj, business, payment_status, payment_method,
                   payment_note, amount_str):
    """Post a sale for real: deduct stock, record the payment, log, lock.
    Shared by the complete-now checkout path and (Stage 2) the draft-list confirm."""

    # ── Stock deduction — completed sales only ──────────
    for item in sale_obj.sale_items.select_related('product', 'product__material').all():
        product = item.product
        if not product:
            continue
        quantity = item.quantity
        try:
            stock = Stock.objects.get(business=business, material=product.material)
            if stock.quantity >= quantity:
                stock.quantity -= quantity
                stock.save()
            else:
                messages.error(request, f"{stock.name}'s quantity - Insufficient stock. ")
        except Exception:
            pass

        if not product.is_service:
            if product.prepared_quantity < quantity:
                messages.warning(request, f"{product.name} - Insufficient stock.")
            product.prepared_quantity -= quantity
            product.save()

    # ── Payment capture ─────────────────────────────────
    if payment_method in ('credit', 'store_credit'):
        payment_method = 'cash'   # store credit paused — guard hand-crafted POSTs
    payment_note = (payment_note or '').strip()

    if payment_status == 'full':
        payment_amount = sale_obj.total_revenue
    elif payment_status == 'partial':
        try:
            payment_amount = Decimal((amount_str or '0').strip())
        except (ValueError, ArithmeticError, InvalidOperation):
            payment_amount = Decimal('0')
        if payment_amount <= 0:
            messages.warning(request, "Partial amount was invalid - recorded as debt instead.")
            payment_amount = Decimal('0')
        elif payment_amount >= sale_obj.total_revenue:
            payment_amount = sale_obj.total_revenue
            messages.info(request, "Amount matched total - recorded as paid in full")
    else:
        payment_amount = Decimal('0')

    method_display = None
    if payment_amount > 0:
        payment = SalesPayment.objects.create(
            sale=sale_obj, business=business, amount=payment_amount,
            method=payment_method, note=payment_note, created_by=request.user,
        )
        method_display = payment.get_method_display()
        paid_desc = f"via {method_display}"
        if payment_status == 'partial':
            paid_desc += f" (partial) · ₱{sale_obj.outstanding:.2f} outstanding"
        log_activity(
            business, request.user, 'sale.paid', target=payment, description=paid_desc,
            metadata={'reference': sale_obj.reference, 'amount': f"{payment_amount:.2f}",
                      'method': payment.method, 'outstanding': str(sale_obj.outstanding)},
        )

    if not sale_obj.total_revenue or sale_obj.total_revenue == 0:
        payment_text = "Free"
    elif payment_status == 'full' and method_display:
        payment_text = f"via {method_display}"
    elif payment_status == 'partial' and method_display:
        payment_text = f"via {method_display} (partial ₱{payment_amount:.2f})"
    else:
        payment_text = "Debt"

    items_text = summarize_items(sale_obj.sale_items.all(), prefix='-')
    log_activity(
        business, request.user, 'sale.completed', target=sale_obj,
        description=f"{payment_text} · {items_text}",
        metadata={'reference': sale_obj.reference, 'total': f"{sale_obj.total_revenue:.2f}",
                  'line_count': sale_obj.line_count, 'payment_status': payment_status,
                  'payment_method': payment_method if payment_status != 'utang' else None},
    )
    log_audit(
        business, request.user, 'create', target=sale_obj,
        new_values={'total_revenue': sale_obj.total_revenue, 'line_count': sale_obj.line_count,
                    'payment_status': payment_status,
                    'payment_method': payment_method if payment_status != 'utang' else None},
    )
    sale_obj.is_locked = True
    sale_obj.save(update_fields=['is_locked'])


def can_void_sale(sale):
    """Same-day, not already void, no returns, and the void window is open.
    Drafts (pending/canceled) were never posted, so there's nothing to void."""
    return (
        sale.status == 'completed'
        and not sale.is_void
        and not sale.returns.exists()
        and sale.date == timezone.localdate()
        and void_window_open(sale.business)
    )

@login_required(login_url='login')
def clear_sale(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    request.session['sale'] = {}
    request.session.pop('sale_discount_percent', None)
    request.session.modified = True
    messages.success(request, 'All items has been removed.')
    
    # HTMX
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'core/partials/_cart_response.html', {
            'cart_count':     0,
            'cart_items':     0,
            'total':          Decimal('0'),
            'cart_url':       'view-sale',
            'icon':           'bi bi-cart3',
            'label':          'Sales Record',
            'clear_sessions': 'clear-sale',
            'name':           'Products',
            'total_name':     'sales',
            'type':           'sales',
            'messages':       get_messages(request),
        })
        
    return_url = request.session.get('catalog_return')
    if return_url:
        return redirect(return_url)
    return redirect('product-list', business_slug=business.slug)

@login_required(login_url='login')
# @permission_required('staff_view')
@permission_required('read_only') # dev
def sale_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sales = get_queryset_for_user(request.user, Sale.objects.all()).filter(
        business=business, status='completed').order_by('-reference')   # drafts live in the draft list

    # for staff to see their own records
    sales = filter_to_own_if_staff(request.user, sales) 
    form = SaleFilterForm(request.GET or None)
    
    today = timezone.localdate()
    month = today.month
    year = today.year
    iso_year, iso_week, iso_weekday = today.isocalendar()
    last_year = iso_year - 1

    current_year = f"{year}-{month:02d}"   # zero-padded — f"-0{month}" breaks for Oct–Dec ("2026-010")

    period = request.GET.get('period')
    
    total_revenue = sales.total_revenue()
    average_total_revenue = sales.average_total_revenue()
    total_sales_count = sales.active().count()
    
    if form.is_valid():
        # search = form.cleaned_data.get('search')
        select_month = form.cleaned_data.get('select_month')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')
        
        
        if start_date and end_date:
            sales = sales.filter(date__range=(start_date, end_date))
        
        if select_month:
            # Option 1: strptime — safer, validates format, better for untrusted input
            parsed = datetime.strptime(select_month, '%Y-%m')

            # Option 2: split — simpler, faster, fine when input is trusted (e.g. type="month")
            # year, month = map(int, select_month.split('-'))
            sales = sales.filter(date__month=parsed.month)
        
        # if search:
        #     select_month = ''
        #     sales = sales.filter(
        #         Q(id__iexact=search) |
        #         Q(total_revenue__iexact=search) |
        #         Q(sale_items__quantity__iexact=search) |
        #         Q(line_count__iexact=search) 

        #     ).distinct() # <--- this allows to not have multiple rows when u filter 
            
        if period == 'last_week':
            if iso_week == 1:
                last_year_of_last_week = date(last_year, 12, 28).isocalendar()[1]
                sales = sales.filter(date__year=last_year, date__week=last_year_of_last_week)
            else:
                sales = sales.filter(date__year=iso_year, date__week=iso_week-1)
        
        period_map = {
            'month': {'date__month': month, 'date__year': year},
            'today': {'date': today},
            'week': {'date__week': iso_week, 'date__year': iso_year}

        }
        
        filter_kwargs = period_map.get(period)
        if filter_kwargs:
            sales = sales.filter(**filter_kwargs)
            
        total_revenue = sales.total_revenue()
        average_total_revenue = sales.average_total_revenue()
        total_sales_count = sales.active().count()
        
    max_revenue = sales.aggregate(max=Max('total_revenue'))['max'] or 0
    
    # Employee/seller filter — owner only
    user_filter = None
    users = []

    if request.user.role == 'owner':
        user_filter = request.GET.get('user')
        if user_filter and user_filter.isdigit():
            sales = sales.filter(created_by_id=int(user_filter))

        owner = business.user
        if owner:
            users.append({
                'id': owner.id,
                'display': owner.name or owner.username,
                'is_owner': True,
            })

        employee_users = Employee.objects.filter(
            business=business,
            is_locked=False,
            staff_user__isnull=False,
        ).select_related('staff_user').order_by('name')

        for emp in employee_users:
            u = emp.staff_user
            users.append({
                'id': u.id,
                'display': emp.name or u.name or u.username,
                'is_owner': False,
            })
            
    if request.user.role == 'owner' and user_filter and user_filter.isdigit():
        total_revenue = sales.total_revenue()
        average_total_revenue = sales.average_total_revenue()
        total_sales_count = sales.active().count()
        max_revenue = sales.active().aggregate(max=Max('total_revenue'))['max'] or 0

    # Payment-method filter — composes with the period/date/user filters above.
    # Match on "has at least one payment via this method" using an id subquery so
    # a multi-payment sale never duplicates rows (which would skew the aggregates).
    payment_methods = SalesPayment.PAYMENT_METHOD_CHOICES
    active_payment = request.GET.get('payment')
    if active_payment in {code for code, _ in payment_methods}:
        paid_sale_ids = SalesPayment.objects.filter(
            sale__in=sales, method=active_payment,
        ).values_list('sale_id', flat=True)
        sales = sales.filter(id__in=paid_sale_ids)
        total_revenue = sales.total_revenue()
        average_total_revenue = sales.average_total_revenue()
        total_sales_count = sales.active().count()
        max_revenue = sales.active().aggregate(max=Max('total_revenue'))['max'] or 0
    else:
        active_payment = None

    collected = SalesPayment.objects.filter(sale__in=sales.active()).aggregate(t=Sum('amount'))['t'] or 0
    receivables = (total_revenue or 0) - collected

    paginator = Paginator(sales.prefetch_related('payments'), 8)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    recent_events = ActivityEvent.objects.filter(
        verb__startswith='sale.', business=business,
    )
    recent_events = scope_events_for_user(recent_events, request.user)[:4]
    
    from core.utils.kpis import get_sale_kpis
    kpis = get_sale_kpis(business)
    
    def _pct(curr, base):
        """Returns (direction, abs_pct_string) or (None, None) if no comparison."""
        base = float(base or 0)
        curr = float(curr or 0)
        if base <= 0:
            return (None, None)
        pct = ((curr - base) / base) * 100
        if abs(pct) < 0.05:
            return ('flat', '0.0%')
        direction = 'up' if pct > 0 else 'down'
        return (direction, f"{abs(pct):.1f}%")

    c = kpis['current']
    sales_deltas = {
        'today_dir':     None, 'today_pct':     None,
        'today_rev_dir': None, 'today_rev_pct': None,
        'week_rev_dir':  None, 'week_rev_pct':  None,
        'month_rev_dir': None, 'month_rev_pct': None,
    }
    # count_today vs yesterday — use snapshot delta if available, else None
    if kpis['deltas'].get('count_today') is not None:
        d = kpis['deltas']['count_today']
        if d > 0:   sales_deltas['today_dir'], sales_deltas['today_pct'] = 'up', f"{int(d)}"
        elif d < 0: sales_deltas['today_dir'], sales_deltas['today_pct'] = 'down', f"{int(abs(d))}"
        else:       sales_deltas['today_dir'], sales_deltas['today_pct'] = 'flat', '0'

    sales_deltas['today_rev_dir'], sales_deltas['today_rev_pct'] = _pct(c['revenue_today'], c['revenue_yesterday'])
    sales_deltas['week_rev_dir'],  sales_deltas['week_rev_pct']  = _pct(c['revenue_week'],  c['revenue_last_week'])
    sales_deltas['month_rev_dir'], sales_deltas['month_rev_pct'] = _pct(c['revenue_month'], c['revenue_last_month'])

    # ── Embedded Receivables panel ─────────────────────────────────────────
    # Same "outstanding sales" data as the standalone Receivables page. Every
    # filter param is recv_-prefixed so it can't clash with this page's own
    # period/page/payment filters (one page = one request.GET). Shows ALL the
    # business's debts (money owed to the business), regardless of who rang the
    # sale — matching the standalone page.
    #
    # GUARD: owner/dev always see it; staff only if the owner granted them
    # can_handle_receivables. When not permitted we skip the work entirely (not
    # just hide it in the template) and the panel is left out of the page.
    can_view_receivables = can_handle_receivables(request.user, business)

    recv_page_obj = None
    recv_total_outstanding = Decimal('0')
    recv_period = recv_status = ''
    recv_filter_active = False
    recv_any_count = 0

    if can_view_receivables:
        recv_base = (
            Sale.objects.filter(business=business, status='completed')   # a pending draft isn't a receivable
            .prefetch_related('payments', 'returns')
            .order_by('-date')
        )

        recv_period = request.GET.get('recv_period', '')
        recv_status = request.GET.get('recv_status', '')

        recv_sales = recv_base
        if recv_period == 'today':
            recv_sales = recv_sales.filter(date=today)
        elif recv_period == 'last_week':
            recv_sales = recv_sales.filter(date__gte=today - timedelta(days=7))
        elif recv_period == 'week':
            recv_sales = recv_sales.filter(date__week=iso_week, date__iso_year=iso_year)
        elif recv_period == 'month':
            recv_sales = recv_sales.filter(date__month=month, date__year=year)

        # outstanding is a computed property, not a DB field — filter in Python.
        outstanding_sales = [s for s in recv_sales if s.outstanding > 0]
        if recv_status == 'partial':
            outstanding_sales = [s for s in outstanding_sales if s.amount_paid > 0]
        elif recv_status == 'utang':
            outstanding_sales = [s for s in outstanding_sales if s.amount_paid == 0]

        recv_total_outstanding = sum((s.outstanding for s in outstanding_sales), Decimal('0'))
        recv_paginator = Paginator(outstanding_sales, 7)
        recv_page_obj = recv_paginator.get_page(request.GET.get('recv_page'))

        # Business-wide count of unpaid sales, IGNORING the recv_ date/status filters —
        # lets the empty state tell "no debts at all" apart from "none in this range"
        # (e.g. all debts are June while the filter is July). Prevents the panel from
        # cheerfully saying "all paid up" when unpaid sales exist outside the window.
        recv_filter_active = bool(recv_period or recv_status)
        recv_any_count = sum(1 for s in recv_base if s.outstanding > 0) if recv_filter_active else recv_paginator.count
        
        # Pending-drafts count for the header "Drafts" chip (same staff scoping as the list).
        pending_count = filter_to_own_if_staff(
            request.user,
            get_queryset_for_user(request.user, Sale.objects.filter(business=business, status='pending'))
        ).count()


    context = {
        'page_obj': page_obj,
        'total_revenue': total_revenue,
        'average_total_revenue': average_total_revenue,
        'total_sales_count': total_sales_count,
        'max_revenue': max_revenue,
        'current_year': current_year, # this is for dynamic year for select month
        'section': 'sale',
        'recent_events': recent_events,
        
        'sales_deltas': sales_deltas, 
        'kpis': kpis,
        
        'collected': collected,
        'receivables': receivables,

        # employee
        'users': users,
        'active_user': user_filter,

        # payment-method filter
        'payment_methods': payment_methods,
        'active_payment': active_payment,

        # embedded receivables panel (recv_-prefixed params — see partial)
        'can_view_receivables': can_view_receivables,
        'recv_page_obj': recv_page_obj,
        'recv_total_outstanding': recv_total_outstanding,
        'recv_period': recv_period,
        'recv_status': recv_status,
        'recv_filter_active': recv_filter_active,
        'recv_any_count': recv_any_count,
        'pending_count': pending_count,

    }

    return render(request, 'Sales/sale_list.html', context)

@login_required(login_url='login')
# @permission_required('staff_view')
@permission_required('read_only') # dev
def sale_detail(request, sale_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)
    sale_items = sale.sale_items.select_related('product').order_by('product__is_service', 'id')
    sale_employees = sale.sale_employees.select_related('employee')
    total_salary_cost = sale_employees.aggregate(total_salary_cost=Sum('daily_rate'))['total_salary_cost'] or 0
    payments = sale.payments.select_related('created_by').order_by('created_at')
    
    context = {
        'sale': sale, 
        'sale_items': sale_items, 
        'sale_employees': sale_employees, 
        'total_salary_cost': total_salary_cost,
        'payments': payments,
        'can_void': can_void_sale(sale),
        'section': 'sale',
    }
    return render(request, 'Sales/sale_detail.html', context)

@login_required(login_url='login')
@permission_required('add') # dev
def add_to_sales(request, product_id, business_slug):
    business = get_business_for_user(request.user, business_slug)

    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=product_id)
    product_key = str(product.id)

    # session-based rental → resolve the chosen tier
    session = None
    if product.is_session_based:
        session_id = request.GET.get('session') or request.POST.get('session')
        session = product.sessions.filter(id=session_id).first() if session_id else None
        if session is None:
            messages.warning(request, f"Pick a session length for {product.name}.")

    if product.is_locked:
        messages.warning(request, f"{product.name} is locked - upgrade your plan or unlock it to sell.")

    elif product.is_session_based:
        if session:
            if product_key in sale and sale[product_key].get('session_id') == session.id:
                sale[product_key]['quantity'] += 1                  # same tier → another block
            else:
                sale[product_key] = {                              # new / switched tier
                    'id': product.id,
                    'name': f"{product.name} ({session.label})",
                    'quantity': 1,
                    'cost_price': '0',
                    'selling_price': str(session.price),
                    'session_id': session.id,
                }
        # no tier picked → already warned, nothing added

    elif product.is_service or product.prepared_quantity >= 1:
        if product_key in sale:
            if product.is_service or sale[product_key]['quantity'] < product.prepared_quantity:
                sale[product_key]['quantity'] += 1
            else:
                messages.warning(request, f"{product.name} - Insufficient stock.")
        else:
            sale[product_key] = {
                'id': product.id,
                'name': product.name,
                'quantity': 1,
                'cost_price': str(product.cost_price),
                'selling_price': str(product.selling_price),
            }
    else:
        messages.warning(request, f"{product.name} - Insufficient stock.")

    request.session['sale'] = sale
    request.session.modified = True
    
    # HTMX
    if request.headers.get('HX-Request') == 'true':
        total = sum(Decimal(str(item['selling_price'])) * item['quantity']
                for item in sale.values()        
        )

        resp = render(request, 'core/partials/_cart_response.html', {
            'label': 'Sales Record',
            'icon': 'bi bi-cart3',
            'total': total,
            'messages': get_messages(request),
            'cart_items': len(sale),
            'cart_count': sum(item['quantity'] for item in sale.values()),
            'cart_url': 'view-sale',
            'clear_sessions': 'clear-sale',
            'name': 'Products',
            'total_name': 'sales',
            'type': 'sales',
        })
        resp['HX-Trigger'] = 'cartChanged'
        return resp
    
    query_string = request.META.get('QUERY_STRING', '')
    if request.GET.get('next') == 'view-sale':
        return redirect('view-sale', business_slug=business.slug)
    url = reverse('product-list', kwargs={'business_slug': business.slug})
    return redirect(f"{url}?{query_string}" if query_string else url)


@login_required(login_url='login')
@permission_required('view') # dev
def view_sale(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = prune_stale_cart_lines(request, business, 'sale', Product)
    total_revenue = 0
    total_cost_price = 0
    # employees = Employee.objects.filter(user=owner)
    
    # # after confirming who's in shift it will save the checkbox.
    # selected_employee_ids = request.session.get('selected_employee_ids', [])
    # print('session', selected_employee_ids)
    # selected_employee_ids = [str(id) for id in selected_employee_ids]
    # print('string', selected_employee_ids)
    # total_salary_cost = Decimal(request.session.get('total_salary_cost', 0))
 
    items = []
    
    if sale:
        for product_id, data in sale.items():
            product = get_object_or_404(Product, business=business, id=product_id)
            quantity = data.get('quantity', 1)
            selling_price = data.get('selling_price')
            cost_price = data.get('cost_price')
        
        
            # computations
            total_cost_price_per_line = Decimal(cost_price) * quantity         
            
            total_cost_price += total_cost_price_per_line
            
            total_selling_price = Decimal(selling_price) * quantity
            total_revenue += total_selling_price
            

            
            # preset = product.product_preset_items.first()
            # if preset:
            #     supplier_name = preset.supplier_name
            # else:
            #     supplier_name = 'No supplier'

            items.append({
                'supplier': product.material.supplier.name if product.material else '',
                'id': product.id,
                'image': product.image.url if product.image else '',
                'name': product.name,
                'selling_price': selling_price,
                'quantity': quantity,
                'cost_price': cost_price,
                'total_selling_price': str(total_selling_price),
                'total_cost_price_per_line': total_cost_price_per_line,
                
            })
    
    line_count = len(items)
    request.session['sale'] = sale
    request.session['line_count'] = line_count
    request.session.modified = True
    
    paginator = Paginator(items, 5)
    page = request.GET.get('page')
    page_obj =  paginator.get_page(page)
    
    # How many filler rows to reach a full page
    blank_rows = range(paginator.per_page - len(page_obj.object_list))
    
    services = []
    if business.offers_services:
        services = (
            Product.services.filter(business=business)
            .annotate(units_sold=Coalesce(Sum('sale_items__quantity'), 0))
            .order_by('-units_sold', 'name')   # most-bought first
        )
    
    context = {
        'items': items, 
        'total_revenue': total_revenue, 
        'total_cost_price': total_cost_price,
        'page_obj': page_obj,
        'blank_rows': blank_rows,
        'services': services,
        'section': 'product',
        'cart_scope': 'sale',
        'sale_discount_percent': request.session.get('sale_discount_percent', 0),

        # 'employees': employees, 
        # 'selected_employee_ids': selected_employee_ids, 
        # 'total_salary_cost': total_salary_cost,

        }
    
    return render(request, 'Sales/view_sale.html', context)

@login_required(login_url='login')
@permission_required('view') # dev
def view_session_summary(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = prune_stale_cart_lines(request, business, 'sale', Product)
    total_revenue = 0
    total_cost_price = 0

    total_salary_cost = Decimal(request.session.get('total_salary_cost', 0))
    print('total_salary_cost', total_salary_cost)
    
    items = []
        
    if sale:
        for product_id, data in sale.items():
            product = get_object_or_404(Product, business=business, id=product_id)
            quantity = data.get('quantity', 1)
            cost_price = data.get('cost_price', 0)
            selling_price = data.get('selling_price', 0)
            
            # computations
            total_cost_price_per_line = Decimal(cost_price) * quantity
            total_cost_price += total_cost_price_per_line
            
            total_selling_price = Decimal(selling_price) * quantity
            total_revenue += total_selling_price

            items.append({
                'supplier_name': product.material.supplier.name if product.material else '',
                'id': product.id,
                'name': product.name,
                'selling_price': selling_price,
                'cost_price': cost_price,
                'quantity': quantity,
                'total_selling_price': str(total_selling_price),
                'total_cost_price_per_line': total_cost_price_per_line,

            })
            
    paginator = Paginator(items, 8)
    page = request.GET.get('page')
    page_obj =  paginator.get_page(page)
    
    request.session['sale'] = sale
    request.session.modified = True
    raw = request.GET.get('discount_percent')
    if raw is not None and business.enable_sale_discount:
        try:
            pct = Decimal(raw)
        except ArithmeticError:
            pct = Decimal('0')
        request.session['sale_discount_percent'] = str(max(Decimal('0'), min(pct, Decimal('100'))))

    discount_percent = Decimal(request.session.get('sale_discount_percent', '0') or '0')
    discount_amount  = total_revenue * discount_percent / Decimal('100')
    net_total        = max(total_revenue - discount_amount, Decimal('0'))

    context = {
        'items': items, 
        'page_obj': page_obj,
        'total_revenue': total_revenue, 
        'total_cost_price': total_cost_price, 
        'employees': 'employees', 
        'total_salary_cost': total_salary_cost, 
        'section': 'product',
        
        'discount_percent': discount_percent, 
        'discount_amount': discount_amount, 
        'net_total': net_total,
        }
    
    return render(request, 'Sales/view_session_summary.html', context)

@login_required(login_url='login')
@capacity_required('sale')
@permission_required('update') # dev
def confirm_view_summary(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    sale = prune_stale_cart_lines(request, business, 'sale', Product)
    line_count = request.session.get('line_count', 0)
    total_salary_cost = request.session.get('total_salary_cost', 0)

    # ── Complete-now vs park-as-pending (from the "Verify the payment?" modal) ──
    payment_status = request.POST.get('payment_status', 'full')
    payment_method = request.POST.get('payment_method', 'cash')
    payment_note   = request.POST.get('payment_note', '')
    amount_str     = request.POST.get('amount_paid', '0')

    sale_status = request.POST.get('sale_status', 'completed')
    # Deferring only makes sense for electronic payments we can't verify instantly.
    if sale_status == 'pending' and (payment_method not in ('gcash', 'bank') or payment_status == 'utang'):
        sale_status = 'completed'

    # Concurrent-pending cap — a short verify queue, not storage.
    if sale_status == 'pending':
        pending_count = Sale.objects.filter(business=business, status='pending').count()
        if pending_count >= PENDING_SALE_CAP:
            messages.warning(
                request,
                f"You have {PENDING_SALE_CAP} sales awaiting payment confirmation. "
                f"Confirm or cancel one before adding another sales."
            )
            return redirect('view-session-summary', business_slug=business.slug)

    total_revenue = 0
    total_cost_price = 0

    try:
        with transaction.atomic():
            sale_obj = Sale.objects.create(
                user=business.user, business=business,
                total_revenue=0, total_salary_cost=0,
                created_by=request.user, status=sale_status)

            for product_id, data in sale.items():
                product = get_object_or_404(Product, business=business, id=product_id)
                quantity = data.get('quantity', 1)
                cost_price = data.get('cost_price', 0)
                selling_price = data.get('selling_price', 0)
                session_id = data.get('session_id')

                total_cost_price += Decimal(cost_price) * quantity
                total_revenue    += Decimal(selling_price) * quantity

                SaleItem.objects.create(
                    sale=sale_obj, product=product, price_at_sale=selling_price,
                    cost_price=cost_price, quantity=quantity, session_id=session_id,
                )
            # NOTE: stock is deducted in _finalize_sale — completed sales only.
            # A pending draft snapshots the items but touches no inventory until confirmed.

            # ── Whole-order customer discount (%) ───────────────
            gross = max(total_revenue, Decimal('0'))
            discount_percent = Decimal('0')
            if business.enable_sale_discount:
                try:
                    discount_percent = Decimal(request.session.get('sale_discount_percent', '0') or '0')
                except ArithmeticError:
                    discount_percent = Decimal('0')
                discount_percent = max(Decimal('0'), min(discount_percent, Decimal('100')))

            sale_obj.discount_percent  = discount_percent
            sale_obj.discount_amount   = gross * discount_percent / Decimal('100')
            sale_obj.total_revenue     = max(gross - sale_obj.discount_amount, Decimal('0'))
            sale_obj.total_salary_cost = total_salary_cost
            sale_obj.line_count        = line_count
            sale_obj.save()

            if sale_status == 'completed':
                _finalize_sale(request, sale_obj, business,
                               payment_status, payment_method, payment_note, amount_str)
            else:
                # Park it — remember the intended payment so the draft-list
                # "Confirm" can finalize later without re-asking.
                sale_obj.pending_method = payment_method
                sale_obj.pending_status = payment_status
                if payment_status == 'partial':
                    try:
                        sale_obj.pending_amount = Decimal((amount_str or '0').strip())
                    except (ValueError, ArithmeticError, InvalidOperation):
                        sale_obj.pending_amount = None
                sale_obj.pending_note = (payment_note or '').strip()
                sale_obj.save(update_fields=['pending_method', 'pending_status',
                                             'pending_amount', 'pending_note'])
                log_audit(
                    business, request.user, 'create', target=sale_obj,
                    new_values={'status': 'pending', 'total_revenue': sale_obj.total_revenue,
                                'line_count': sale_obj.line_count, 'payment_method': payment_method},
                )

    except ValidationError:
        messages.error(request, f"Cannot complete the sale - Insufficient stock.")
        return redirect('view-sale', business_slug=business.slug)

    for key in ('total_salary_cost', 'line_count', 'sale_discount_percent'):
        request.session.pop(key, 0)

    request.session['sale'] = {}
    request.session.modified = True

    if sale_status == 'pending':
        # Non-blocking: cart's cleared, serve the next customer right away.
        messages.success(request, "Sale saved to pending. Confirm the payment once received.")
        return redirect('view-sale', business_slug=business.slug)

    return redirect('sale-summary', business_slug=sale_obj.business.slug, sale_id=sale_obj.id)


@login_required(login_url='login')
@permission_required('add')
def add_sales_payment(request, business_slug, sale_id):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)

    # GUARD: recording a debt payment is a receivables action. Owner/dev, or staff
    # the owner granted can_handle_receivables. Blocks direct URL/POST pokes even
    # though the button is hidden for non-permitted staff.
    if not can_handle_receivables(request.user, business):
        return HttpResponseForbidden("You don't have access to receivables.")
    is_hx = request.headers.get('HX-Request')

    if request.method == 'POST':
        amount_str = request.POST.get('amount', '').strip()
        method = request.POST.get('method', 'cash')
        note = request.POST.get('note', '').strip()
        next_param = request.POST.get('next', '')

        def form_error(msg):
            if is_hx:
                return render(request, 'core/partials/_payment_modal.html', {
                    'p_title': sale.reference, 'p_payer': 'customer',
                    'p_outstanding': sale.outstanding,
                    'p_total': sale.total_revenue, 'p_paid': sale.amount_paid,
                    'p_action': reverse('add-sales-payment', kwargs={
                        'business_slug': business.slug, 'sale_id': sale.id}),
                    'method_choices': SalesPayment.PAYMENT_METHOD_CHOICES,
                    'error': msg, 'amount_val': amount_str, 'method_val': method,
                    'note_val': note, 'p_next': next_param,
                })
            messages.error(request, msg)
            return redirect('add-sales-payment', business_slug=business_slug, sale_id=sale_id)

        if method in ('credit', 'store_credit'):
            # store credit paused — UI hides it; guard hand-crafted POSTs
            return form_error("Store credit is currently unavailable — choose another method.")

        try:
            amount = Decimal(amount_str)
        except (ValueError, ArithmeticError):
            return form_error("Enter a valid amount.")

        if amount <= 0:
            return form_error("Payment amount must be greater than ₱0.")

        overpay = amount > sale.outstanding

        with transaction.atomic():
            payment = SalesPayment.objects.create(
                sale=sale,
                business=business,
                amount=amount,
                method=method,
                note=note,
                created_by=request.user,
            )

            paid_desc = f"via {payment.get_method_display()}"
            if sale.outstanding > 0:
                paid_desc += f" (partial) · ₱{sale.outstanding:.2f} outstanding"

            log_activity(
                business, request.user, 'sale.paid',
                target=payment,
                description=paid_desc,
                metadata={
                    'reference': sale.reference,
                    'amount': str(amount),
                    'method': method,
                    'note': note,
                    'outstanding': str(sale.outstanding),
                },
            )
            log_audit(
                business, request.user, 'payment',
                target=sale,
                new_values={'amount': amount, 'method': method,
                            'outstanding_after': sale.outstanding},
                reason=note,
            )

        # ----- HX: show the Payment Recorded summary as a modal -----
        if is_hx:
            addmore = reverse('add-sales-payment', kwargs={
                'business_slug': business.slug, 'sale_id': sale.id})
            if next_param:
                addmore += f'?next={next_param}'
            return render(request, 'core/partials/_payment_recorded_modal.html', {
                'p_title': sale.reference,
                'p_payer': 'customer',
                'p_doc_label': 'Sale Total',
                'payment': payment,
                'p_total': sale.total_revenue,
                'p_paid': sale.amount_paid,
                'outstanding': sale.outstanding,
                'overpay': overpay,
                'p_view_url': reverse('sale-detail', kwargs={
                    'business_slug': business.slug, 'sale_id': sale.id}),
                'p_addmore_action': addmore,
            })

        # ----- non-HX fallback: warning + existing success-page redirect -----
        if overpay:
            messages.warning(
                request,
                f"Payment ₱{amount:.2f} exceeds outstanding ₱{sale.outstanding + amount:.2f}. "
                f"Outstanding will go negative (store credit to customer)."
            )
        messages.success(request, f"Payment of ₱{amount:.2f} recorded.")
        url = reverse('sale-payment-success', kwargs={
            'business_slug': business_slug,
            'sale_id': sale_id,
            'payment_id': payment.id,
        })
        if next_param:
            url += f'?next={next_param}'
        return redirect(url)

    # ----- GET: modal fragment (HX) or full page (fallback) -----
    if is_hx:
        return render(request, 'core/partials/_payment_modal.html', {
            'p_title': sale.reference,
            'p_payer': 'customer',
            'p_outstanding': sale.outstanding,
            'p_total': sale.total_revenue,
            'p_paid': sale.amount_paid,
            'p_action': reverse('add-sales-payment', kwargs={
                'business_slug': business.slug, 'sale_id': sale.id}),
            'method_choices': SalesPayment.PAYMENT_METHOD_CHOICES,
        })

    context = {
        'sale': sale,
        'outstanding': sale.outstanding,
        'method_choices': SalesPayment.PAYMENT_METHOD_CHOICES,
        'section': 'receivable',
    }
    return render(request, 'Sales/add_sales_payment.html', context)



@login_required(login_url='login')
@permission_required('view') # dev
def view_sale_summary(request, sale_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)
    sale_items = sale.sale_items.select_related('product')
    total_salary_cost = sale.sale_employees.aggregate(total_salary_cost=Sum('daily_rate'))['total_salary_cost'] or 0
    print(total_salary_cost)
    
    total_revenue = 0
    total_cost_price = 0

    items = []

    for item in sale_items:
        cost_price = item.cost_price
        quantity = item.quantity
        unsold_quantity = item.unsold_quantity

        total_cost_price_per_line = (cost_price * quantity)
        total_cost_price += total_cost_price_per_line

        total_selling_price = item.price_at_sale * quantity
        total_revenue += total_selling_price

        # Safely walk product → material → supplier 
        product  = item.product
        material = product.material if product else None
        supplier = material.supplier if material else None

        items.append({
            'supplier_name':              supplier.name if supplier else '',
            'id':                         product.id if product else None,
            'name':                       item.name,
            'quantity':                   item.quantity,
            'selling_price':              item.price_at_sale,
            'unsold_quantity':            unsold_quantity,
            'cost_price':                 cost_price,
            'total_cost_price_per_line':  total_cost_price_per_line,
            'total_selling_price':        total_selling_price,
            'is_service':                 product.is_service if product else False,
        })
    items.sort(key=lambda x: x['is_service'])  # goods first, services last
        
    paginator = Paginator(items, 6)
    page = request.GET.get('page')
    page_obj =  paginator.get_page(page)
    
    # How many filler rows to reach a full page
    blank_rows = range(paginator.per_page - len(page_obj.object_list))

    context = {
        'items': items, 
        'sale': sale,
        'page_obj': page_obj,
        'blank_rows': blank_rows,
        'total_cost_price': total_cost_price, 
        'total_revenue': total_revenue, 
        'total_salary_cost': total_salary_cost, 
        'can_void': can_void_sale(sale),
        'section': 'sale'
        }

    
    return render(request, 'Sales/view_sale_summary.html', context)

@login_required(login_url='login')
@xframe_options_sameorigin
def sale_receipt(request, business_slug, sale_id):
    business = get_business_for_user(request.user, business_slug)
    bp = getattr(business, 'plan', None)   # kept only to gate the OFFICIAL invoice

    # Plain sales slip prints on ALL plans (2026-06-29 decision). Only the official
    # BIR invoice is gated: Premium/Pro AND BIR-accredited (is_bir_active).
    sale = get_object_or_404(Sale, business=business, id=sale_id)
    
    sale_items = sale.sale_items.select_related('product').all()
    goods_items = [i for i in sale_items if not (i.product and i.product.is_service)]
    service_items = [i for i in sale_items if i.product and i.product.is_service]
    
    width = request.GET.get('width', '80')
    if width not in ('58', '80'):
        width = business.receipt_width or '80'
        
    context = {
        'sale': sale,
        'items': sale_items,
        'goods_items': goods_items,
        'service_items': service_items,
        'business': business,
        'width': width,
        'embed': request.GET.get('embed') == '1',
        'vat_summary': sale.vat_summary(),
        'is_official': bool(getattr(business, 'is_bir_active', False)) and bool(bp and bp.has_receipt_print()),
    }
    
    return render(request, 'sales/sale_receipt.html', context)

@login_required(login_url='login')
def sale_receipt_modal(request, business_slug, sale_id):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)
    return render(request, 'Sales/_receipt_modal.html', {'sale': sale})

@login_required(login_url='login')
@permission_required('add')   # owner + staff; tune if you want owner-only
def void_sale(request, business_slug, sale_id):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)
    is_hx = request.headers.get('HX-Request')

    if not can_void_sale(sale):
        messages.error(request, "This sale can no longer be voided — use Sales Returns instead.")
        if is_hx:
            resp = HttpResponse(status=204)
            resp['HX-Redirect'] = reverse('sale-summary', kwargs={'business_slug': business.slug, 'sale_id': sale.id})
            return resp
        return redirect('sale-summary', business_slug=business.slug, sale_id=sale.id)

    if request.method != 'POST':
        if is_hx:
            return render(request, 'core/partials/_void_modal.html', {
                'v_title': sale.reference,
                'v_subtitle': f"₱{sale.total_revenue or 0:.2f}",
                'v_note': "Voiding cancels this sale completely — it stops counting toward revenue and the cash drawer, and the items go back into stock. This is for mistakes on this shift, not customer returns.",
                'v_action': reverse('void-sale', kwargs={'business_slug': business.slug, 'sale_id': sale.id}),
                'v_icon': 'bi-receipt',
                'reasons': Sale.VOID_REASON_CHOICES,
            })
        return render(request, 'Sales/void_sale.html', {
            'sale': sale, 'reasons': Sale.VOID_REASON_CHOICES, 'section': 'sale',
        })

    reason = request.POST.get('void_reason', '').strip()
    action = request.POST.get('action', 'void')   # 'void' | 'reedit'

    with transaction.atomic():
        # 1) restock — exact reverse of confirm_view_summary
        for item in sale.sale_items.select_related('product', 'product__material'):
            product = item.product
            if not product:
                continue
            if not product.is_service:
                product.prepared_quantity += item.quantity
                product.save(update_fields=['prepared_quantity'])
            if product.material_id:
                stock = Stock.objects.filter(business=business, material=product.material).first()
                if stock:
                    stock.quantity += item.quantity
                    stock.save(update_fields=['quantity'])

        # 2) flag void — revenue + drawer auto-exclude via is_void
        sale.is_void = True
        sale.void_reason = reason
        sale.voided_by = request.user
        sale.voided_at = timezone.now()
        sale.save(update_fields=['is_void', 'void_reason', 'voided_by', 'voided_at'])

        log_activity(
            business, request.user, 'sale.voided',
            target=sale,
            description=reason or 'Voided',
            metadata={'reference': sale.reference, 'total': f"{sale.total_revenue or 0:.2f}"},
        )
        
        log_audit(
            business, request.user, 'void',
            target=sale,
            old_values={'is_void': False, 'total_revenue': sale.total_revenue},
            new_values={'is_void': True},
            reason=reason,
        )


    # 3) re-ring → reload items into the cart and jump to it
    if action == 'reedit':
        cart = {}
        for item in sale.sale_items.select_related('product'):
            product = item.product
            if not product:
                continue
            key = str(product.id)
            if key in cart:
                cart[key]['quantity'] += item.quantity
            else:
                cart[key] = {
                    'id': product.id,
                    'name': product.name,
                    'quantity': item.quantity,
                    'cost_price': str(item.cost_price),
                    'selling_price': str(item.price_at_sale),  # what was actually charged
                }
        request.session['sale'] = cart
        request.session.modified = True
        messages.success(request, f"Sale {sale.reference} voided — edit the items and save again.")
        return redirect('view-sale', business_slug=business.slug)

    messages.success(request, f"Sale {sale.reference} has been voided.")
    if request.user.role == 'owner':
        return redirect('dashboard', business_slug=business.slug)
    return redirect('product-list', business_slug=business.slug)

@login_required(login_url='login')
@permission_required('read_only')
def sale_draft_list(request, business_slug):
    """Parked sales (pending) + canceled ones — everything not yet completed.
    Defaults to the Pending tab (the active verify queue)."""
    business = get_business_for_user(request.user, business_slug)

    base = get_queryset_for_user(request.user, Sale.objects.all()).filter(business=business)
    base = filter_to_own_if_staff(request.user, base)

    pending  = base.filter(status='pending').order_by('-created_at').prefetch_related('sale_items')
    canceled = base.filter(status='canceled').order_by('-canceled_at').prefetch_related('sale_items')

    pending_count  = pending.count()
    canceled_count = canceled.count()

    tab = request.GET.get('tab', 'pending')
    if tab not in ('pending', 'canceled'):
        tab = 'pending'

    rows = pending if tab == 'pending' else canceled
    paginator = Paginator(rows, 8)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'Sales/sale_draft_list.html', {
        'section': 'sale',
        'tab': tab,
        'page_obj': page_obj,
        'pending_count': pending_count,
        'canceled_count': canceled_count,
        'pending_cap': PENDING_SALE_CAP,
    })

@login_required(login_url='login')
@capacity_required('sale')
@permission_required('update')
def confirm_sale_draft(request, business_slug, sale_id):
    """Payment landed → post the parked sale for real (stock + payment + lock)."""
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)

    if sale.status != 'pending':
        messages.error(request, "This sale isn't awaiting confirmation.")
        return redirect('sale-draft-list', business_slug=business.slug)

    if request.method != 'POST':
        return redirect('sale-draft-list', business_slug=business.slug)

    with transaction.atomic():
        # Snapshot the parked intent, then clear it — must happen BEFORE finalize
        # locks the row (the immutability guard only allows void fields once locked).
        p_status = sale.pending_status or 'full'
        p_method = sale.pending_method or 'cash'
        p_note   = sale.pending_note or ''
        p_amount = str(sale.pending_amount) if sale.pending_amount is not None else '0'

        sale.status = 'completed'
        sale.pending_method = ''
        sale.pending_status = ''
        sale.pending_amount = None
        sale.pending_note = ''
        sale.save(update_fields=['status', 'pending_method', 'pending_status',
                                 'pending_amount', 'pending_note'])

        _finalize_sale(request, sale, business, p_status, p_method, p_note, p_amount)

    messages.success(request, f"{sale.reference} confirmed and posted to sales.")
    return redirect('sale-summary', business_slug=business.slug, sale_id=sale.id)

@login_required(login_url='login')
@permission_required('update')
def cancel_sale_draft(request, business_slug, sale_id):
    """Payment never landed → mark the parked sale canceled. Nothing to reverse
    (a draft never touched stock or revenue). Kept on record, not deleted."""
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)
    is_hx = request.headers.get('HX-Request')

    if sale.status != 'pending':
        messages.error(request, "Only a pending sale can be canceled.")
        if is_hx:
            resp = HttpResponse(status=204)
            resp['HX-Redirect'] = reverse('sale-draft-list', kwargs={'business_slug': business.slug})
            return resp
        return redirect('sale-draft-list', business_slug=business.slug)

    if request.method != 'POST':
        if is_hx:
            return render(request, 'sales/partials/_cancel_draft_modal.html', {
                'sale': sale,
                'reasons': Sale.CANCEL_REASON_CHOICES,
                'action': reverse('sale-draft-cancel',
                                  kwargs={'business_slug': business.slug, 'sale_id': sale.id}),
            })
        return redirect('sale-draft-list', business_slug=business.slug)

    reason = request.POST.get('cancel_reason', '').strip()

    sale.status = 'canceled'
    sale.canceled_reason = reason
    sale.canceled_by = request.user
    sale.canceled_at = timezone.now()
    sale.save(update_fields=['status', 'canceled_reason', 'canceled_by', 'canceled_at'])

    log_activity(business, request.user, 'sale.canceled', target=sale,
                 description=reason or 'Canceled',
                 metadata={'reference': sale.reference})
    log_audit(business, request.user, 'update', target=sale,
              old_values={'status': 'pending'}, new_values={'status': 'canceled'}, reason=reason)

    messages.success(request, f"{sale.reference} canceled.")
    if is_hx:
        resp = HttpResponse(status=204)
        resp['HX-Redirect'] = reverse('sale-draft-list',
                                      kwargs={'business_slug': business.slug}) + '?tab=canceled'
        return resp
    return redirect('sale-draft-list', business_slug=business.slug)


# Map SalesReturn.reason → Waste.reason for damaged items
RETURN_TO_WASTE_REASON = {
    'defective': 'defective',
    'expired':   'expired',
    # everything else → 'damage' fallback
}

@login_required(login_url='login')
@permission_required('add')
  # owner-only (blocks staff)
def sales_return_create(request, business_slug, sale_id):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)
    sale_items = sale.sale_items.select_related('product').all()

    # Hiding the button on sale_detail isn't enough — the URL is still typeable, and the
    # form used to render with every row saying "Fully returned" and no input anywhere.
    if not sale.has_returnable_items:
        messages.info(request, f"{sale.reference} has nothing left to return.")
        return redirect('sale-detail', business_slug=business_slug, sale_id=sale.id)

    if request.method == 'POST':
        reason = request.POST.get('reason', 'other')
        reason_note = request.POST.get('reason_note', '').strip()

        # The posted refund_method is IGNORED (2026-07-12) — the split is computed below.
        #
        # The old store-credit guard used to live here, rejecting 'credit' outright. That
        # left CASH as the only possible method, which meant returning goods on an UNPAID
        # (utang) sale handed the customer money they had never paid us. The two things
        # were being conflated: STORE CREDIT (a wallet balance to spend later — still
        # paused, still not built) is not the same as DEDUCTING A REFUND FROM WHAT THIS
        # CUSTOMER ALREADY OWES, which is just arithmetic on an existing debt and has to
        # work. split_refund() does the latter and never touches the former.

        items_to_return = []
        total_refund = Decimal('0')

        for si in sale_items:
            qty_str = request.POST.get(f'qty_{si.id}', '0')
            try:
                qty = int(qty_str)
            except ValueError:
                qty = 0
            if qty <= 0:
                continue
            
            if qty > si.returnable_quantity:
                messages.error(request,
                    f"{si.name}: only {si.returnable_quantity} returnable "
                    f"(already returned {si.total_returned_quantity} of {si.quantity}).")
                return redirect('sales-return-create',
                                business_slug=business_slug, sale_id=sale_id)


            unit_refund_str = request.POST.get(f'unit_refund_{si.id}', str(si.price_at_sale))
            try:
                unit_refund = Decimal(unit_refund_str)
            except (ValueError, ArithmeticError):
                unit_refund = si.price_at_sale

            resellable = request.POST.get(f'resellable_{si.id}', 'true') == 'true'

            items_to_return.append({
                'sale_item': si,
                'qty': qty,
                'unit_refund': unit_refund,
                'resellable': resellable,
            })
            total_refund += unit_refund * qty

        if not items_to_return:
            messages.warning(request, "Pick at least one item to return.")
            return redirect('sales-return-create',
                            business_slug=business_slug, sale_id=sale_id)

        # In sales_return_create, after collecting items, before atomic block:
        already_refunded = sale.amount_refunded  # already a property on Sale
        max_refund = (sale.total_revenue or Decimal('0')) - already_refunded

        if total_refund > max_refund:
            messages.error(request,
                f"Refund ₱{total_refund:.2f} exceeds remaining refundable ₱{max_refund:.2f}.")
            return redirect('sales-return-create',
                            business_slug=business_slug, sale_id=sale_id)

        # ★ Debt first, cash second. What the customer still owes is knocked off before a
        # single peso is handed back, so an utang sale can never pay out cash for goods
        # that were never paid for. See core/utils/returns.split_refund.
        refund_cash, refund_credit = split_refund(sale.outstanding, total_refund)
        refund_method = refund_method_for(refund_cash, refund_credit)

        with transaction.atomic():
            return_obj = SalesReturn.objects.create(
                original_sale=sale,
                business=business,
                reason=reason,
                reason_note=reason_note,
                refund_total=total_refund,
                refund_cash=refund_cash,
                refund_credit=refund_credit,
                refund_method=refund_method,
                created_by=request.user,
            )

            damaged_items = []   # collect for single Waste record

            for item in items_to_return:
                si = item['sale_item']
                qty = item['qty']

                SalesReturnItem.objects.create(
                    sales_return=return_obj,
                    original_sale_item=si,
                    name=si.name,
                    quantity=qty,
                    unit_refund=item['unit_refund'],
                    resellable=item['resellable'],
                )

                if item['resellable']:
                    # Back to inventory: bump Stock + Product.prepared_quantity
                    if si.product and si.product.material:
                        try:
                            stock = Stock.objects.get(
                                business=business, material=si.product.material
                            )
                            stock.quantity += qty
                            stock.save()
                        except Stock.DoesNotExist:
                            pass

                    if si.product:
                        si.product.prepared_quantity += qty
                        si.product.save(update_fields=['prepared_quantity'])
                else:
                    # Mark for Waste
                    damaged_items.append({
                        'sale_item': si,
                        'qty': qty,
                    })

            # If any damaged items, create a Waste record bundling them
            if damaged_items:
                waste_reason = RETURN_TO_WASTE_REASON.get(reason, 'damage')
                total_waste_cost = sum(
                    (d['sale_item'].cost_price or Decimal('0')) * d['qty']
                    for d in damaged_items
                )

                waste = Waste.objects.create(
                    business=business,
                    user=business.user,
                    total_cost=total_waste_cost,
                    reason=waste_reason,
                    created_by=request.user,
                )

                for d in damaged_items:
                    si = d['sale_item']
                    WasteItem.objects.create(
                        waste=waste,
                        product=si.product,
                        price=si.cost_price or Decimal('0'),
                        quantity=d['qty'],
                        name=si.name,
                    )
                    
                items_text = ", ".join(f"-{d['qty']} {d['sale_item'].name}" for d in damaged_items[:2])
                extras = len(damaged_items) - 2
                if extras > 0:
                    items_text += f", +{extras} more"
                log_activity(
                    business, request.user, 'waste.recorded',
                    target=waste,
                    description=f"{waste.get_reason_display()} · {items_text}",
                    metadata={'reason': waste_reason, 'total': f"{total_waste_cost:.2f}",
                              'sales_return_id': return_obj.id},
                )
                
            items_text = summarize_items(
                return_obj.items.all(),
                sign_for=lambda it: '+' if it.resellable else '-',
            )
            log_activity(
                business, request.user, 'sale.refunded',
                target=return_obj,
                description=f"{items_text}",
                metadata={'reference': return_obj.reference,
                          'total': f"{total_refund:.2f}",
                          'reason': reason,
                          'refund_method': refund_method},
            )
            log_audit(
                business, request.user, 'return',
                target=return_obj.original_sale,
                new_values={'return_ref': return_obj.reference,
                            'refund_total': total_refund,
                            'refund_method': refund_method},
                reason=reason,
            )

        messages.success(request, f"Return {return_obj.reference} recorded.")
        return redirect('sales-return-success', business_slug=business.slug, return_id=return_obj.id)

    context = {
        'sale': sale,
        'sale_items': sale_items,
        'reason_choices': SalesReturn.REASON_CHOICES,
        # The method is no longer picked — the form explains the split instead, and it
        # needs the balance to do that.
        'outstanding': sale.outstanding,
        'section': 'sale-return',
    }
    return render(request, 'Sales/sales_return_create.html', context)

@login_required(login_url='login')
@permission_required('add') # dev
@permission_required('staff_view')   # owner-only (blocks staff)
def return_recorded(request, business_slug, return_id):
    business = get_business_for_user(request.user, business_slug)
    return_obj = get_object_or_404(SalesReturn, business=business, id=return_id)
    items = return_obj.items.select_related('original_sale_item').all()

    context = {
        'return_obj': return_obj,
        'items': items,
        'section': 'sale-return',
    }
    return render(request, 'Sales/return_recorded.html', context)
    
@login_required(login_url='login')
@permission_required('staff_view')   # owner-only (blocks staff)
def sales_return_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    returns = SalesReturn.objects.filter(business=business).select_related(
        'original_sale', 'created_by'
    ).order_by('-date', '-created_at')
    
    # Only reasons that have at least one return for this business
    used_reason_values = (
        SalesReturn.objects
        .filter(business=business)
        .values_list('reason', flat=True)
        .distinct()
        .order_by('reason')
    )
    reason_dict = dict(SalesReturn.REASON_CHOICES)
    reason_choices = [
        (v, reason_dict.get(v, v.replace('_', '').title()))
        for v in used_reason_values if v
    ]

    form = SalesReturnFilterForm(request.GET or None)
    if form.is_valid():
        q = form.cleaned_data.get('q')
        reason = form.cleaned_data.get('reason')
        select_month = form.cleaned_data.get('select_month')
        sd = form.cleaned_data.get('start_date')
        ed = form.cleaned_data.get('end_date')
        period = request.GET.get('period', '')
        
        today = timezone.localdate()
        iso_year, iso_week, _ = today.isocalendar()

        if period == 'today':
            returns = returns.filter(date=today)
        elif period == 'last_week':
            last_week = today - timedelta(days=7)
            returns = returns.filter(date__gte=last_week)
        elif period == 'week':
            returns = returns.filter(date__week=iso_week, date__year=iso_year)
        elif period == 'month':
            returns = returns.filter(date__month=today.month, date__year=today.year)

        if q:
            filters = Q(reference__icontains=q)
            try:
                filters |= Q(refund_total=Decimal(q))
            except (InvalidOperation, ValueError):
                pass
            returns = returns.filter(filters)

        if reason:
            returns = returns.filter(reason=reason)

        if select_month:
            try:
                parsed = datetime.strptime(select_month, '%Y-%m')
                returns = returns.filter(date__year=parsed.year, date__month=parsed.month)
            except ValueError:
                pass

        if sd and ed:
            returns = returns.filter(date__range=(sd, ed))

    totals = returns.aggregate(
        total_refunded=Sum('refund_total'),
        avg_refund=Avg('refund_total'),
    )

    paginator = Paginator(returns, 7)
    page_obj = paginator.get_page(request.GET.get('page'))

    today = timezone.localdate()
    return render(request, 'Sales/sales_return_list.html', {
        'page_obj': page_obj,
        'form': form,
        'section': 'sale-return',
        'total_refunded': totals['total_refunded'] or 0,
        'avg_refund': totals['avg_refund'] or 0,
        'total_count': returns.count(),
        'current_year': f"{today.year}-0{today.month}",
        'reason_choices': reason_choices,
    })

@login_required(login_url='login')
@permission_required('staff_view')   # owner-only (blocks staff)
def sales_return_detail(request, business_slug, return_id):
    business = get_business_for_user(request.user, business_slug)
    return_obj = get_object_or_404(SalesReturn, business=business, id=return_id)
    items = return_obj.items.select_related('original_sale_item').all()

    return render(request, 'Sales/sales_return_detail.html', {
        'return_obj': return_obj,
        'items': items,
        'section': 'sale-return',
    })

@login_required(login_url='login')

@permission_required('add') # dev
def payment_recorded(request, business_slug, sale_id, payment_id):
    business = get_business_for_user(request.user, business_slug)
    sale = get_object_or_404(Sale, business=business, id=sale_id)
    payment = get_object_or_404(SalesPayment, business=business, id=payment_id)
    
    context = {
        'sale': sale,
        'payment': payment,
        'outstanding': sale.outstanding,
        'section': 'receivable',
    }
    return render(request, 'Sales/payment_recorded.html', context)

@login_required(login_url='login')
def sales_receivables(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    # GUARD: owner/dev, or staff the owner granted can_handle_receivables.
    if not can_handle_receivables(request.user, business):
        messages.error(request, "You don't have access to receivables.")
        return redirect('sale-list', business_slug=business.slug)

    sales = (
        Sale.objects.filter(business=business, status='completed')   # drafts aren't debts
        .prefetch_related('payments', 'returns')
        .order_by('-date')
    )


    # Date filters (SQL-level — applies to the queryset)
    period = request.GET.get('period', '')
    select_month = request.GET.get('select_month', '')
    start_date = request.GET.get('start_date', '')
    end_date = request.GET.get('end_date', '')
    status_filter = request.GET.get('status', '')

    today = timezone.localdate()
    iso_year, iso_week, _ = today.isocalendar()

    if period == 'today':
        sales = sales.filter(date=today)
    elif period == 'last_week':
        last_week = today - timedelta(days=7)
        sales = sales.filter(date__gte=last_week)
    elif period == 'week':
        sales = sales.filter(date__week=iso_week, date__iso_year=iso_year)
    elif period == 'month':
        sales = sales.filter(date__month=today.month, date__year=today.year)

    if select_month:
        try:
            parsed = datetime.strptime(select_month, '%Y-%m')
            sales = sales.filter(date__year=parsed.year, date__month=parsed.month)
        except ValueError:
            pass

    if start_date and end_date:
        sales = sales.filter(date__range=(start_date, end_date))

    # Python-side filtering (outstanding is a property, not a DB field)
    outstanding_sales = [s for s in sales if s.outstanding > 0]

    if status_filter == 'partial':
        outstanding_sales = [s for s in outstanding_sales if s.amount_paid > 0]
    elif status_filter == 'utang':
        outstanding_sales = [s for s in outstanding_sales if s.amount_paid == 0]

    total_outstanding = sum((s.outstanding for s in outstanding_sales), Decimal('0'))

    paginator = Paginator(outstanding_sales, 7)
    page_obj = paginator.get_page(request.GET.get('page'))

    today_str = f"{today.year}-{today.month:02d}"

    context = {
        'page_obj': page_obj,
        'total_outstanding': total_outstanding,
        'section': 'receivable',
        'current_year': today_str,
    }
    return render(request, 'Sales/sales_receivables.html', context)

# @login_required(login_url='login')
# @permission_required('staff_add')
# def add_daily_rate_to_sale(request):
#     business = get_business_for_user(request.user, business_slug)
#     total_salary_cost = 0
#     if request.method == 'POST':
#         employee_ids = request.POST.getlist('employees')
        
#         employee = Employee.objects.filter(id__in=employee_ids, user=owner)
        
#         if employee_ids:
#             total_salary_cost = employee.aggregate(daily_rate=Sum('daily_rate'))['daily_rate'] or 0
            
#         if total_salary_cost:
#             messages.success(request, f"{employee.count()} staff added to shift. ₱{total_salary_cost} labor cost will be logged in summary.")
#         else:
#             messages.success(request, "Shift cleared. No labor cost will be recorded.")
            
#         request.session['total_salary_cost'] = str(total_salary_cost)
#         request.session['selected_employee_ids'] = employee_ids
#         request.session.modified = True
            
#     return redirect('view-sale')

@login_required(login_url='login')
def edit_view_sale_quantity(request, product_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=product_id)
    product_key = str(product.id)
    if request.method == 'POST':
        raw_qty = request.POST.get(f"new_quantity", 1)
        new_quantity = int(raw_qty) if raw_qty else None
        
        if product.is_service or product.prepared_quantity >= new_quantity:
                
            if new_quantity < 1:
                new_quantity = 1
                
            sale[product_key]['quantity'] = new_quantity
            # messages.success(request, f"{product.name}'s quantity has been updated.")
        else:
            messages.warning(request, f"{product.name} - Insufficient stock.")
    
    request.session['sale'] = sale
    request.session.modified = True
    
    page = request.GET.get('page', '')
    url = reverse('view-sale', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)
    
@login_required(login_url='login')
def edit_total_selling_price(request, product_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=product_id)
    product_key = str(product.id)
    
    if sale:
        data = sale[product_key]
        quantity = data.get('quantity', 0)
        selling_price = data.get('selling_price')
        raw_selling_price = request.POST.get('new_total_selling_price') 
        new_total_selling_price = Decimal(raw_selling_price) / quantity if raw_selling_price else None
        
        if new_total_selling_price and new_total_selling_price != Decimal(selling_price):
            sale[product_key]['selling_price'] = str(new_total_selling_price)
            # messages.success(request, f"The revenue for {product.name} has been updated.")
    request.session['sale'] = sale
    request.session.modified = True
    
    page = request.GET.get('page', '')
    url = reverse('view-sale', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)

@login_required(login_url='login')
def edit_unsold_quantity(request, product_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=product_id)
    product_key = str(product.id)
    if sale:
        new_unsold_quantity = int(request.POST.get(f"new_unsold_quantity"))
        quantity = sale[product_key]['quantity']
        
        if new_unsold_quantity <= quantity:
            sale[product_key]['unsold_quantity'] = new_unsold_quantity
        #     messages.success(request, f"The unsold quantity has been updated.")
        # else:
        #     messages.warning(request, f"The unsold quantity can't exceed the quantity.")
        
    request.session['sale'] = sale
    request.session.modified = True
    
    page = request.GET.get('page', '')
    url = reverse('view-sale', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)

@login_required(login_url='login')
@permission_required('delete') # dev
def delete_view_sale_quantity(request, product_id, business_slug):
    business = get_business_for_user(request.user, business_slug)
    
    sale = request.session.get('sale', {})
    product = get_object_or_404(Product, business=business, id=product_id)
    product_key = str(product.id)
    
    if product_key in sale:
        del sale[product_key]
        # messages.success(request, f"{product.name} has been removed from the sale.")
        
    request.session['sale'] = sale
    request.session.modified = True
    
    page = request.GET.get('page', '')
    url = reverse('view-sale', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)



    
    
    
    


    
