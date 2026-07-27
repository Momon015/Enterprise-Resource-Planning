"""Auto clock-out of shifts a staff member forgot to close.

A forgotten clock-out doesn't cost payroll (the daily rate is earned at clock-in), but it
leaves the shift `is_active` forever — blocking the next day's clock-in and letting
expected_cash absorb every later sale. `close_stale_shifts` closes such shifts at the
business's closing time (or 24h after clock-in for a 24-hour business) and flags them for
the same acknowledge/dispute review an owner-close gets.

The cutoff rule and the sweep both live in Employee.utils so the nightly command and the
lazy sweep on clock-in/timecard load agree — these tests pin that rule.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from activity.models import ActivityEvent
from Employee.models import DrawerSession, Shift, ShiftEmployee
from Employee.utils import (
    close_stale_shifts,
    pending_acks_for_staff,
    shift_auto_close_cutoff,
)
from tests.factories import make_business, make_employee, make_staff, make_timecard


@pytest.fixture
def business(owner):
    biz, _plan = make_business(owner, plan='standard')
    return biz


# ── The cutoff rule ────────────────────────────────────────────

def test_cutoff_is_24h_after_clock_in_when_no_closing_time(business):
    """NULL closing_time = open 24 hours → cap a forgotten shift at 24h, not end-of-day."""
    business.closing_time = None
    ci = timezone.now() - timedelta(hours=5)
    card = make_timecard(business, clock_in=ci)

    assert shift_auto_close_cutoff(card, business) == ci + timedelta(hours=24)


def test_cutoff_is_the_closing_time_on_the_clock_in_day(business):
    """closing_time set → close at that wall-clock time on the day they clocked in."""
    business.closing_time = time(21, 0)
    ci = timezone.make_aware(datetime(2026, 7, 20, 6, 0))
    card = make_timecard(business, clock_in=ci)

    expected = timezone.make_aware(datetime(2026, 7, 20, 21, 0))
    assert shift_auto_close_cutoff(card, business) == expected


def test_closing_time_after_midnight_rolls_to_the_next_day(business):
    """A 2 AM close is TOMORROW relative to a 6 PM clock-in — roll forward so a late-night
    business gets the correct same-session cutoff instead of one before they clocked in."""
    business.closing_time = time(2, 0)
    ci = timezone.make_aware(datetime(2026, 7, 20, 18, 0))
    card = make_timecard(business, clock_in=ci)

    expected = timezone.make_aware(datetime(2026, 7, 21, 2, 0))
    assert shift_auto_close_cutoff(card, business) == expected


# ── The sweep ──────────────────────────────────────────────────

def test_open_shift_past_its_cutoff_is_auto_closed(business):
    business.closing_time = None
    business.save()
    ci = timezone.now() - timedelta(hours=30)   # past the 24h cap
    card = make_timecard(business, clock_in=ci)

    closed = close_stale_shifts(business)

    assert closed == 1
    card.refresh_from_db()
    assert card.clock_out == ci + timedelta(hours=24)
    assert card.auto_closed is True
    assert card.auto_closed_at is not None
    assert card.is_active is False


def test_shift_still_within_its_window_is_left_open(business):
    business.closing_time = None
    business.save()
    card = make_timecard(business, clock_in=timezone.now() - timedelta(hours=3))

    closed = close_stale_shifts(business)

    assert closed == 0
    card.refresh_from_db()
    assert card.clock_out is None
    assert card.auto_closed is False


def test_sweep_is_idempotent(business):
    """Only ever touches clock_out=NULL rows, so a second run is a no-op."""
    business.closing_time = None
    business.save()
    card = make_timecard(business, clock_in=timezone.now() - timedelta(hours=30))

    assert close_stale_shifts(business) == 1
    card.refresh_from_db()
    first_close = card.clock_out

    assert close_stale_shifts(business) == 0
    card.refresh_from_db()
    assert card.clock_out == first_close


def test_auto_close_does_not_change_payroll(business):
    """The daily rate is earned at clock-in; closing the shift must not move Shift.amount."""
    business.closing_time = None
    business.save()
    card = make_timecard(business, clock_in=timezone.now() - timedelta(hours=30),
                         employee=make_employee(business, daily_rate='400'))
    assert card.shift.amount == Decimal('400')

    close_stale_shifts(business)

    card.shift.refresh_from_db()
    assert card.shift.amount == Decimal('400')


# ── Cashier vs non-cashier ─────────────────────────────────────

def test_cashier_auto_close_leaves_the_count_unset_and_needs_review(business):
    """We never fabricate a count nobody made — counted_* stay NULL (expected_cash shows
    as 'what should be there, unverified') and the shift needs the staff's acknowledgement."""
    business.closing_time = None
    business.save()
    card = make_timecard(business, clock_in=timezone.now() - timedelta(hours=30),
                         employee=make_employee(business, is_cashier=True, daily_rate='400'))

    close_stale_shifts(business)

    card.refresh_from_db()
    assert card.is_cashier is True
    assert card.counted_cash is None
    assert card.close_needs_ack is True


def test_non_cashier_auto_close_still_needs_ack_but_has_no_drawer(business):
    business.closing_time = None
    business.save()
    card = make_timecard(business, clock_in=timezone.now() - timedelta(hours=30),
                         employee=make_employee(business, is_cashier=False, daily_rate='400'))

    close_stale_shifts(business)

    card.refresh_from_db()
    assert card.is_cashier is False
    assert card.auto_closed is True
    assert card.close_needs_ack is True


# ── Owner notification (bell event) ────────────────────────────

def test_cashier_auto_close_notifies_the_owner(business):
    """A cashier's uncounted drawer is the owner's problem — fire a bell event that links
    to the shift. Expected cash rides in metadata (not the shared bell text)."""
    business.closing_time = None
    business.save()
    card = make_timecard(business, clock_in=timezone.now() - timedelta(hours=30),
                         employee=make_employee(business, is_cashier=True, daily_rate='400'))

    close_stale_shifts(business)

    event = ActivityEvent.objects.get(business=business, verb='shift.auto_closed')
    assert event.actor is None           # system, not a real user
    assert event.is_important is True
    assert event.target_id == card.id
    assert 'expected_cash' in event.metadata


def test_non_cashier_auto_close_does_not_notify_the_owner(business):
    """Attendance-only shift = no drawer, nothing to reconcile → no owner event."""
    business.closing_time = None
    business.save()
    make_timecard(business, clock_in=timezone.now() - timedelta(hours=30),
                  employee=make_employee(business, is_cashier=False, daily_rate='400'))

    close_stale_shifts(business)

    assert not ActivityEvent.objects.filter(business=business, verb='shift.auto_closed').exists()


def test_no_owner_event_when_cash_reconciliation_is_off(business):
    """Reconciliation off = owner is always the cashier, no drawer to check → no event."""
    business.closing_time = None
    business.enable_cash_reconciliation = False
    business.save()
    make_timecard(business, clock_in=timezone.now() - timedelta(hours=30),
                  employee=make_employee(business, is_cashier=True, daily_rate='400'))

    close_stale_shifts(business)

    assert not ActivityEvent.objects.filter(business=business, verb='shift.auto_closed').exists()


# ── Shared drawer + staff banner ───────────────────────────────

def test_auto_close_releases_a_shared_drawer_it_was_holding(business):
    business.closing_time = None
    business.save()
    card = make_timecard(business, clock_in=timezone.now() - timedelta(hours=30),
                         employee=make_employee(business, is_cashier=True))
    session = DrawerSession.objects.create(
        business=business, date=timezone.localdate(),
        opening_cash=Decimal('500'), status='open',
    )
    card.drawer_session = session
    card.save()
    session.current_holder = card
    session.save(update_fields=['current_holder'])

    close_stale_shifts(business)

    session.refresh_from_db()
    assert session.status == 'closed'
    assert session.closed_at is not None


def test_auto_closed_shift_surfaces_in_the_staff_pending_banner(business):
    """The 'shift to confirm' banner is driven by pending_acks_for_staff — an auto-close
    (closed_by NULL) must show up there just like an owner-close."""
    business.closing_time = None
    business.save()
    staff_user, employee = make_staff(business, is_cashier=True)
    card = make_timecard(business, clock_in=timezone.now() - timedelta(hours=30),
                         employee=employee)

    close_stale_shifts(business)

    acks = pending_acks_for_staff(staff_user, business)
    assert acks['has_any'] is True
    assert card in acks['closes']


# ── The management command ─────────────────────────────────────

def test_management_command_closes_stale_shifts(business):
    business.closing_time = None
    business.save()
    card = make_timecard(business, clock_in=timezone.now() - timedelta(hours=30))

    call_command('close_stale_shifts')

    card.refresh_from_db()
    assert card.auto_closed is True
    assert card.clock_out is not None
