"""Builders for the object graph every test needs.

A single Sale in this app sits on top of User → Subscription → BusinessProfile →
BusinessPlan → Product → Sale → SaleItem. If every test has to assemble that by hand,
the second test never gets written. Everything here exists so a test can say what it
means in one line and assert on the next.

Signals already do some of the work — creating a User with role='owner' auto-creates a
free Subscription, and creating a BusinessProfile auto-creates a free BusinessPlan plus
its default Category (see user/signals.py). These helpers fetch those rather than
fighting them, so the graph a test gets is the same graph production builds.
"""
from decimal import Decimal
from datetime import timedelta

from django.utils import timezone

from user.models import User, BusinessProfile
from subscription.models import Subscription, BusinessPlan
from Product.models import Product
from Sales.models import Sale, SaleItem, SalesPayment
from Employee.models import Employee, Shift, ShiftEmployee

_seq = 0


def _next(prefix):
    global _seq
    _seq += 1
    return f"{prefix}{_seq}"


def set_created_at(obj, when):
    """Force a created_at, which save() refuses to honour.

    created_at is auto_now_add (core.models.TimeStampModel), so the INSERT always
    stamps 'now' and no amount of assigning the field survives a save(). QuerySet
    .update() compiles straight to SQL and skips that machinery, which is the only
    way a test can place a row inside a particular shift window. _base_manager is
    used because the default manager on some models filters rows out.
    """
    type(obj)._base_manager.filter(pk=obj.pk).update(created_at=when)
    obj.refresh_from_db()
    return obj


def make_owner(*, billing_cycle='monthly', is_founder=False, is_lifetime=False):
    """An owner and their (signal-created) Subscription."""
    user = User.objects.create(username=_next('owner'), role='owner')
    sub = Subscription.objects.get(user=user)
    sub.billing_cycle = billing_cycle
    sub.is_founder = is_founder
    sub.is_lifetime = is_lifetime
    sub.save()
    return user, sub


def make_business(owner, *, plan='free', name=None, months_in=0, term_days=None):
    """A business on `plan`, returned as (BusinessProfile, BusinessPlan).

    months_in  — how long the current billing term has already been running. Drives the
                 refund clock (plan_started_at), so a test can say "they paid 3 months
                 ago" without touching timestamps by hand.
    term_days  — length of the paid term; defaults to 365 on yearly, 30 on monthly.
    """
    biz = BusinessProfile.objects.create(user=owner, business_name=name or _next('Biz '))
    bp = BusinessPlan.objects.get(business=biz)   # signal already made it, on free

    if plan != 'free':
        sub = Subscription.objects.get(user=owner)
        if term_days is None:
            term_days = 365 if sub.billing_cycle == 'yearly' else 30
        started = timezone.now() - timedelta(days=30 * months_in)
        bp.plan = plan
        bp.is_active = True
        bp.plan_started_at = started
        bp.expires_at = started + timedelta(days=term_days)
        bp.save()

        # The signal created a FREE BusinessPlan during BusinessProfile.objects.create()
        # above, which populated biz's reverse one-to-one cache. `bp` is a separately
        # fetched object, so saving it leaves that cache serving a stale 'free' — and
        # anything reading business.plan (plan gates, has_timecards) would silently see
        # the wrong tier. Production never hits this; it re-fetches per request.
        biz.refresh_from_db()
    return biz, bp


def make_product(business, *, selling_price='100', cost_price='60', stock=100, name=None):
    return Product.objects.create(
        user=business.user,
        business=business,
        name=name or _next('Product '),
        selling_price=Decimal(str(selling_price)),
        cost_price=Decimal(str(cost_price)),
        prepared_quantity=stock,
    )


def make_employee(business, *, name=None, daily_rate='500', is_cashier=True,
                  staff_user=None):
    return Employee.objects.create(
        user=business.user,
        business=business,
        staff_user=staff_user,
        name=name or _next('Staff '),
        daily_rate=Decimal(str(daily_rate)),
        is_cashier=is_cashier,
    )


def make_staff(business, **kwargs):
    """A staff member, returned as (User, Employee).

    Staff are two rows: a User (they log in, they're a sale's created_by) and an
    Employee (the seat that carries the timecard and the wage). Anything that asks
    "is this person on shift?" walks user → Employee.staff_user → ShiftEmployee, so a
    test that builds only one of the two gets a staff member the gate cannot see.

    User.owner is what points a staff member back at their boss — get_owner() reads it
    directly rather than going through Employee, so every view guarded by
    get_business_for_user 404s for staff without it.
    """
    user = User.objects.create(
        username=_next('staff'), role='staff', owner=business.user)
    return user, make_employee(business, staff_user=user, **kwargs)


def make_timecard(business, *, clock_in, clock_out=None, employee=None, shift=None):
    """One employee's timecard for today — the drawer the void gate keys on.

    A ShiftEmployee carrying a clock_in is what flips a business into "has shifts
    today"; the clock_out is what seals that drawer. Pass both to model a drawer
    that has been counted and closed, clock_out=None for one still open.

    Pass `shift` to hang a second employee off the same day (an AM/PM handover).
    """
    employee = employee or make_employee(business)
    shift = shift or Shift.objects.create(
        user=business.user,
        business=business,
        date=timezone.localdate(),
        amount=Decimal('0'),
    )
    return ShiftEmployee.objects.create(
        shift=shift,
        employee=employee,
        name=employee.name,
        daily_rate=employee.daily_rate,
        is_cashier=employee.is_cashier,
        clock_in=clock_in,
        clock_out=clock_out,
    )


def make_payment(sale, amount, *, method='cash', at=None):
    """A payment against a sale. `at` places it inside a shift window.

    This is the row expected_cash actually sums, so it — not the sale's own
    timestamp — is what decides whether a void disturbs a counted drawer.
    """
    payment = SalesPayment.objects.create(
        sale=sale,
        business=sale.business,
        amount=Decimal(str(amount)),
        method=method,
        date=sale.date,
    )
    if at is not None:
        set_created_at(payment, at)
    return payment


def make_sale(business, items, *, discount_percent=0, status='completed', date=None,
              rung_at=None, created_by=None):
    """A completed Sale with its lines and correct totals.

    items — [(product, qty), ...] or [(product, qty, unit_price), ...] when the test
            needs a price that differs from the product's sticker.

    rung_at — when the sale was actually rung, for tests that care which shift window
              it landed in. `date` is the books' date; this is the wall-clock stamp.

    created_by — who rang it up, defaulting to the owner. Note the asymmetry this
              mirrors from checkout: `user` is the OWNER on every sale (it's the tenancy
              FK), while `created_by` is the actual ringer. Anything asking "whose sale
              is this?" means created_by.

    Totals are computed the way Sales/views.py checkout computes them: gross is summed
    from the SaleItem sticker prices, then the whole-order discount comes off the gross.
    The discount is stored ONLY on the Sale — never written down onto the lines — which
    is exactly the asymmetry SaleItem.effective_unit_price exists to resolve.
    """
    pct = Decimal(str(discount_percent))
    sale = Sale.objects.create(
        user=business.user,
        business=business,
        created_by=created_by or business.user,
        date=date or timezone.localdate(),
        status=status,
        total_revenue=Decimal('0'),
        total_salary_cost=Decimal('0'),
        line_count=len(items),
    )

    gross = Decimal('0')
    for item in items:
        product, qty = item[0], item[1]
        unit = Decimal(str(item[2])) if len(item) > 2 else product.selling_price
        gross += unit * qty
        SaleItem.objects.create(
            sale=sale,
            product=product,
            name=product.name,
            price_at_sale=unit,
            cost_price=product.cost_price,
            quantity=qty,
        )

    sale.discount_percent = pct
    sale.discount_amount = gross * pct / Decimal('100')
    sale.total_revenue = max(gross - sale.discount_amount, Decimal('0'))
    sale.save()

    if rung_at is not None:
        set_created_at(sale, rung_at)
    return sale
