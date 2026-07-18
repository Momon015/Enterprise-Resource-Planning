"""The void seal — who may erase a transaction, and until when.

A void says "this never happened": revenue drops, the goods go back on the shelf, and
the money comes back out of expected_cash. That is also the exact shape of a cash skim
— pocket the ₱500, void the sale, and the drawer reconciles clean with no shortage to
flag. So a void is legal only while the drawer it touches is still open and uncounted.
Once the cash has been counted, the correction route is a Return, which leaves a trail.

What makes a late void dangerous is that a reconciliation is half stored and half
computed. counted_cash is a stored field, frozen at clock-out. expected_cash is a live
@property (Employee/models.py) that re-sums SalesPayment rows on every read, excluding
voided sales. So voiding a sale out of a shift that was already counted silently
rewrites that shift's variance after the fact — and can manufacture a shortage against
whoever put their name on the count.

THE RULE (2026-07-16), keyed on the drawer rather than the day:
  - rung while a drawer was open      → voidable until that drawer closes
  - rung while no drawer was open     → voidable until midnight
  - the drawer that counted it closed → sealed, for everyone, the owner included
  - and only ever by the person who rang it (staff additionally need their own
    timecard open, so a sale no drawer counted can't be rung-and-voided off-shift)

The two cases marked are holes in the version this replaced, which asked "is ANY
drawer open?" business-wide instead of "is THIS sale's drawer closed?".
"""
from datetime import timedelta

import pytest
from django.utils import timezone

from Sales.views import can_void_sale
from tests.factories import (make_business, make_payment, make_sale, make_staff,
                             make_timecard)


@pytest.fixture
def business(owner):
    """Overrides the shared fixture, which is on Free.

    PLAN_LIMITS gives free max_staff: 0 and timecards: False — no staff, no clocking in.
    Every drawer in this file would be a state production can't reach. Standard is the
    cheapest plan that has either. `product` inherits this override for free.
    """
    biz, _plan = make_business(owner, plan='standard')
    return biz


def ago(hours):
    """A moment `hours` before now.

    Every window here is relative to now rather than pinned to a wall-clock hour, so
    nothing goes flaky when the suite runs at 00:30 and "9am today" would be in the
    future. The gate compares datetimes to datetimes, so relative offsets exercise it
    identically.
    """
    return timezone.now() - timedelta(hours=hours)


# ── The drawer, open and closed ──────────────────────────────────────────────

def test_void_is_open_while_the_drawer_is_still_open(business, owner, product):
    """The mistake-correction window: rang it wrong, fix it before the count."""
    make_timecard(business, clock_in=ago(4), clock_out=None)
    sale = make_sale(business, [(product, 2)], rung_at=ago(2))

    assert can_void_sale(sale, owner) is True


def test_closing_the_drawer_seals_the_sales_it_counted(business, owner, product):
    """The anti-skim seal, and it binds the owner too — this is their own sale."""
    make_timecard(business, clock_in=ago(8), clock_out=ago(4))
    sale = make_sale(business, [(product, 2)], rung_at=ago(6))

    assert can_void_sale(sale, owner) is False


def test_a_day_with_no_timecards_stays_voidable_until_midnight(business, owner, product):
    """An owner working alone never clocks in, so no drawer is ever counted.

    Nothing to corrupt, so the only limit is the day-freeze. This is the solo-owner
    case the drawer-wide rule used to break.
    """
    sale = make_sale(business, [(product, 2)], rung_at=ago(6))

    assert can_void_sale(sale, owner) is True


def test_yesterdays_sale_is_never_voidable(business, owner, product):
    """Midnight closes the books. Past days are corrected by Return, never erased."""
    yesterday = timezone.localdate() - timedelta(days=1)
    sale = make_sale(business, [(product, 2)], date=yesterday)

    assert can_void_sale(sale, owner) is False


# ── The holes the per-drawer rule exists to close ──────────────────────────

def test_a_sale_rung_after_every_drawer_closed_is_still_voidable(business, owner, product):
    """Clock out at 3pm, ring a sale at 6pm — that sale is in no drawer at all.

    No shift counted it, so voiding it cannot disturb any reconciliation. The old gate
    blocked it anyway: it saw "a shift existed today", found none still open, and shut
    the window on the whole business for the rest of the day. This is the case that bit
    us in testing — an owner alone in the shop, unable to fix a sale nobody counted.
    """
    make_timecard(business, clock_in=ago(8), clock_out=ago(4))
    sale = make_sale(business, [(product, 2)], rung_at=ago(1))

    assert can_void_sale(sale, owner) is True


def test_an_open_pm_drawer_does_not_unseal_the_counted_am_drawer(business, owner, product):
    """The handover hole, and why this change is a tightening, not a loosening.

    ShiftEmployee rows are per-employee, but the old gate read them business-wide. With
    an AM staffer clocked 9–1 (drawer counted, handed over, signed for) and a PM staffer
    clocked in until close, "is any drawer open?" stayed True all evening — so the AM
    staffer's 10am sales were still voidable hours after their drawer was blind-recounted.

    That is precisely the skim the seal exists to stop, made worse by someone else's
    name being on the count.
    """
    am = make_timecard(business, clock_in=ago(8), clock_out=ago(4))
    make_timecard(business, clock_in=ago(4), clock_out=None, shift=am.shift)
    sale = make_sale(business, [(product, 2)], rung_at=ago(6))   # rung on AM's watch

    assert can_void_sale(sale, owner) is False


def test_a_payment_counted_into_a_closed_drawer_seals_the_sale(business, owner, product):
    """The seal follows the money, not just the ring.

    An utang rung before anyone clocked in, then collected once the shift was underway:
    the ring lands outside every window while the cash lands inside a drawer that has
    since been counted. Sealing on ring-time alone would call this voidable and let the
    void pull ₱200 back out of a closed shift's expected_cash.
    """
    make_timecard(business, clock_in=ago(8), clock_out=ago(4))
    sale = make_sale(business, [(product, 2)], rung_at=ago(9))   # before clock-in
    make_payment(sale, '200', at=ago(6))                         # counted by the drawer

    assert can_void_sale(sale, owner) is False


# ── Whose sale is it ─────────────────────────────────────────────────────────

def test_the_owner_cannot_void_a_sale_they_did_not_ring(business, owner, product):
    """Ownership is created_by — and this is the trap the rule nearly fell into.

    Sale.user is the OWNER on every row; it's the tenancy FK, set to business.user by
    checkout regardless of who is standing at the till. Only created_by records the
    ringer. Keying "my own transactions" on .user would have matched every sale in the
    business for the owner and none at all for staff — a restriction that silently does
    nothing, which is the worst kind.
    """
    staff_user, employee = make_staff(business)
    make_timecard(business, employee=employee, clock_in=ago(4), clock_out=None)
    sale = make_sale(business, [(product, 2)], rung_at=ago(2), created_by=staff_user)

    assert sale.user == owner              # tenancy, not authorship...
    assert sale.created_by == staff_user   # ...this is who rang it
    assert can_void_sale(sale, owner) is False


def test_staff_can_void_their_own_sale_while_they_are_clocked_in(business, product):
    """Staff fix their own mistakes on their own shift. That's the whole window."""
    staff_user, employee = make_staff(business)
    make_timecard(business, employee=employee, clock_in=ago(4), clock_out=None)
    sale = make_sale(business, [(product, 2)], rung_at=ago(2), created_by=staff_user)

    assert can_void_sale(sale, staff_user) is True


def test_staff_cannot_void_their_own_sale_once_their_shift_closed(business, product):
    """Clock out is the seal: the drawer has been counted, so it's a Return now."""
    staff_user, employee = make_staff(business)
    make_timecard(business, employee=employee, clock_in=ago(8), clock_out=ago(4))
    sale = make_sale(business, [(product, 2)], rung_at=ago(6), created_by=staff_user)

    assert can_void_sale(sale, staff_user) is False


def test_staff_cannot_void_a_sale_that_no_drawer_ever_counted(business, product):
    """The ring-without-clocking-in skim, which the drawer seal alone would miss.

    Staff who never clock in have no drawer counting them, so nothing seals their sales
    and the seal would happily allow a void: ring ₱500 cash, pocket it, void it, no
    variance anywhere because no shift ever claimed the money. Requiring their own
    timecard to be open is what closes it.
    """
    staff_user, _employee = make_staff(business)      # never clocks in
    sale = make_sale(business, [(product, 2)], rung_at=ago(2), created_by=staff_user)

    assert can_void_sale(sale, staff_user) is False


# ── The non-shift halves of the gate ─────────────────────────────────────────

def test_an_already_voided_sale_cannot_be_voided_twice(business, owner, product):
    """A second void would restock the goods again and invent inventory."""
    make_timecard(business, clock_in=ago(4), clock_out=None)
    sale = make_sale(business, [(product, 2)], rung_at=ago(2))
    sale.is_void = True
    sale.save(update_fields=['is_void'])

    assert can_void_sale(sale, owner) is False


def test_a_draft_sale_was_never_posted_so_there_is_nothing_to_void(business, owner, product):
    """Drafts touch no stock, no revenue and no drawer. Cancel them, don't void them."""
    make_timecard(business, clock_in=ago(4), clock_out=None)
    sale = make_sale(business, [(product, 2)], status='pending', rung_at=ago(2))

    assert can_void_sale(sale, owner) is False
