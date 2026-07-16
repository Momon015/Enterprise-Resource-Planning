"""Staff must be on shift to ring a sale.

The invariant: every peso a staff member takes belongs to exactly one drawer that
somebody counts. expected_cash only sums payments between clock_in and clock_out, so a
staff sale rung with nobody clocked in is claimed by NO drawer — the cash sits in the
till while being invisible to every reconciliation, and tomorrow's opening_cash is
snapshotted from business settings rather than the real till, so the gap never surfaces.

It also keeps the void rule total. Staff may only void their own work while their own
shift is open, and the owner may only void their own — so a sale rung by staff off-shift
would have no corrector at all. Requiring the clock-in is what stops that record from
existing in the first place.

This is deliberately a speed bump and not a wall: the checkout guard leaves the cart
alone and sends them to clock in, so it costs a tap rather than the customer.
"""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from Employee.utils import must_clock_in_to_sell
from Sales.models import Sale
from tests.factories import make_business, make_staff, make_timecard


@pytest.fixture
def business(owner):
    """Standard — free has max_staff: 0, so staff on it can't exist. See test_plan_limits."""
    biz, _plan = make_business(owner, plan='standard')
    return biz


def ago(hours):
    return timezone.now() - timedelta(hours=hours)


def test_staff_must_clock_in_before_they_can_sell(business):
    staff_user, _employee = make_staff(business)

    assert must_clock_in_to_sell(business, staff_user) is True


def test_staff_on_an_open_shift_can_sell(business):
    staff_user, employee = make_staff(business)
    make_timecard(business, employee=employee, clock_in=ago(2), clock_out=None)

    assert must_clock_in_to_sell(business, staff_user) is False


def test_staff_who_already_clocked_out_must_clock_back_in(business):
    """Their drawer is counted and closed — a further sale has nowhere to land."""
    staff_user, employee = make_staff(business)
    make_timecard(business, employee=employee, clock_in=ago(8), clock_out=ago(4))

    assert must_clock_in_to_sell(business, staff_user) is True


def test_the_owner_never_has_to_clock_in(business, owner):
    """An owner has no seat and no timecard. A solo owner IS the drawer."""
    assert must_clock_in_to_sell(business, owner) is False


def test_a_plan_without_timecards_cannot_require_clocking_in(owner):
    """The guard against PLAN_LIMITS drifting.

    Requiring a clock-in where clocking in doesn't exist would lock staff out of selling
    entirely. Production can't reach this today (free has zero seats), which is exactly
    why the rule shouldn't depend on remembering that — see test_plan_limits.
    """
    free_business, _plan = make_business(owner, plan='free')
    staff_user, _employee = make_staff(free_business)

    assert must_clock_in_to_sell(free_business, staff_user) is False


def test_checkout_turns_clocked_out_staff_away_without_creating_a_sale(client, business):
    """The guard belongs at checkout, because that is where the Sale row is born.

    Defining the rule isn't enough — this drives the real view to prove it's wired in
    ahead of the write, and that being turned away costs the cart nothing.
    """
    staff_user, _employee = make_staff(business)
    client.force_login(staff_user)

    response = client.post(
        reverse('sale-confirm-summary', kwargs={'business_slug': business.slug}))

    assert response.status_code == 302
    assert response.url == reverse('shift-dashboard',
                                   kwargs={'business_slug': business.slug})
    assert Sale.objects.count() == 0
