"""Shift.amount IS the payroll — and stays equal to the rates that make it up.

Shift.amount is a CACHE of Σ ShiftEmployee.daily_rate. It is read by nine places
(Dashboard ×3, Daily Summary ×3, Expense + Profit Analytics ×3), so if it drifts, every
money surface in the app is wrong at once and none of them disagree loudly enough to
notice — they'd all be quietly, consistently wrong.

This exact field already shipped broken: clock-in created the Shift with amount=0 and
nothing ever went back, so every row read ₱0 while the real payroll was ₱2,000. Readers
survived only because they summed the relation directly. Migration 0013 backfilled the
column and the readers now trust it, which means the signal in Employee/signals.py is
load-bearing.

These tests are the point. `margin_low` was a signal silently deleted by an unrelated
rework, and nothing failed, because nothing held it down. If you are here because a test
broke after removing the signal — the signal is the thing that was supposed to stay.
"""
from decimal import Decimal

import pytest

from Employee.models import Shift, ShiftEmployee
from tests.factories import make_business, make_employee, make_timecard


@pytest.fixture
def business(owner):
    biz, _plan = make_business(owner, plan='standard')
    return biz


def test_clocking_in_puts_the_rate_on_the_shift(business):
    """The money lands when they ARRIVE — clock_out is not a condition.

    A daily rate is earned by showing up. If payroll only counted on clock-out, today's
    figure would climb all day and only settle after the last person left, and anyone
    who forgot to time out would be paid ₱0 until an owner closed them.
    """
    make_timecard(business, clock_in='2026-07-17 08:00Z',
                  employee=make_employee(business, daily_rate='400'))

    shift = Shift.objects.get(business=business)
    assert shift.amount == Decimal('400')


def test_a_second_staff_member_adds_to_the_same_shift(business):
    """One Shift per business per day — the AM/PM handover shape."""
    first = make_timecard(business, clock_in='2026-07-17 08:00Z',
                          employee=make_employee(business, daily_rate='400'))
    make_timecard(business, clock_in='2026-07-17 14:00Z', shift=first.shift,
                  employee=make_employee(business, daily_rate='350'))

    first.shift.refresh_from_db()
    assert first.shift.amount == Decimal('750')


def test_clocking_out_does_not_change_the_payroll(business):
    """Going home doesn't alter what the day cost."""
    card = make_timecard(business, clock_in='2026-07-17 08:00Z',
                         employee=make_employee(business, daily_rate='400'))

    card.clock_out = '2026-07-17 17:00Z'
    card.save()

    card.shift.refresh_from_db()
    assert card.shift.amount == Decimal('400')


def test_removing_a_staff_member_takes_their_rate_back_off(business):
    """post_delete matters as much as post_save — an increment-only cache would strand
    the rate of someone who was removed from the shift."""
    first = make_timecard(business, clock_in='2026-07-17 08:00Z',
                          employee=make_employee(business, daily_rate='400'))
    second = make_timecard(business, clock_in='2026-07-17 14:00Z', shift=first.shift,
                           employee=make_employee(business, daily_rate='350'))

    second.delete()

    first.shift.refresh_from_db()
    assert first.shift.amount == Decimal('400')


def test_recompute_is_idempotent(business):
    """Recompute-don't-increment, stated as a test: running it again can't inflate.

    An increment is only correct if every past write was correct and none is ever
    replayed. This must land on the same number from any path, any number of times.
    """
    card = make_timecard(business, clock_in='2026-07-17 08:00Z',
                         employee=make_employee(business, daily_rate='400'))
    shift = card.shift

    for _ in range(3):
        shift.recompute_amount()

    shift.refresh_from_db()
    assert shift.amount == Decimal('400')


def test_stored_amount_equals_the_sum_of_the_rates(business):
    """THE invariant the nine readers depend on: the cache == its source.

    Asserted against the relation the column replaced, so this fails if the two ever
    part company — which is the only way the payroll on every page goes wrong at once.
    """
    first = make_timecard(business, clock_in='2026-07-17 08:00Z',
                          employee=make_employee(business, daily_rate='400'))
    make_timecard(business, clock_in='2026-07-17 14:00Z', shift=first.shift,
                  employee=make_employee(business, daily_rate='350'))

    from django.db.models import Sum
    for shift in Shift.objects.filter(business=business):
        live = ShiftEmployee.objects.filter(shift=shift).aggregate(
            t=Sum('daily_rate'))['t'] or Decimal('0')
        assert shift.amount == live
