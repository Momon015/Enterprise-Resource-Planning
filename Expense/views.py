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

from Expense.models import (PurchaseItem, Purchase, Waste, WasteItem, Expense, 
    ExpenseItem, MiscExpense, PurchaseReturn, PurchaseReturnItem,
    PurchasePayment)

from Expense.forms import (PurchaseForm, PurchaseItemForm, PurchaseFilterForm,
    ProductWasteForm, MaterialWasteForm, WasteItemFilterForm, ExpenseForm, ExpenseFilterForm, 
    MiscExpenseForm, PurchaseReturnFilterForm)

from Employee.models import Shift, ShiftEmployee, Employee
from Employee.utils import void_allowed

from Supplier.models import Material
from Supplier.forms import MaterialForm

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
from django.db.models import Sum, Avg, Max, Count, OuterRef, Subquery

from user.models import User

from core.utils.owner import get_owner, permission_required, get_queryset_for_user, get_business_for_user, filter_to_own_if_staff, can_handle_payables
from core.utils.cart import prune_stale_cart_lines
from core.utils.returns import refund_method_for, split_refund

from django.contrib.messages import get_messages

from subscription.decorators import capacity_required

from activity.models import ActivityEvent
from activity.utils import log_activity, scope_events_for_user, summarize_items, log_audit, needs_owner_review, log_margin_drop
# logging
import logging

# Create your views here.

logger = logging.getLogger('Expense')

def can_void_purchase(purchase, user):
    """Whether `user` may void this purchase. NEVER decorate — see can_void_sale.

    Mirrors the sale gate (Employee.utils.void_allowed carries the rule + why). Voiding
    a purchase moves cash the opposite way — expected_cash goes UP, exposing theft rather
    than hiding it — so this is mistake-correction rather than an anti-skim seal, gated
    the same way for one consistent rule.
    """
    return (
        not purchase.is_void
        and not purchase.returns.exists()
        and void_allowed(
            purchase.business, user,
            on_date=purchase.purchase_date,
            rung_at=purchase.created_at,
            payments=purchase.payments,
            created_by_id=purchase.created_by_id,
        )
    )

def _normalize_cart_discount_mode(request, business):
    """Keep the cart consistent with the active purchase-discount mode.
    % mode  → force every per-item flat discount to 0.
    flat mode → drop any leftover whole-order %.
    Prevents stale discounts after the owner flips the mode mid-cart.
    """
    cart = request.session.get('cart', {})
    if business.enable_purchase_discount:
        changed = False
        for data in cart.values():
            if Decimal(data.get('discount', 0) or 0) != 0:
                data['discount'] = '0'
                changed = True
        if changed:
            request.session['cart'] = cart
            request.session.modified = True
    else:
        if request.session.pop('purchase_discount_percent', None) is not None:
            request.session.modified = True

"""clearing cart just in case there's a bug """
@login_required(login_url='login')
def clear_cart(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    request.session['cart'] = {}
    request.session.pop('purchase_discount_percent', None)
    request.session.modified = True
    messages.success(request, "All items has been removed.")
    
    
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'core/partials/_cart_response.html', {
            'cart_count':     0,
            'cart_items':     0,
            'total':          Decimal('0'),
            'cart_url':       'view-cart',
            'icon':           'bi bi-cart3',
            'label':          'Purchase Record',
            'clear_sessions': 'clear-cart',
            'name':           'Materials',
            'total_name':     'cost',
            'type':           'purchase',
            'messages':       get_messages(request),
        })
    
    
    
    
    return redirect('material-list', business_slug=business.slug)

"""clearing cart just in case there's a bug """

def _build_payables_context(request, business):
    """Shared Payables (open supplier balances) context for BOTH the embedded
    panel in purchase_history AND the standalone purchase-payables page — one
    partial, one computation, no drift.

    Every param is pay_-prefixed (pay_period, pay_status, pay_page, plus the
    standalone-only pay_select_month / pay_start_date / pay_end_date) so the
    panel's filters never clash with the purchase list's own period/page/payment
    filters when it's embedded (one page = one request.GET). Mirrors the
    receivables recv_-prefixed block in Sales.sale_list.
    """
    from datetime import datetime as _dt

    today = timezone.localdate()
    iso_year, iso_week, _ = today.isocalendar()

    base = (
        Purchase.objects.filter(business=business)
        .select_related('status')
        .prefetch_related('payments', 'returns')
        .order_by('due_date', '-purchase_date')
    )

    pay_period = request.GET.get('pay_period', '')
    pay_status = request.GET.get('pay_status', '').strip()
    pay_select_month = request.GET.get('pay_select_month', '').strip()
    pay_start_date = request.GET.get('pay_start_date', '').strip()
    pay_end_date = request.GET.get('pay_end_date', '').strip()

    purchases = base
    if pay_period == 'today':
        purchases = purchases.filter(purchase_date=today)
    elif pay_period == 'last_week':
        purchases = purchases.filter(purchase_date__gte=today - timedelta(days=7))
    elif pay_period == 'week':
        purchases = purchases.filter(purchase_date__week=iso_week, purchase_date__iso_year=iso_year)
    elif pay_period == 'month':
        purchases = purchases.filter(purchase_date__month=today.month, purchase_date__year=today.year)

    if pay_select_month:
        try:
            parsed = _dt.strptime(pay_select_month, '%Y-%m')
            purchases = purchases.filter(purchase_date__year=parsed.year, purchase_date__month=parsed.month)
        except ValueError:
            pass

    if pay_start_date and pay_end_date:
        try:
            sd = _dt.strptime(pay_start_date, '%Y-%m-%d').date()
            ed = _dt.strptime(pay_end_date, '%Y-%m-%d').date()
            purchases = purchases.filter(purchase_date__range=(sd, ed))
        except ValueError:
            pass

    # outstanding is a computed property, not a DB field — filter in Python.
    outstanding_purchases = [p for p in purchases if p.outstanding > 0]
    if pay_status == 'partial':
        outstanding_purchases = [p for p in outstanding_purchases if p.amount_paid > 0]
    elif pay_status == 'utang':
        outstanding_purchases = [p for p in outstanding_purchases if p.amount_paid == 0]

    pay_total_outstanding = sum((p.outstanding for p in outstanding_purchases), Decimal('0'))
    pay_overdue_count = sum(1 for p in outstanding_purchases if p.due_date and p.due_date < today)

    paginator = Paginator(outstanding_purchases, 7)
    pay_page_obj = paginator.get_page(request.GET.get('pay_page'))

    # Business-wide count of unpaid purchases, IGNORING the pay_ filters — lets the
    # empty state tell "no bills at all" apart from "none in this range" (e.g. every
    # bill is June while the filter is July). Same honest-empty-state fix as recv_.
    pay_filter_active = bool(pay_period or pay_status or pay_select_month or (pay_start_date and pay_end_date))
    pay_any_count = sum(1 for p in base if p.outstanding > 0) if pay_filter_active else paginator.count

    return {
        'pay_page_obj': pay_page_obj,
        'pay_total_outstanding': pay_total_outstanding,
        'pay_overdue_count': pay_overdue_count,
        'pay_period': pay_period,
        'pay_status': pay_status,
        'pay_filter_active': pay_filter_active,
        'pay_any_count': pay_any_count,
        'today': today,
    }


@login_required(login_url='login')
# @permission_required('staff_view')
@permission_required('read_only') # dev
def purchase_history(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    purchases = get_queryset_for_user(request.user, Purchase.objects.all()).filter(business=business).order_by('-reference')
    # show their own records if staff
    purchases = filter_to_own_if_staff(request.user, purchases)
    # forms
    form = PurchaseFilterForm(request.GET or None)
    
    # count, sum and purchased total cost.
    total_count = purchases.active().count()
    total_cost = purchases.purchase_total_cost()
    average_cost = purchases.average_total_cost()
    
    today = timezone.localdate()
    iso_year, iso_week, iso_weekday = today.isocalendar()
    
    year = today.year
    month = today.month
    
    current_year = f"{year}-{today.month:02d}"
    
    if form.is_valid():
        # search = form.cleaned_data.get('search')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')
        select_month = form.cleaned_data.get('select_month')
        period = form.cleaned_data.get('period')
        
        # if search:
        #     purchases = purchases.filter(
        #         Q(line_count__iexact=search) |
        #         Q(id__iexact=search) | 
        #         Q(materials__quantity__iexact=search) |
        #         Q(total_cost__icontains=search)
        #     ).distinct() # allows not to have duplicates after using search

        if select_month:
            parsed_year, parsed_month = map(int, select_month.split("-"))
            purchases = purchases.filter(purchase_date__month=parsed_month, purchase_date__year=parsed_year)
            
        """
        if you are using request.GET.get for getting the
        url DATE strings you need to convert it using
        strptime/strftime before you can extract the year,
        month, and day. The isocalendar() needs to be unpack 
        to get the year, number of weeks, and weekday. I
        intentionally made it to use form.cleaned_data and 
        request.GET.get() for learning purposes only.
        """
            
        if start_date and end_date:
            purchases = purchases.filter(purchase_date__range=(start_date, end_date))
            
        """
        This is for quick filter for today, this week, this month, and
        this year using the timezone.now().
        """
        
        last_year = iso_year - 1
        
        if period == 'last_week':
            """ Get the last week for last year using date. """
            
            last_week_of_last_year = date(last_year, 12, 28).isocalendar()[1]

            if iso_week == 1:
                purchases = purchases.filter(purchase_date__week=last_week_of_last_year, purchase_date__year=last_year)
            else:
                purchases = purchases.filter(purchase_date__week=iso_week - 1, purchase_date__year=year)
        
        else:
            # for mapping period
            period_map = {
                "today": {'purchase_date__day': today.day},
                "week": {"purchase_date__year": year, "purchase_date__week": iso_week},
                "month": {"purchase_date__month": month, "purchase_date__year": year},
            }
            filter_kwargs = period_map.get(period)
            if filter_kwargs:
                purchases = purchases.filter(**filter_kwargs)
            
        total_count = purchases.active().count()
        total_cost = purchases.purchase_total_cost()
        average_cost = purchases.average_total_cost()
        
    # Owner-only user/seller filter 
    user_filter = None
    users = []

    if request.user.role == 'owner':
        user_filter = request.GET.get('user')
        if user_filter and user_filter.isdigit():
            purchases = purchases.filter(created_by_id=int(user_filter))

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

    # Recompute totals after filter
    if user_filter and user_filter.isdigit():
        total_cost = purchases.purchase_total_cost()
        average_cost = purchases.average_total_cost()

    # Payment-method filter — composes with the period/date/user filters above.
    # Match on "has at least one payment via this method" using an id subquery so
    # a multi-payment purchase never duplicates rows (which would skew totals).
    payment_methods = PurchasePayment.PAYMENT_METHOD_CHOICES
    active_payment = request.GET.get('payment')
    if active_payment in {code for code, _ in payment_methods}:
        paid_purchase_ids = PurchasePayment.objects.filter(
            purchase__in=purchases, method=active_payment,
        ).values_list('purchase_id', flat=True)
        purchases = purchases.filter(id__in=paid_purchase_ids)
        total_count = purchases.active().count()
        total_cost = purchases.purchase_total_cost()
        average_cost = purchases.average_total_cost()
    else:
        active_payment = None

    paid = PurchasePayment.objects.filter(purchase__in=purchases.active()).aggregate(t=Sum('amount'))['t'] or 0
    payables = (total_cost or 0) - paid

    paginator = Paginator(purchases.prefetch_related('payments'), 8)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    ytd_start = timezone.localdate().replace(month=1, day=1)
    ytd_spend = (
        Purchase.objects.active().filter(business=business, purchase_date__gte=ytd_start)
        .aggregate(total_cost=Sum('total_cost'))['total_cost'] or 0
    )

    
    recent_events = ActivityEvent.objects.filter(
        verb__startswith='purchase.', business=business,
    )
    
    recent_events = scope_events_for_user(recent_events, request.user)[:4]
    
    from core.utils.kpis import get_purchase_kpis
    kpis = get_purchase_kpis(business)
    def _pct(curr, base):
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
    purchase_deltas = {
        'today_dir':     None, 'today_pct':     None,
        'today_cost_dir': None, 'today_cost_pct': None,
        'week_cost_dir':  None, 'week_cost_pct':  None,
        'month_cost_dir': None, 'month_cost_pct': None,
    }
    if kpis['deltas'].get('count_today') is not None:
        d = kpis['deltas']['count_today']
        if d > 0:   purchase_deltas['today_dir'], purchase_deltas['today_pct'] = 'up', f"{int(d)}"
        elif d < 0: purchase_deltas['today_dir'], purchase_deltas['today_pct'] = 'down', f"{int(abs(d))}"
        else:       purchase_deltas['today_dir'], purchase_deltas['today_pct'] = 'flat', '0'

    purchase_deltas['today_cost_dir'], purchase_deltas['today_cost_pct'] = _pct(c['cost_today'], c['cost_yesterday'])
    purchase_deltas['week_cost_dir'],  purchase_deltas['week_cost_pct']  = _pct(c['cost_week'],  c['cost_last_week'])
    purchase_deltas['month_cost_dir'], purchase_deltas['month_cost_pct'] = _pct(c['cost_month'], c['cost_last_month'])

    # ── Embedded Payables panel (pay_-prefixed params — see _payables_panel.html) ──
    # Owner/dev always; staff only where the owner granted can_handle_payables.
    # When not permitted we skip the work entirely and leave the panel off the page.
    can_view_payables = can_handle_payables(request.user, business)
    pay_ctx = _build_payables_context(request, business) if can_view_payables else {}

    context = {
        'page_obj': page_obj,      
        'total_count': total_count, 
        'total_cost': total_cost, 
        'average_cost': average_cost, 
        'ytd_spend': ytd_spend,
        'current_year': current_year,
        'section': 'purchase',
        'recent_events': recent_events,
        'kpis': kpis,
        
        'purchase_deltas': purchase_deltas,
        
        'paid': paid,
        'payables': payables,

        
        'users': users,
        'active_user': user_filter,

        # payment-method filter
        'payment_methods': payment_methods,
        'active_payment': active_payment,

        # embedded payables panel (pay_-prefixed params — see partial)
        'can_view_payables': can_view_payables,

        }
    context.update(pay_ctx)
    return render(request, 'Expense/purchase_history.html', context)

@login_required(login_url='login')
# @permission_required('staff_view')
@permission_required('read_only') # dev
def purchase_detail(request, business_slug, purchase_id):
    business = get_business_for_user(request.user, business_slug)
    
    purchase = get_object_or_404(Purchase, business=business, id=purchase_id)
    purchase_items = purchase.materials.select_related('material')
    line_count = purchase_items.count()
    payments = purchase.payments.select_related('created_by').order_by('created_at')
    
    subtotal       = sum((i.price * i.quantity for i in purchase_items), Decimal('0'))
    total_discount = sum((i.discount for i in purchase_items), Decimal('0'))
    
    context = {
        'purchase': purchase, 
        'purchase_items': purchase_items, 
        'line_count': line_count,
        'payments': payments,
        'section': 'purchase',
        'subtotal': subtotal,
        'total_discount': total_discount,
        'can_void': can_void_purchase(purchase, request.user),
    }
    return render(request, 'Expense/purchase_detail.html', context)

@login_required(login_url='login')
@permission_required('add')
def add_to_cart(request, business_slug, id):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, business=business, id=id) 
    material_slug = material.slug
    
    material_key = str(material.id) 
    
    if material.quantity >= 1:
        
        if material_key in cart:
            if cart[material_key]['quantity'] < material.quantity:
                cart[material_key]['quantity'] += 1
                # messages.success(request, f"{material.name}'s quantity has increased.")

            else:
                messages.warning(request, f"{material.name} - quantity limit reached.")
                
        else:
            # first time adding to the cart.
            cart[material_key] = {
                'supplier': material.supplier.name if material.supplier else 'No supplier',
                'id': material.id,
                'slug': material_slug,
                'name': material.name,
                'price': float(material.price),
                'quantity': 1,
                'discount': str(0),
            }
            # messages.success(request, f"{material.name} added to purchase.")
    else:
         messages.warning(request, f"{material.name} -  quantity limit reached.")

    # save the session
    request.session['cart'] = cart
    request.session.modified = True
    
    # htmx 
    if request.headers.get('HX-Request') == 'true':
        # compute total for the overview partial
        total = sum(Decimal(str(item['price'])) * item['quantity']
                for item in cart.values()
        )

        resp = render(request, 'core/partials/_cart_response.html', {
            'cart_count': sum(item['quantity'] for item in cart.values()),
            'cart_items': len(cart),
            'messages':   get_messages(request),
            'total': total,
            'cart_url': 'view-cart',
            'icon': 'bi bi-cart3',
            'label': 'Purchase Record',
            'clear_sessions': 'clear-cart',
            'name': 'Materials',
            'total_name': 'cost',
            'type': 'purchase',
        })
        resp['HX-Trigger'] = 'cartChanged'
        return resp

        
    # fallback if htmx didn't work
    """
    Query with parameters, this allows to add items in the
    purchase without resetting the pagination page.
    """
    query_params = {}
    if request.GET.get('page'):
        query_params['page'] = request.GET.get('page')
    if request.GET.get('search'):
        query_params['search'] = request.GET.get('search')
    if request.GET.get('category'):
        query_params['category'] = request.GET.get('category')
    
    url = reverse('material-list', kwargs={'business_slug': business.slug})
    if query_params:
        url += "?" + urlencode(query_params)
    
    # LOGGING: add to cart
    logger.debug(f"Current Session Cart: {request.session.get('cart')}")
        
    # return redirect('material-list')
    """
    request.META['QUERY_STRING'] is the raw query string sent by the browser.
    It is already URL-encoded (same format as urllib.parse.urlencode output),
    so it can be safely appended to redirects to preserve pagination and filters.
    """
    # return redirect(f"{reverse('material-list')}?{request.META.get('QUERY_STRING', '')}")

    return redirect(url)


@login_required(login_url='login')
@permission_required('view') # dev
def view_cart(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    _normalize_cart_discount_mode(request, business)
    cart = prune_stale_cart_lines(request, business, 'cart', Material)

    subtotal = 0
    total_discount = 0
    cart_items = []

    for material_id, data in cart.items():
        material = get_object_or_404(Material, business=business, id=material_id)
        material_slug = material.slug
        str_discount = data.get('discount', 0)
        discount = Decimal(str_discount)
        quantity = data.get('quantity', 1)
        price = data.get('price')
        # computations

        item_total = Decimal(price) * quantity
        item_discount = item_total - discount
        total_discount += discount
        subtotal += item_total
        
        linked_product = material.products.first()
        
        
        cart_items.append({
            'supplier': material.supplier.name if material.supplier else 'No supplier',
            'id': material_id,
            'image': linked_product.image.url if linked_product and linked_product.image else '',
            'slug': material_slug,
            'material': material.name,
            'quantity': quantity,
            'subtotal': subtotal,
            'price': price,
            'item_total': item_total,
            'discount': discount,
            'item_discount': item_discount,
        })
        
    paginator = Paginator(cart_items, 4)
    page = request.GET.get('page')
    page_obj =  paginator.get_page(page)
    
    # How many filler rows to reach a full page
    blank_rows = range(paginator.per_page - len(page_obj.object_list))
    
    # ── Purchase discount MODE (prefill the whole-order % from session) ──
    purchase_discount_percent = Decimal('0')
    if business.enable_purchase_discount:
        purchase_discount_percent = Decimal(request.session.get('purchase_discount_percent', '0') or '0')

    total_after_discount = max(subtotal - total_discount, 0)

    # LOGGING: View Cart 
    logger.debug(f" View Cart Sessions: {request.session.get('cart')}")
    
    context = {
        'total_after_discount': total_after_discount,
        'cart_items': cart_items,
        'page_obj': page_obj,
        'blank_rows': blank_rows,
        'subtotal': subtotal, 
        'total_discount': total_discount, 
        'section': 'material',
        'cart_scope': 'purchase',
        'enable_purchase_discount': business.enable_purchase_discount,
        'purchase_discount_percent': purchase_discount_percent,
        }

    return render(request, 'Expense/view_cart.html', context)


@login_required(login_url='login')
@permission_required('view') # dev
def view_cart_summary(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    _normalize_cart_discount_mode(request, business)
    cart = prune_stale_cart_lines(request, business, 'cart', Material)
    subtotal = 0
    total_discount = 0
    cart_items = []
    
    for material_id, data in cart.items():
        material = get_object_or_404(Material, business=business, id=material_id)
        material_slug = material.slug
        str_discount = data.get('discount', 0)
        discount = Decimal(str_discount)
        quantity = data['quantity']
        price = data.get('price')
        
        # computations
        item_total = Decimal(price) * quantity
        item_discount = item_total - discount
        total_discount += discount
        subtotal += item_total
        
        cart_items.append({
            'supplier': material.supplier.name if material.supplier else 'No supplier',
            'id': material.id,
            'name': material.name,
            'slug': material_slug,
            'price': price,
            'item_total': item_total,
            'quantity': quantity,
            'discount': discount,
            'item_discount': item_discount,
        })
        
    paginator = Paginator(cart_items, 8)
    page = request.GET.get('page')
    page_obj =  paginator.get_page(page)
        
    # save the cart length in session
    request.session['lines'] = len(cart_items)
    request.session.modified = True
    
    # ── Purchase discount MODE (read/carry the whole-order %) ───────────
    raw = request.GET.get('discount_percent')
    if raw is not None and business.enable_purchase_discount:
        try:
            pct = Decimal(raw)
        except ArithmeticError:
            pct = Decimal('0')
        request.session['purchase_discount_percent'] = str(max(Decimal('0'), min(pct, Decimal('100'))))

    order_discount_percent = Decimal('0')
    order_discount_amount  = Decimal('0')
    if business.enable_purchase_discount:
        total_discount = Decimal('0')          # % mode ignores per-item flats
        order_discount_percent = Decimal(request.session.get('purchase_discount_percent', '0') or '0')
        order_discount_amount  = subtotal * order_discount_percent / Decimal('100')

    total_after_discount = max(subtotal - total_discount - order_discount_amount, 0)

    
    # LOGGING: View Cart Summary
    logger.debug(f"View Summary Cart Sessions: {request.session.get('cart')}")
    
    from datetime import timedelta
    today = timezone.localdate()
    max_due_date = today + timedelta(days=30)
    
    context = {
        'subtotal': subtotal,
        'page_obj': page_obj,
        'total_after_discount': total_after_discount, 
        'cart_items': cart_items, 
        'total_discount': total_discount,
        'today_iso': today.isoformat(),               
        'max_due_date_iso': max_due_date.isoformat(),
        'section': 'material',
        'enable_purchase_discount': business.enable_purchase_discount,
        'order_discount_percent': order_discount_percent,
        'order_discount_amount': order_discount_amount,

    }
    return render(request, 'Expense/view_cart_summary.html', context)

@login_required(login_url='login')
@capacity_required('purchase')
@permission_required('update') # dev
def confirm_purchase_summary(request, business_slug):
    lines = request.session.get('lines', 0)
    subtotal = 0
    total_discount = 0

    business = get_business_for_user(request.user, business_slug)
    cart = prune_stale_cart_lines(request, business, 'cart', Material)
    _normalize_cart_discount_mode(request, business)
    # ── Purchase discount MODE — whole-order % carried via session ──────
    percent_mode = business.enable_purchase_discount
    order_discount_percent = Decimal('0')
    if percent_mode:
        try:
            order_discount_percent = Decimal(request.session.get('purchase_discount_percent', '0') or '0')
        except ArithmeticError:
            order_discount_percent = Decimal('0')
        order_discount_percent = max(Decimal('0'), min(order_discount_percent, Decimal('100')))
    
    try: 
        with transaction.atomic():
            # removed paid (status) to 
            purchase = Purchase.objects.create(
                user=business.user, 
                business=business, 
                total_cost=0, 
                created_by=request.user
            )

            for material_id, data in cart.items():
                material = get_object_or_404(Material, business=business, id=material_id)
                str_discount = data.get('discount', 0)
                discount = Decimal(str_discount)
                if percent_mode:
                    discount = Decimal('0')    # % mode: per-item flats ignored (Option A — keeps void un-blend clean)

                quantity = data['quantity']
                price = data.get('price')
                
                # computations
                item_total = Decimal(price) * quantity

                total_discount += discount
                subtotal += item_total
                
                if material.quantity < quantity:
                    messages.warning(request, f"{material.name} - quantity limit reached.")
                
                PurchaseItem.objects.create(
                    purchase=purchase,
                    material=material,
                    discount=discount,
                    quantity=quantity,
                    price=price,
                )
                
                """
                1st purchase
                coke 5 qty
                formula = PHP 5.00 * 5 qty - 5.00(discount) = PHP 20.00
                
                previous quantity = 5 qty
                previous price = PHP 20.00
                
                2nd purchase
                coke 20 qty
                formula = PHP 5.00 * 20 qty = PHP 100.00 - 5.00(discount) = PHP 95.00
                
                total_quantity = 5(previous quantity) + 20 qty = 25 qty

                new stock price = PHP 20(previous price) + PHP 95 = PHP 115 / 25 qty = PHP 4.60
                
                3rd purchase 
                coke 15 qty
                formula PHP 5.00 * 15 qty = PHP 75.00
                
                total_quantity = 25.00(previous quantity) + 15 qty = 40 qty
                
                new_stock_price 115.00(previous price) + PHP 75.00 = PHP 190.00 / 40 qty = PHP 4.75
                """
                
                line_total_cost  = (Decimal(price) * quantity) - discount
                
                if material.is_multi_unit:
                    line_total_cost  = Decimal(price * quantity) - discount
                    quantity = quantity * material.piece_per_unit
                
                
                stock, created = Stock.objects.get_or_create(
                    user=business.user,
                    business=business,
                    material=material,
                    defaults={
                        'quantity': quantity,
                        'price': line_total_cost / quantity,
                        'created_by': request.user,
                    }         
                )
                
                if not created:
                    old_price = stock.price
                    old_quantity = stock.quantity
                    total_quantity = old_quantity + quantity
                    stock.quantity = total_quantity
                    stock.price = ((old_price * old_quantity) + line_total_cost ) / total_quantity
                    stock.save()
                

                product, created = Product.objects.get_or_create(
                    user=business.user,
                    business=business,
                    name=material.name,
                    material=material,
                    defaults={
                        'cost_price': line_total_cost / quantity,
                        'selling_price': 0.00,
                        'prepared_quantity': quantity,
                        'created_by': request.user,
                    }
                )
                
                if not created:
                    previous_qty = product.prepared_quantity
                    previous_price = product.cost_price

                    # Read the margin BEFORE the blend — this is the whole basis for
                    # "did this delivery push it below target?" (see log_margin_drop).
                    margin_status_before = product.margin_status

                    total_quantity = previous_qty + quantity

                    product.prepared_quantity = total_quantity
                    product.cost_price = ((previous_price * previous_qty) + line_total_cost) / total_quantity
                    product.save()

                    # A supplier price rise erodes the margin silently — nobody is on the
                    # product page when it happens. Fires only on a CROSSING (good→low).
                    log_margin_drop(business, request.user, product, margin_status_before)


            # ── Apply discount (flat per-item already in subtotal; % applied here) ──
            if percent_mode:
                order_discount_amount = subtotal * order_discount_percent / Decimal('100')
                total_after_discount  = max(subtotal - order_discount_amount, 0)
                purchase.discount_percent = order_discount_percent
                purchase.discount_amount  = order_discount_amount
            else:
                total_after_discount = max(subtotal - total_discount, 0)
                # discount_percent / discount_amount stay 0 (model defaults)

            # save purchase lines - cart length
            purchase.line_count = lines

            # save the purchase object
            purchase.total_cost = total_after_discount
            purchase.save()

            
            # ── Payment capture ─────────────────────────────────
            payment_status = request.POST.get('payment_status', 'full')
            payment_method = request.POST.get('payment_method', 'cod')
            payment_note = request.POST.get('payment_note', '').strip()
            due_date_str = request.POST.get('due_date', '').strip()

            # Optional due date (future date for Net 15/30)
            if due_date_str:
                try:
                    from datetime import date as date_cls, timedelta
                    parsed_due_date = date_cls.fromisoformat(due_date_str)
                    today = timezone.localdate()
                    max_allowed = today + timedelta(days=30)
                    
                    if parsed_due_date < today:
                        messages.error(request, "Due date can't be set in the past.")
                        # decide: redirect back or just skip the due_date assignment
                        parsed_due_date = None
                    elif parsed_due_date > max_allowed:
                        messages.error(request, f"Due date can't be more than 30 days out (max {max_allowed}).")
                        parsed_due_date = None
                        
                    if parsed_due_date:
                        purchase.due_date = parsed_due_date
                            
                except ValueError:
                    pass  # silently ignore bad date format

            # Determine payment amount
            if payment_status == 'full':
                payment_amount = purchase.total_cost
            elif payment_status == 'partial':
                amount_str = request.POST.get('amount_paid', '0').strip()
                try:
                    payment_amount = Decimal(amount_str)
                except (ValueError, ArithmeticError):
                    payment_amount = Decimal('0')

                if payment_amount <= 0:
                    messages.warning(request, "Partial amount was invalid — recorded as debt instead.")
                    payment_amount = Decimal('0')
                elif payment_amount >= purchase.total_cost:
                    payment_amount = purchase.total_cost
                    messages.info(request, "Amount matched total — recorded as paid in full.")
            else:  # utang
                payment_amount = Decimal('0')

            # Create payment row if amount > 0
            method_display = None
            if payment_amount > 0:
                payment = PurchasePayment.objects.create(
                    purchase=purchase,
                    business=business,
                    amount=payment_amount,
                    method=payment_method,
                    note=payment_note,
                    created_by=request.user,
                )
                method_display = payment.get_method_display()

                paid_desc = f"via {method_display}"
                if payment_status == 'partial':
                    paid_desc += f" (partial) · ₱{purchase.outstanding:.2f} outstanding"

                log_activity(
                    business, request.user, 'purchase.paid',
                    target=payment,
                    description=paid_desc,
                    metadata={
                        'reference': purchase.reference,
                        'amount': f"{payment_amount:.2f}",
                        'method': payment.method,
                        'outstanding': str(purchase.outstanding),
                    },
                )

            # Status + is_paid based on actual outstanding
            if purchase.is_fully_paid:
                status_name = 'paid'
            else:
                status_name = 'pending'

            status, _ = StatusModel.objects.get_or_create(name=status_name)
            purchase.status = status
            purchase.is_paid = purchase.is_fully_paid
            purchase.save(update_fields=['status', 'is_paid', 'due_date'])

            # Activity log
            if purchase.total_cost == 0:
                payment_text = "Free"
            elif payment_status == 'full' and method_display:
                payment_text = f"via {method_display}"
            elif payment_status == 'partial' and method_display:
                payment_text = f"via {method_display} (partial ₱{payment_amount:.2f})"
            else:
                payment_text = "Debt"


            items_text = summarize_items(purchase.materials.all(), prefix='+')
            log_activity(
                business, request.user, 'purchase.recorded',
                target=purchase,
                description=f"{payment_text} · {items_text}",
                metadata={
                    'reference': purchase.reference,
                    'total': f"{purchase.total_cost:.2f}",
                    'line_count': purchase.line_count,
                    'payment_status': payment_status,
                    'payment_method': payment_method if payment_status != 'utang' else None,
                },
            )
            log_audit(
                business, request.user, 'create',
                target=purchase,
                new_values={
                    'total_cost': purchase.total_cost,
                    'line_count': purchase.line_count,
                    'payment_status': payment_status,
                    'payment_method': payment_method if payment_status != 'utang' else None,
                },
            )            
            purchase.is_locked = True
            purchase.save(update_fields=['is_locked'])



    except ValidationError:
        messages.error(request, f"Cannot complete the purchase - Insufficient stock.")
        return redirect('material-list')
        
    # save the purchase ID for ref
    request.session['purchase_id'] = purchase.id
    
    # clear the session
    request.session['cart'] = {}
    request.session.pop('purchase_discount_percent', None)
    request.session.modified = True

    return redirect('view-purchase-summary', business_slug=business.slug, purchase_id=purchase.id)

@login_required(login_url='login')
@permission_required('add')   # blocks dev; staff + owner can record
def add_purchase_payment(request, business_slug, purchase_id):
    business = get_business_for_user(request.user, business_slug)
    purchase = get_object_or_404(Purchase, business=business, id=purchase_id)
    is_hx = request.headers.get('HX-Request')

    # GUARD: only owner/dev or a staffer the owner granted can_handle_payables may
    # settle supplier bills. Blocks direct URL/POST pokes even if the button is hidden.
    if not can_handle_payables(request.user, business):
        return HttpResponseForbidden("You don't have access to payables.")

    if request.method == 'POST':
        amount_str = request.POST.get('amount', '').strip()
        method = request.POST.get('method', 'cod')
        note = request.POST.get('note', '').strip()
        next_param = request.POST.get('next', '')

        def form_error(msg):
            if is_hx:
                return render(request, 'core/partials/_payment_modal.html', {
                    'p_title': purchase.reference, 'p_payer': 'supplier',
                    'p_outstanding': purchase.outstanding,
                    'p_total': purchase.total_cost, 'p_paid': purchase.amount_paid,
                    'p_action': reverse('add-purchase-payment', kwargs={
                        'business_slug': business.slug, 'purchase_id': purchase.id}),
                    'method_choices': PurchasePayment.PAYMENT_METHOD_CHOICES,
                    'error': msg, 'amount_val': amount_str, 'method_val': method,
                    'note_val': note, 'p_next': next_param,
                })
            messages.error(request, msg)
            return redirect('add-purchase-payment', business_slug=business_slug, purchase_id=purchase_id)

        if method in ('credit', 'store_credit'):
            # credit method paused in the payment modal — guard hand-crafted POSTs
            return form_error("Credit is currently unavailable here — choose another method.")

        try:
            amount = Decimal(amount_str)
        except (ValueError, ArithmeticError):
            return form_error("Enter a valid amount.")

        if amount <= 0:
            return form_error("Payment amount must be greater than ₱0.")

        overpay = amount > purchase.outstanding

        with transaction.atomic():
            payment = PurchasePayment.objects.create(
                purchase=purchase,
                business=business,
                amount=amount,
                method=method,
                note=note,
                created_by=request.user,
            )

            # Update status + is_paid based on actual outstanding
            if purchase.is_fully_paid:
                paid_status, _ = StatusModel.objects.get_or_create(name='paid')
                purchase.status = paid_status
            purchase.is_paid = purchase.is_fully_paid
            purchase.save(update_fields=['status', 'is_paid'])

            paid_desc = f"via {payment.get_method_display()}"
            if purchase.outstanding > 0:
                paid_desc += f" (partial) · ₱{purchase.outstanding:.2f} outstanding"

            log_activity(
                business, request.user, 'purchase.paid',
                target=payment,
                description=paid_desc,
                metadata={
                    'reference': purchase.reference,
                    'amount': f"{amount:.2f}",
                    'method': method,
                    'note': note,
                    'outstanding': str(purchase.outstanding),
                },
            )
            log_audit(
                business, request.user, 'payment',
                target=purchase,
                new_values={'amount': amount, 'method': method,
                            'outstanding_after': purchase.outstanding},
                reason=note,
            )

        # ----- HX: Payment Recorded summary as a modal -----
        if is_hx:
            addmore = reverse('add-purchase-payment', kwargs={
                'business_slug': business.slug, 'purchase_id': purchase.id})
            if next_param:
                addmore += f'?next={next_param}'
            return render(request, 'core/partials/_payment_recorded_modal.html', {
                'p_title': purchase.reference,
                'p_payer': 'supplier',
                'p_doc_label': 'Purchase Total',
                'payment': payment,
                'p_total': purchase.total_cost,
                'p_paid': purchase.amount_paid,
                'outstanding': purchase.outstanding,
                'overpay': overpay,
                'p_view_url': reverse('purchase-detail', kwargs={
                    'business_slug': business.slug, 'purchase_id': purchase.id}),
                'p_addmore_action': addmore,
            })

        # ----- non-HX fallback: warning + existing success-page redirect -----
        if overpay:
            messages.warning(
                request,
                f"Payment ₱{amount:.2f} exceeds outstanding ₱{purchase.outstanding + amount:.2f}. "
                f"Outstanding will go negative (supplier credit)."
            )
        messages.success(request, f"Payment of ₱{amount:.2f} recorded.")
        url = reverse('purchase-payment-success', kwargs={
            'business_slug': business_slug,
            'purchase_id': purchase_id,
            'payment_id': payment.id,
        })
        if next_param:
            url += f'?next={next_param}'
        return redirect(url)

    # ----- GET: modal fragment (HX) or full page (fallback) -----
    if is_hx:
        return render(request, 'core/partials/_payment_modal.html', {
            'p_title': purchase.reference,
            'p_payer': 'supplier',
            'p_outstanding': purchase.outstanding,
            'p_total': purchase.total_cost,
            'p_paid': purchase.amount_paid,
            'p_action': reverse('add-purchase-payment', kwargs={
                'business_slug': business.slug, 'purchase_id': purchase.id}),
            'method_choices': PurchasePayment.PAYMENT_METHOD_CHOICES,
        })

    context = {
        'purchase': purchase,
        'outstanding': purchase.outstanding,
        'method_choices': PurchasePayment.PAYMENT_METHOD_CHOICES,
        'next': request.GET.get('next', ''),
        'section': 'purchase',
    }
    return render(request, 'Expense/add_purchase_payment.html', context)



@login_required(login_url='login')
@permission_required('view') # dev
def view_purchase_summary(request, business_slug, purchase_id):
    business = get_business_for_user(request.user, business_slug)
    purchase = get_object_or_404(Purchase, business=business, id=purchase_id)
    purchase_items = purchase.materials.select_related('material')
    
    total_discount = 0
    subtotal = 0
    cart_items = []
    for item in purchase_items:
        item_total = item.price * item.quantity
        quantity = item.quantity
        
        # handling discount items
        discount = item.discount
        item_discount = item_total - discount
        total_discount += discount
        
        subtotal += item_total
        
        cart_items.append({
            'supplier': item.supplier,
            'name': item.name,
            'price': item.price,
            'quantity': quantity,
            'item_total': item_total,
            'discount': discount,
            'item_discount': item_discount,

        })
        
    paginator = Paginator(cart_items, 6)
    page = request.GET.get('page')
    page_obj =  paginator.get_page(page)
    
    # How many filler rows to reach a full page
    blank_rows = range(paginator.per_page - len(page_obj.object_list))
    

    context = {
        'cart_items': cart_items,
        'page_obj': page_obj,
        'blank_rows': blank_rows,
        'subtotal': subtotal, 
        'total_cost': purchase.total_cost, 
        'total_discount': total_discount, 
        'can_void': can_void_purchase(purchase, request.user),
        'purchase': purchase,
        }
    
    return render(request, 'Expense/view_purchase_summary.html', context)

@login_required(login_url='login')
@permission_required('add')
def void_purchase(request, business_slug, purchase_id):
    business = get_business_for_user(request.user, business_slug)
    purchase = get_object_or_404(Purchase, business=business, id=purchase_id)
    is_hx = request.headers.get('HX-Request')

    if not can_void_purchase(purchase, request.user):
        messages.error(request, "This purchase can no longer be voided — use Purchase Returns instead.")
        if is_hx:
            resp = HttpResponse(status=204)
            resp['HX-Redirect'] = reverse('view-purchase-summary', kwargs={'business_slug': business.slug, 'purchase_id': purchase.id})
            return resp
        return redirect('view-purchase-summary', business_slug=business.slug, purchase_id=purchase.id)

    if request.method != 'POST':
        if is_hx:
            return render(request, 'core/partials/_void_modal.html', {
                'v_title': purchase.reference,
                'v_subtitle': f"₱{purchase.total_cost or 0:.2f}",
                'v_note': "Voiding cancels this purchase completely — it stops counting toward expenses and the cash drawer, and the stock it added is pulled back out. This is for mistakes on this shift, not supplier returns.",
                'v_action': reverse('void-purchase', kwargs={'business_slug': business.slug, 'purchase_id': purchase.id}),
                'v_icon': 'bi-receipt-cutoff',
                'reasons': Purchase.VOID_REASON_CHOICES,
            })
        return render(request, 'Expense/void_purchase.html', {
            'purchase': purchase, 'reasons': Purchase.VOID_REASON_CHOICES,
        })

    reason = request.POST.get('void_reason', '').strip()
    action = request.POST.get('action', 'void')

    with transaction.atomic():
        # 1) un-blend this purchase from stock + product (exact reversal)
        for item in purchase.materials.select_related('material'):
            material = item.material
            if not material:
                continue
            cost_in = (Decimal(item.price) * item.quantity) - Decimal(item.discount or 0)
            qty_in  = item.quantity * material.piece_per_unit if material.is_multi_unit else item.quantity

            stock = Stock.objects.filter(business=business, material=material).first()
            if stock:
                new_qty = stock.quantity - qty_in
                if new_qty > 0:
                    new_val = (stock.quantity * stock.price) - cost_in
                    stock.price = max(Decimal('0'), new_val / new_qty)
                stock.quantity = max(0, new_qty)
                stock.save(update_fields=['quantity', 'price'])

            product = Product.objects.filter(business=business, material=material).first()
            if product:
                new_qty = product.prepared_quantity - qty_in
                if new_qty > 0:
                    new_val = (product.prepared_quantity * product.cost_price) - cost_in
                    product.cost_price = max(Decimal('0'), new_val / new_qty)
                product.prepared_quantity = max(0, new_qty)
                product.save(update_fields=['prepared_quantity', 'cost_price'])

        # 2) flag void — total_cost + payments auto-exclude via is_void
        purchase.is_void = True
        purchase.void_reason = reason
        purchase.voided_by = request.user
        purchase.voided_at = timezone.now()
        purchase.save(update_fields=['is_void', 'void_reason', 'voided_by', 'voided_at'])

        log_activity(
            business, request.user, 'purchase.voided',
            target=purchase,
            description=reason or 'Voided',
            metadata={'reference': purchase.reference, 'total': f"{purchase.total_cost or 0:.2f}"},
            important=needs_owner_review(business, request.user),
        )
        log_audit(
            business, request.user, 'void',
            target=purchase,
            old_values={'is_void': False, 'total_cost': purchase.total_cost},
            new_values={'is_void': True},
            reason=reason,
        )


    # 3) re-ring → rebuild the purchase cart
    if action == 'reedit':
        cart = {}
        for item in purchase.materials.select_related('material'):
            material = item.material
            if not material:
                continue
            key = str(material.id)
            if key in cart:
                cart[key]['quantity'] += item.quantity
            else:
                cart[key] = {
                    'supplier': material.supplier.name if material.supplier else 'No supplier',
                    'id': material.id,
                    'slug': material.slug,
                    'name': material.name,
                    'price': float(item.price),
                    'quantity': item.quantity,
                    'discount': str(item.discount),
                }
        request.session['cart'] = cart
        request.session.modified = True
        messages.success(request, f"Purchase {purchase.reference} voided — edit and save again.")
        return redirect('view-cart', business_slug=business.slug)

    messages.success(request, f"Purchase {purchase.reference} has been voided.")
    if request.user.role == 'owner':
        return redirect('dashboard', business_slug=business.slug)
    return redirect('material-list')


@login_required(login_url='login')
@permission_required('add')   # blocks dev only; staff + owner can create
def purchase_return_create(request, business_slug, purchase_id):
    business = get_business_for_user(request.user, business_slug)
    purchase = get_object_or_404(Purchase, business=business, id=purchase_id)
    purchase_items = purchase.materials.select_related('material').all()

    # Same guard as the sales side — the button is hidden, but the URL is still typeable.
    if not purchase.has_returnable_items:
        messages.info(request, f"{purchase.reference} has nothing left to return.")
        return redirect('purchase-detail', business_slug=business_slug, purchase_id=purchase.id)

    if request.method == 'POST':
        reason = request.POST.get('reason', 'other')
        reason_note = request.POST.get('reason_note', '').strip()
        refund_method = request.POST.get('refund_method', 'cash')

        items_to_return = []
        total_refund = Decimal('0')

        for pi in purchase_items:
            qty_str = request.POST.get(f'qty_{pi.id}', '0')
            try:
                qty = int(qty_str)
            except ValueError:
                qty = 0

            if qty <= 0:
                continue

            if qty > pi.returnable_quantity:
                messages.error(request,
                    f"{pi.name}: only {pi.returnable_quantity} returnable "
                    f"(already returned {pi.total_returned_quantity} of {pi.quantity}).")
                return redirect('purchase-return-create',
                                business_slug=business_slug, purchase_id=purchase_id)

            # ★ Price the refund off what we PAID (unit price less whatever discount the
            # PO carried), never off pi.price — see PurchaseItem.effective_unit_price.
            paid_per_unit = pi.effective_unit_price
            unit_refund_str = request.POST.get(f'unit_refund_{pi.id}', str(paid_per_unit))
            try:
                unit_refund = Decimal(unit_refund_str)
            except (ValueError, ArithmeticError):
                unit_refund = paid_per_unit

            items_to_return.append({
                'purchase_item': pi,
                'qty': qty,
                'unit_refund': unit_refund,
            })
            total_refund += unit_refund * qty

        if not items_to_return:
            messages.warning(request, "Pick at least one item to return.")
            return redirect('purchase-return-create',
                            business_slug=business_slug, purchase_id=purchase_id)

        # In purchase_return_create, after collecting items, before atomic block:
        already_refunded = purchase.amount_refunded_cash + purchase.amount_refunded_credit
        max_refund = (purchase.total_cost or Decimal('0')) - already_refunded

        if total_refund > max_refund:
            messages.error(request,
                f"Refund ₱{total_refund:.2f} exceeds remaining refundable ₱{max_refund:.2f}.")
            return redirect('purchase-return-create',
                            business_slug=business_slug, purchase_id=purchase_id)

        # ★ The refund method is COMPUTED, not taken from the form (2026-07-12). Debt
        # first, cash second: whatever we still owe is knocked off before any cash comes
        # back, so the supplier can never "refund" money we never paid them. The posted
        # refund_method is ignored on purpose — see core/utils/returns.split_refund.
        refund_cash, refund_credit = split_refund(purchase.outstanding, total_refund)
        refund_method = refund_method_for(refund_cash, refund_credit)

        with transaction.atomic():
            return_obj = PurchaseReturn.objects.create(
                original_purchase=purchase,
                business=business,
                reason=reason,
                reason_note=reason_note,
                refund_total=total_refund,
                refund_cash=refund_cash,
                refund_credit=refund_credit,
                refund_method=refund_method,
                created_by=request.user,
            )

            for item in items_to_return:
                pi = item['purchase_item']
                PurchaseReturnItem.objects.create(
                    purchase_return=return_obj,
                    original_purchase_item=pi,
                    name=pi.name,
                    quantity=item['qty'],
                    unit_refund=item['unit_refund'],
                )

                # Decrement stock — items go back to supplier
                try:
                    stock = Stock.objects.get(business=business, material=pi.material)
                    stock.quantity = max(0, stock.quantity - item['qty'])
                    stock.save()
                except Stock.DoesNotExist:
                    pass  # No stock entry for this material; skip silently
                
                # Decrement product (mirror the waste flow)
                product = Product.objects.filter(business=business, material=pi.material).first()
                if product:
                    product.prepared_quantity = max(0, product.prepared_quantity - item['qty'])
                    product.save()
                    
            items_text = summarize_items(return_obj.items.all(), prefix='-')    
            log_activity(
                business, request.user, 'purchase.refunded',
                target=return_obj,
                description=f"{items_text}",
                metadata={
                    'reference': return_obj.reference,
                    'total': f"{total_refund:.2f}",
                    'reason': reason,
                    'refund_method': refund_method,
                },
                important=needs_owner_review(business, request.user),
            )
            log_audit(
                business, request.user, 'return',
                target=return_obj.original_purchase,
                new_values={'return_ref': return_obj.reference,
                            'refund_total': total_refund,
                            'refund_method': refund_method},
                reason=reason,
            )

        messages.success(request, f"Return {return_obj.reference} recorded.")
        return redirect('purchase-detail',
                        business_slug=business_slug, purchase_id=purchase_id)

    context = {
        'purchase': purchase,
        'purchase_items': purchase_items,
        'reason_choices': PurchaseReturn.REASON_CHOICES,
        # The method is no longer picked — the form explains the split instead, and it
        # needs the balance to do that.
        'outstanding': purchase.outstanding,
        'section': 'purchase-return',
    }
    return render(request, 'Expense/purchase_return_create.html', context)

@login_required(login_url='login')
@permission_required('add')
def purchase_payment_recorded(request, business_slug, purchase_id, payment_id):
    business = get_business_for_user(request.user, business_slug)
    purchase = get_object_or_404(Purchase, business=business, id=purchase_id)
    payment = get_object_or_404(PurchasePayment, business=business, id=payment_id)

    context = {
        'purchase': purchase,
        'payment': payment,
        'outstanding': purchase.outstanding,
        'section': 'payable',
    }
    return render(request, 'Expense/purchase_payment_recorded.html', context)


@login_required(login_url='login')
@permission_required('add')
def purchase_return_recorded(request, business_slug, return_id):
    business = get_business_for_user(request.user, business_slug)
    return_obj = get_object_or_404(PurchaseReturn, business=business, id=return_id)
    items = return_obj.items.select_related('original_purchase_item').all()

    context = {
        'return_obj': return_obj,
        'items': items,
        'section': 'purchase-return',
    }
    return render(request, 'Expense/purchase_return_recorded.html', context)


@login_required(login_url='login')
@permission_required('staff_view')   # owner-only (blocks staff)
def purchase_return_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    returns = PurchaseReturn.objects.filter(business=business).select_related(
        'original_purchase', 'created_by'
    ).order_by('-date', '-created_at')

    # Only reasons that have at least one return for this business
    used_reason_values = (
        PurchaseReturn.objects
        .filter(business=business)
        .values_list('reason', flat=True)
        .distinct()
        .order_by('reason')
    )
    reason_dict = dict(PurchaseReturn.REASON_CHOICES)
    reason_choices = [
        (v, reason_dict.get(v, v.replace('_', ' ').title()))
        for v in used_reason_values if v
    ]

    form = PurchaseReturnFilterForm(request.GET or None)
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
            returns = returns.filter(date__week=iso_week, date__iso_year=iso_year)
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
    return render(request, 'Expense/purchase_return_list.html', {
        'page_obj': page_obj,
        'form': form,
        'section': 'purchase-return',
        'total_refunded': totals['total_refunded'] or 0,
        'avg_refund': totals['avg_refund'] or 0,
        'total_count': returns.count(),
        'current_year': f"{today.year}-{today.month:02d}",
        'reason_choices': reason_choices,
    })

@login_required(login_url='login')
@permission_required('staff_view')   # owner-only (blocks staff)
def purchase_return_detail(request, business_slug, return_id):
    business = get_business_for_user(request.user, business_slug)
    return_obj = get_object_or_404(PurchaseReturn, business=business, id=return_id)
    items = return_obj.items.select_related('original_purchase_item').all()

    context = {
        'return_obj': return_obj,
        'items': items,
        'section': 'purchase-return',
    }
    return render(request, 'Expense/purchase_return_detail.html', context)

@login_required(login_url='login')
def purchase_payables(request, business_slug):
    business = get_business_for_user(request.user, business_slug)

    # GUARD: owner/dev, or staff the owner granted can_handle_payables.
    # Replaces the old owner-only @permission_required('staff_view') so a granted
    # staffer reaching this page from the navbar isn't bounced. Mirrors sales_receivables.
    if not can_handle_payables(request.user, business):
        messages.error(request, "You don't have access to payables.")
        return redirect('purchase-list', business_slug=business.slug)

    # Unified: same _payables_panel.html partial + same _build_payables_context()
    # helper that feeds the embedded panel in purchase_history — single source of
    # truth, so the table can never drift between the two pages.
    today = timezone.localdate()
    context = {
        'section': 'payable',
        'current_year': f"{today.year}-{today.month:02d}",
    }
    context.update(_build_payables_context(request, business))

    return render(request, 'Expense/purchase_payables.html', context)

@login_required(login_url='login')
def cart_remove_materials(request, business_slug, id):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, business=business, id=id)
    
    material_key = str(material.id)
    
    if material_key in cart:
        del cart[material_key]
        # messages.success(request, f"{material.name} removed from the purchase record.")
         
    request.session.modified = True
    
    page = request.GET.get('page', '')
    url = reverse('view-cart', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)


@login_required(login_url='login')
def edit_total_price(request, business_slug, material_id):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, business=business, id=material_id)
    material_key = str(material.id)

    if cart and material_key in cart:
        data = cart[material_key]
        quantity = data.get('quantity', 0)
        price = data.get('price')
        raw_total = request.POST.get('new_total_price')

        # Guard: need a value AND a usable quantity
        if raw_total is not None and raw_total != '' and quantity > 0:
            new_unit_price = Decimal(raw_total) / quantity

            # Compare against existing unit price (use is not None, not truthiness)
            if price is None or new_unit_price != Decimal(price):
                cart[material_key]['price'] = str(new_unit_price)
                # if new_unit_price == 0:
                #     messages.success(request, f"{material.name} marked as free (₱0.00 unit cost).")
                # else:
                #     messages.success(request, f"{material.name}'s unit cost has been updated.")

    request.session['cart'] = cart
    request.session.modified = True

    page = request.GET.get('page', '')
    url = reverse('view-cart', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)


@login_required(login_url='login')
def cart_edit_material(request, business_slug, id):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})
    material = get_object_or_404(Material, business=business, id=id)

    material_key = str(material.id)
    
    if request.method == 'POST':
        raw_qty = request.POST.get('quantity')
        quantity = int(raw_qty) if raw_qty else 0
        
        if material.quantity >= quantity:
        
            if quantity < 1:
                quantity = 1
            
            cart[material_key]['quantity'] = quantity
            # messages.success(request, f"{material.name}'s quantity has been updated.")
            request.session.modified = True
        else:
             messages.warning(request, f"{material.name} - quantity limit reached.")
    
    page = request.GET.get('page', '')
    url = reverse('view-cart', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)

@login_required(login_url='login')
def cart_discount_material(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    cart = request.session.get('cart', {})

    for material_id, data in cart.items():
        # Discount
        raw_discount = request.POST.get(f"discount_{material_id}")
        discount_input = Decimal(raw_discount) if raw_discount else Decimal('0')
        cart[material_id]['discount'] = str(discount_input)

        # Total price → derive unit price
        raw_total = request.POST.get(f"total_price_{material_id}")
        quantity = data.get('quantity', 0)
        if raw_total is not None and raw_total != '' and quantity > 0:
            new_unit_price = Decimal(raw_total) / quantity
            cart[material_id]['price'] = str(new_unit_price)

    request.session['cart'] = cart
    request.session.modified = True
    # messages.success(request, "Cart updated.")

    page = request.GET.get('page', '')
    url = reverse('view-cart', kwargs={'business_slug': business.slug})
    if page:
        url = f"{url}?page={page}"
    return redirect(url)

@login_required(login_url='login')
@permission_required('read_only') # dev
def waste_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    stocks = get_queryset_for_user(request.user, Stock.objects.all()).filter(business=business).order_by('-created_at')
    wastes = get_queryset_for_user(request.user, Waste.objects.all()).filter(business=business).order_by('-date', '-id')
    
    wastes = filter_to_own_if_staff(request.user, wastes)
    
    total_waste_cost = wastes.aggregate(waste_cost=Sum(F('waste_items__price') * F('waste_items__quantity')))['waste_cost'] or 0
    max_waste = wastes.aggregate(max=Max('total_cost'))['max'] or 0 
    
    form = WasteItemFilterForm(request.GET or None)
    period = request.GET.get('period')
    
    today = timezone.localdate()
    current_year = f"{today.year}-{today.month:02d}"
    
    if form.is_valid():
        search = form.cleaned_data.get('search')
        select_month = form.cleaned_data.get('select_month')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')
        
        if search:
            wastes = wastes.filter(
                Q(waste_items__material__name__iexact=search) |
                Q(total_cost__iexact=search) |
                Q(waste_items__quantity__iexact=search)
            )
        
        if select_month:
            parsed_date = datetime.strptime(select_month, '%Y-%m')
            wastes = wastes.filter(date__month=parsed_date.month)
        
        if start_date and end_date:
            wastes = wastes.filter(date__range=(start_date, end_date))
            
        
        if period == 'last_week':
            last_year = today.year - 1
            last_year_of_last_week = date(last_year, 12, 28).isocalendar()[1]
            
            if today.isocalendar()[1] == 1:
                wastes = wastes.filter(date__week=last_year_of_last_week, date__year=last_year)
            else:
                wastes = wastes.filter(date__week=today.isocalendar()[1]-1, date__year=today.year)
                
        if period == 'month':
            wastes = wastes.filter(date__month=today.month, date__year=today.year)
            
        if period == 'today':
            wastes = wastes.filter(date__day=today.day, date__year=today.year)
        
        total_waste_cost = wastes.total_waste_cost()
        
    recent_events = ActivityEvent.objects.filter(
        verb__startswith='waste.', business=business,
    )
    recent_events = scope_events_for_user(recent_events, request.user)[:4]
        
    pagination = Paginator(wastes, 8)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {
        'page_obj': page_obj, 
        'total_waste_cost': total_waste_cost, 
        'stocks': stocks, 
        'max_waste': max_waste,
        'current_year': current_year,
        'section': 'waste',
        'recent_events': recent_events,
        }
    return render(request, 'Expense/waste_list.html', context)

@login_required(login_url='login')
@permission_required('add') # dev
def waste_product_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    page = 'waste_product' 
    if request.method == 'POST':
        form = ProductWasteForm(request.POST, business=business)

        if form.is_valid():
            item = form.save(commit=False)
            item.user = business.user
            item.business = business
            item.product.prepared_quantity -= item.quantity
            item.save()
            messages.success(request, f"{item.product.name} - has been added to expense.")
            return redirect('expense-waste-list', business_slug=business.slug)         
    else:
        form = ProductWasteForm(business=business)

    context = {'form': form, 'page': page, 'section': 'waste'}
    return render(request, 'Expense/waste_create.html', context)

@login_required(login_url='login')
@capacity_required('waste')
@permission_required('add') # dev
def waste_material_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    page = 'waste_material'
    total_cost = 0

    if request.method == 'POST':
        selected_ids = request.POST.getlist('waste_expense', [])
        reason = request.POST.get('reason')
        
        if not reason:
            messages.warning(request, f"You forgot to select a reason.") 
            return redirect('material-waste-create', business_slug=business.slug)
                
        if not selected_ids:
            messages.warning(request, f"You forgot to check the checkbox.")
            return redirect('material-waste-create', business_slug=business.slug)   
            
        else:
            try:
                with transaction.atomic():
                    waste = Waste.objects.create(
                        user=business.user,
                        business=business,
                        total_cost=0,
                        reason=reason,
                        created_by=request.user,
                    )
                    invalid_items = []
                    stocks = Stock.objects.filter(id__in=selected_ids, business=business)
                    for stock in stocks:
                        price = stock.price
                        raw_quantity = request.POST.get(f"quantity_{stock.id}")
                        quantity = int(raw_quantity)

                        if quantity == 0:
                            continue

                        # deduct from the stock
                        if stock:
                            if stock.quantity >= quantity:
                                stock.quantity -= quantity
                                stock.save()
                            else:
                                invalid_items.append(f"{stock.name} - {stock.quantity} left.")
                                continue

                        # deduct as well for the product
                        product = Product.objects.filter(business=business, material=stock.material).first()
                        if product:
                            if product.prepared_quantity >= quantity:
                                product.prepared_quantity -= quantity
                                product.save()

                        WasteItem.objects.create(
                            waste=waste,
                            material=stock.material,
                            price=price,
                            quantity=quantity,
                        )
                        total_cost += Decimal(price) * quantity

                    waste.total_cost = total_cost
                    waste.save()

                    # If nothing valid was processed, delete the empty waste
                    if total_cost == 0:
                        waste.delete()
                        if invalid_items:
                            messages.error(request, f"All items were invalid: {', '.join(invalid_items)}")
                        else:
                            messages.error(request, "No valid items were processed.")
                        return redirect('expense-waste-list', business_slug=business.slug)

                # ── SUCCESS PATH (outside atomic, still inside try) ──
                items_text = summarize_items(waste.waste_items.all(), prefix='-')
                log_activity(business, request.user, 'waste.recorded',
                    target=waste,
                    description=f"{waste.get_reason_display()} · {items_text}",
                    metadata={'reason': waste.reason, 'total': f"{waste.total_cost:.2f}"})

                if invalid_items:
                    messages.warning(request, f"Waste recorded. Some items were skipped: {', '.join(invalid_items)}")
                else:
                    messages.success(request, "Waste has been created.")
                return redirect('expense-waste-list', business_slug=business.slug)

            except ValidationError:
                messages.warning(request, "Waste record can't be processed.")
                return redirect('expense-waste-list', business_slug=business.slug)

    
    stocks = Stock.objects.filter(business=business)


    pagination = Paginator(stocks, 5)
    page_obj = pagination.get_page(request.GET.get('page'))
    context = {
        'page': page,
        'page_obj': page_obj, 
        'section': 'waste', 
        'stocks': stocks,
    }
    return render(request, 'Expense/waste_create.html', context)

@login_required(login_url='login')
@permission_required('view') # dev
def waste_material_detail(request, business_slug, waste_id):
    business = get_business_for_user(request.user, business_slug)
        
    waste = get_object_or_404(Waste, business=business, id=waste_id)
    waste_items = waste.waste_items.select_related('material')
    
    context = {'waste': waste, 'section': 'waste', 'waste_items': waste_items}
    return render(request, 'Expense/waste_detail.html', context)


@login_required(login_url='login')
@capacity_required('expense')
@permission_required('staff_view')
@permission_required('read_only') # dev
def expense_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    total_amount = 0
    
    if request.method == 'POST':
        selected_ids = request.POST.getlist('misc_expense', [])
        
        if not selected_ids:
            messages.warning(request, f"You forgot to check the checkbox.")
        else:
            date_str = request.POST.get('date')
            # How the bill was paid — defaults to cash; only cash leaves the drawer.
            payment_method = request.POST.get('payment_method', 'cash')
            valid_methods = {code for code, _ in Expense.PAYMENT_METHOD_CHOICES}
            if payment_method not in valid_methods:
                payment_method = 'cash'

            # validate date
            try:
                from datetime import date as date_type
                expense_date = date_type.fromisoformat(date_str)

                expense = Expense.objects.create(
                    total_amount=0,
                    user=business.user,
                    business=business,
                    created_by=request.user,
                    date=timezone.localdate(),
                    payment_method=payment_method,
                )
                
                if expense_date > timezone.localdate():
                    messages.error(request, 'Expense date cannot be in the future.')
                    misc_expense = MiscExpense.objects.filter(business=business)
                    return render(request, 'Expense/misc_and_expense_create.html',{
                        'section': 'expense',
                        'misc_expenses': misc_expense,
                    })
            except (ValueError, TypeError):
                messages.error(request, 'Invalid date. Please select a valid date.')
                misc_expenses = MiscExpense.objects.filter(business=business)
                return render(request, 'Expense/misc_and_expense_create.html', {
                    'section': 'expense',
                    'misc_expenses': misc_expenses,
                })
                
            for misc_id in selected_ids:
                misc = get_object_or_404(MiscExpense, business=business, id=misc_id)
                amount = request.POST.get(f"amount_{misc_id}")
                date = request.POST.get('date')
                total_amount += Decimal(amount)
                
                ExpenseItem.objects.create(
                    expense=expense,
                    misc_expense=misc,
                    name=misc.name,
                    amount=amount,
                )
            
            expense.total_amount = total_amount
            expense.date = date
            expense.save()
                
            messages.success(request, 'Expense has been created.')
            return redirect('expense-list', business_slug=business.slug)
        
    misc_expenses = MiscExpense.objects.filter(business=business)
    
    context = {'section': 'expense', 'misc_expenses': misc_expenses}
    return render(request, 'Expense/misc_and_expense_create.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def expense_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    average_amount_cost = 0
    
    expenses = get_queryset_for_user(request.user, Expense.objects.all()).filter(business=business).order_by('-id')
    # shifts = get_queryset_for_user(request.user, Shift.objects.all()).filter(business=business)
    shifts = Shift.objects.none()
    
    expense_by_dates = expenses.values('date').annotate(total_amount=Sum('total_amount')).order_by('-date')
    shift_by_dates = shifts.values('date').annotate(total_shift=Sum('amount')).order_by('-date')
    
    # Calculate average
    average_expense = expenses.values('date').aggregate(total_expenses=Avg('total_amount'))['total_expenses'] or 0
    average_salary = shifts.values('date').aggregate(total_shift=Avg('amount'))['total_shift'] or 0
    
    if expenses and shifts:
        average_amount_cost = (expenses.aggregate(expense=Sum('total_amount'))['expense'] + shifts.aggregate(shift=Sum('amount'))['shift']) / 2
    
    # Apply filters
    form = ExpenseFilterForm(request.GET or None)
    period = request.GET.get('period')
    today = timezone.localdate()
    
    month = today.month
    year = today.year
    current_month = f"{year}-{month:02d}"   # zero-padded — f"-0{month}" breaks for Oct–Dec ("2026-010")
    
    if form.is_valid():
        select_month = form.cleaned_data.get('select_month')
        start_date = form.cleaned_data.get('start_date')
        end_date = form.cleaned_data.get('end_date')

        if start_date and end_date:
            expenses = expenses.filter(date__range=(start_date, end_date))
            shifts = shifts.filter(date__range=(start_date, end_date))
        
        if select_month:
            parsed_date = datetime.strptime(select_month, '%Y-%m')
            expenses = expenses.filter(date__month=parsed_date.month, date__year=parsed_date.year)
            shifts = shifts.filter(date__month=parsed_date.month, date__year=parsed_date.year)
            
        if period == 'last_month':
            if month == 1:
                last_month = 12
                last_year = year - 1
                expenses = expenses.filter(date__month=last_month, date__year=last_year)
                shifts = shifts.filter(date__month=last_month, date__year=last_year)
            else:
                expenses = expenses.filter(date__month=month-1, date__year=year)
                shifts = shifts.filter(date__month=month-1, date__year=year)
                
        if period == 'month':
            expenses = expenses.filter(date__month=month, date__year=year)
            shifts = shifts.filter(date__month=month, date__year=year)
            
        if period == 'current_year':
            expenses = expenses.filter(date__year=year)
            shifts = shifts.filter(date__year=year)
        
        
        expense_by_dates = expenses.values('date').annotate(total_amount=Avg('total_amount')) 
        shift_by_dates = shifts.values('date').annotate(total_shift=Avg('amount'))
        
    # Build summary dict
    summary = {}
    
    for e in expense_by_dates:
        summary[e['date']] = {
            'total_amount': e['total_amount'],
            'total_shift': 0
        }
    
    for s in shift_by_dates:
        if s['date'] in summary:
            summary[s['date']]['total_shift'] = s['total_shift']
        else:
            summary[s['date']] = {
                'total_shift': s['total_shift'],
                'total_amount': 0,
            }

    # Which payment method(s) each day used, so the list can show one pill per row
    # (a single method's pill, or "Mixed" when a day mixed methods) — same convention
    # as Sale/Purchase.payment_method_code.
    methods_by_date = {}
    for row in expenses.values('date', 'payment_method').distinct():
        methods_by_date.setdefault(row['date'], set()).add(row['payment_method'])

    # How many expense line-items (templates) were logged per day — one DB-side
    # Count grouped by date (no N+1). Feeds the Qty column in the list.
    items_by_date = {
        row['expense__date']: row['n']
        for row in ExpenseItem.objects.filter(expense__in=expenses)
                    .values('expense__date').annotate(n=Count('id'))
    }

    # Convert summary dict to list and sort
    summary_list = []

    grand_total_expense = 0
    grand_total_salary = 0

    for date, value in summary.items():
        total_amount = value['total_amount']
        total_shift = value['total_shift']

        grand_total_expense += total_amount
        grand_total_salary += total_shift

        codes = methods_by_date.get(date)
        if not codes:
            method_code = None
        elif len(codes) == 1:
            method_code = next(iter(codes))
        else:
            method_code = 'mixed'

        summary_list.append({
            'date': date,
            'total_amount': total_amount,
            'total_shift': total_shift,
            'payment_method': method_code,
            'expense_count': items_by_date.get(date, 0),
        })
        
    # Calculate average
    average_expense = expenses.values('date').aggregate(total_expenses=Avg('total_amount'))['total_expenses'] or 0
    average_salary = shifts.values('date').aggregate(total_shift=Avg('amount'))['total_shift'] or 0
    
    sorted_list = sorted(summary_list, key=lambda x: x['date'], reverse=True)
    
    
    
    # Pagination
    pagination = Paginator(sorted_list, 8)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {
        
        'page_obj': page_obj, 
        'section': 'expense', 
        'current_month': current_month,
        'average_expense': average_expense,
        'average_salary': average_salary,
        'grand_total_salary': grand_total_salary,
        'grand_total_expense': grand_total_expense,
        'average_amount_cost': average_amount_cost,
        'form': form,
        
    }
    
    return render(request, 'Expense/expense_list.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def expense_detail(request, business_slug, date):
    business = get_business_for_user(request.user, business_slug)
    
    # Get all expenses and employees for this date
    expense = Expense.objects.filter(business=business, date=date)
    exp_items = ExpenseItem.objects.filter(expense__in=expense).select_related('expense')
    
    # shift = Shift.objects.filter(business=business, date=date)
    # shift_employees = ShiftEmployee.objects.filter(shift__in=shift)
    shift = Shift.objects.none()
    shift_employees = ShiftEmployee.objects.none()

    # Calculate totals
    total_expense_cost = expense.aggregate(total=Sum('total_amount'))['total'] or 0
    total_salary_cost = shift_employees.aggregate(total=Sum('daily_rate'))['total'] or 0
    total_cost = total_expense_cost + total_salary_cost

    # Build expense items — carry each line's payment method (from its parent
    # Expense) so the detail table can show how it was paid.
    expense_items = []
    for exp in exp_items:
        expense_items.append({
            'type': 'expense',
            'name': exp.name,
            'category': exp.category,
            'amount': exp.amount,
            'payment_method': exp.expense.payment_method if exp.expense else 'cash',
        })

    # Day-level "Paid with" breakdown — total this day's expenses by method so the
    # owner can reconcile against the drawer (only cash actually leaves the till).
    method_labels = dict(Expense.PAYMENT_METHOD_CHOICES)
    method_breakdown = []
    for row in (expense.values('payment_method')
                       .annotate(total=Sum('total_amount'))
                       .order_by('-total')):
        code = row['payment_method'] or 'cash'
        method_breakdown.append({
            'code': code,
            'label': method_labels.get(code, code.title()),
            'total': row['total'] or 0,
        })
    
    # Build employee items
    employee_items = []
    for emp in shift_employees:
        employee_items.append({
            'type': 'salary',
            'name': emp.name,
            'daily_rate': emp.daily_rate,
        })
    
    context = {
        'date': date,
        'expense': expense,
        'shift': shift,
        'expense_items': expense_items,
        'employee_items': employee_items,
        'total_expense_cost': total_expense_cost,
        'total_salary_cost': total_salary_cost,
        'total_cost': total_cost,
        'method_breakdown': method_breakdown,
        'expense_count': expense.count(),
        'employee_count': shift_employees.count(),
        'section': 'expense'
    }
    
    return render(request, 'Expense/expense_detail.html', context)

# @login_required(login_url='login')
# @permission_required('owner_only')
# def expense_detail(request, username, date):
#     if request.user.role == 'developer':
#         owner = get_object_or_404(User, username=username)
#     else:
#         business = get_business_for_user(request.user, business_slug)
        

#     expense = Expense.objects.filter(user=owner, date=date)
    
#     expense_items = ExpenseItem.objects.filter(expense__in=expense)
    
#     sale = Sale.objects.filter(user=owner, date=date)
#     sale_employees = SaleEmployee.objects.filter(sale__in=sale)
#     total_salary_cost = sale_employees.aggregate(s=Sum(F('daily_rate')))['s'] or 0
    
#     total_amount = 0
    
#     items = []
    
#     for expense in expense_items:
#         expense_name = expense.name
#         amount = expense.amount
#         total_amount += amount
        
#         items.append({
#             'expense_name': expense_name,
#             'amount': amount,
#         })
    
#     for employee in sale_employees:
#         employee_name = employee.name,
#         daily_rate = employee.daily_rate,
        
#         items.append({
#             'employee_name': employee_name,
#             'daily_rate': daily_rate,
            
#         })
    
#     context = {
#         'total_amount': total_amount,
#         'total_salary_cost': total_salary_cost,
#         'items': items, 
#         'expense': expense, 
#         'section': 'expense'
#         }
    
#     return render(request, 'Expense/expense_detail.html', context)


@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def misc_expense_list(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    misc_expenses = get_queryset_for_user(request.user, MiscExpense.objects.all()).filter(business=business).order_by('-created_at')
    
    pagination = Paginator(misc_expenses, 8)
    page = request.GET.get('page')
    page_obj = pagination.get_page(page)
    
    context = {'page_obj': page_obj, 'section': 'expense'}
    return render(request, 'Expense/misc_expense_list.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def misc_expense_create(request, business_slug):
    business = get_business_for_user(request.user, business_slug)
    if request.method == 'POST':
        form = MiscExpenseForm(request.POST, business=business)
        
        if form.is_valid():
            misc_expense = form.save(commit=False)
            misc_expense.user = business.user
            misc_expense.business = business
            misc_expense.created_by = request.user
            misc_expense.name = misc_expense.name.title()
            misc_expense.save()
            messages.success(request, f"{misc_expense.name} has been added to expense.")
            return redirect('misc-expense-list', business_slug=business.slug)
        
    else:
        form = MiscExpenseForm(business=business)
        
    context = {'form': form, 'section': 'expense'}
    return render(request, 'Expense/misc_and_expense_create.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def misc_expense_detail(request, business_slug, misc_expense_id):
    business = get_business_for_user(request.user, business_slug)
        
    misc_expense = get_object_or_404(MiscExpense, business=business, id=misc_expense_id)
    
    context = {'misc_expense': misc_expense, 'section': 'expense'}
    
    return render(request, 'Expense/misc_expense_detail.html', context)

@login_required(login_url='login')
@permission_required('staff_view')
@permission_required('read_only') # dev
def misc_expense_update(request, business_slug, misc_expense_id):
    business = get_business_for_user(request.user, business_slug)
        
    misc_expense = get_object_or_404(MiscExpense, business=business, id=misc_expense_id)
    
    if request.method == 'POST':
        form = MiscExpenseForm(request.POST, instance=misc_expense, business=business)
        
        if form.is_valid():
            misc_expense = form.save(commit=False)
            misc_expense.name = misc_expense.name.title()
            misc_expense.save()
            messages.success(request, f"{misc_expense.name} has been updated.")
            return redirect('misc-expense-list', business_slug=business.slug)
    
    else:
        form = MiscExpenseForm(instance=misc_expense, business=business)

    # htmx GET → form-in-modal (plain POST inside; invalid POST falls back to the full page)
    if request.method == 'GET' and request.headers.get('HX-Request'):
        return render(request, 'Expense/partials/_misc_expense_form_modal.html', {
            'form': form,
            'misc_expense': misc_expense,
            'cm_action': reverse('misc-expense-update', kwargs={
                'business_slug': business.slug, 'misc_expense_id': misc_expense.id}),
        })

    context = {'form': form, 'section': 'expense', 'misc_expense': misc_expense}
    return render(request, 'Expense/misc_expense_update.html', context)

@login_required(login_url='login')
@permission_required('staff_delete')
@permission_required('read_only') # dev
def misc_expense_delete(request, business_slug, misc_expense_id):
    business = get_business_for_user(request.user, business_slug)
        
    misc_expense = get_object_or_404(MiscExpense, business=business, id=misc_expense_id)
    
    if request.method == 'POST':
        misc_expense.delete()
        messages.success(request, f"{misc_expense.name} has been deleted.")
        if request.headers.get('HX-Request'):
            resp = HttpResponse(status=204)
            resp['HX-Redirect'] = reverse('misc-expense-list', kwargs={'business_slug': business.slug})
            return resp
        return redirect('misc-expense-list', business_slug=business.slug)

    # htmx GET → confirm modal (ExpenseItem.misc_expense is SET_NULL + snapshots,
    # so logged expenses genuinely survive the delete — the note promises that)
    if request.headers.get('HX-Request'):
        return render(request, 'core/partials/_confirm_modal.html', {
            'cm_title': misc_expense.name,
            'cm_subtitle': f"₱{misc_expense.amount:.2f} default · {misc_expense.category.name.title() if misc_expense.category else 'No category'}",
            'cm_note': "This template will be removed from your list · Expenses you already logged with it are <strong>kept</strong>.",
            'cm_action': reverse('misc-expense-delete', kwargs={
                'business_slug': business.slug, 'misc_expense_id': misc_expense.id}),
            'cm_label': "Delete Template",
            'cm_tone': 'danger',
            'cm_btn_icon': 'bi-trash3',
            'cm_icon': 'bi-bookmark-fill',
        })

    context = {'misc_expense': misc_expense, 'section': 'expense'}
    return render(request, 'Expense/misc_expense_delete.html', context)