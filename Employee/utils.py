from django.db.models import Q
from django.utils import timezone
from .models import ShiftEmployee, OpeningCashOverride


def get_opening_cash_for_today(business):
    """Resolve today's opening cash: override if set, else business default.
    Returns dict with amount + source ('override' or 'default') + note."""
    today = timezone.localdate()
    override = OpeningCashOverride.objects.filter(business=business, date=today).first()
    if override:
        return {
            'amount': override.amount,
            'source': 'override',
            'note': override.note,
            'override': override,
        }
    return {
        'amount': business.default_opening_cash,
        'source': 'default',
        'note': '',
        'override': None,
    }


def is_opening_cash_locked(business):
    """Locked while any staff has an active shift today."""
    today = timezone.localdate()
    return ShiftEmployee.objects.filter(
        shift__business=business,
        shift__date=today,
        clock_in__isnull=False,
        clock_out__isnull=True,
    ).exists()

def pending_acks_for_staff(user, business):
    """Returns pending opening-cash changes + cash payouts + owner-closed shifts.
    Used by the context processor to drive the banner."""
    from .models import OpeningCashChange, CashPayout

    if user.role != 'staff':
        return {'opening_changes': [], 'payouts': [], 'closes': [], 'has_any': False}

    changes = OpeningCashChange.objects.filter(
        shift__shift__business=business,
        shift__employee__staff_user=user,
        acknowledged=False,
        shift__clock_in__isnull=False,
        shift__clock_out__isnull=True,     # still on shift
    ).select_related('shift', 'changed_by')

    payouts = CashPayout.objects.filter(
        shift__shift__business=business,
        shift__employee__staff_user=user,
        acknowledged=False,
        shift__clock_in__isnull=False,
        shift__clock_out__isnull=True,
    ).exclude(purpose='business_expense').select_related('shift', 'created_by')

    # Owner-closed shifts the staff hasn't confirmed — PERSISTS (no expiry filter):
    # created AT closure, so it must survive past time-out until the staff reviews it.
    closes = ShiftEmployee.objects.filter(
        employee__staff_user=user,
        shift__business=business,
        closed_by__isnull=False,
        close_acknowledged=False,
    ).select_related('shift', 'closed_by')

    return {
        'opening_changes': list(changes),
        'payouts':         list(payouts),
        'closes':          list(closes),
        'has_any':         changes.exists() or payouts.exists() or closes.exists(),
    }

def timecards_enabled(business):
    """Whether this business's plan has clock in/out at all (Standard and up).

    Load-bearing for both shift rules below: each asks staff to be on shift, which is
    impossible to satisfy on a plan where clocking in doesn't exist — staff could then
    never void their own work, and (worse) never sell. Today that can't arise, because
    PLAN_LIMITS gives free max_staff:0 and every plan with seats has timecards. But those
    two settings sit far apart in one config dict with nothing tying them together, so
    neither rule leans on the coincidence. The coupling itself is pinned by
    subscription/tests/test_plan_limits.py.
    """
    plan = getattr(business, 'plan', None)
    return bool(plan and plan.has_timecards())


def must_clock_in_to_sell(business, user):
    """Whether this user has to clock in before they can ring up a sale.

    Staff only. An owner has no seat and never clocks in — a solo owner IS the business,
    so there's no drawer for them to be absent from.

    WHY selling off-shift is blocked at all:
      - Orphan cash. expected_cash only sums payments between clock_in and clock_out, so
        a staff sale rung with nobody clocked in is claimed by NO drawer: the cash is
        physically in the till but invisible to every reconciliation, and tomorrow's
        opening_cash is snapshotted from business settings rather than the real till, so
        it never surfaces.
      - Payroll comes off the timecard, so selling off-shift is working for free.
      - It would strand sales nobody can void — staff need their own shift open and the
        owner may only void their own work, so an off-shift staff sale has no corrector
        at all (see void_allowed).

    A speed bump, NEVER a wall: callers must offer the clock-in rather than refuse the
    sale. Turning away a paying customer is how a POS gets thrown out.
    """
    if user == business.user:
        return False
    if not timecards_enabled(business):
        return False
    return not own_shift_open(business, user)


def own_shift_open(business, user):
    """Whether this user's own timecard is clocked in right now.

    Keyed on the user's Employee seat, so it is False for an owner (who has no seat) —
    callers must handle the owner before asking.
    """
    return ShiftEmployee.objects.filter(
        shift__business=business,
        shift__date=timezone.localdate(),
        employee__staff_user=user,
        clock_in__isnull=False,
        clock_out__isnull=True,
    ).exists()


def counted_drawers(business, on_date):
    """The day's CLOSED timecards — drawers whose cash has been counted and signed for."""
    return ShiftEmployee.objects.filter(
        shift__business=business,
        shift__date=on_date,
        clock_in__isnull=False,
        clock_out__isnull=False,
    )


def sealed_by_counted_drawer(business, on_date, rung_at, payments):
    """Whether this record's money has already been counted into a closed drawer.

    Sealed on EITHER the ring or the money: a record rung inside a closed window
    belongs to that drawer, and so does one rung outside it but PAID inside it (an
    utang collected mid-shift). Ring-time alone would miss the second and let a void
    pull cash back out of a drawer that has already been counted.
    """
    windows = Q()
    for clock_in, clock_out in counted_drawers(business, on_date).values_list(
            'clock_in', 'clock_out'):
        if clock_in <= rung_at <= clock_out:
            return True
        windows |= Q(created_at__gte=clock_in, created_at__lte=clock_out)

    if not windows:                       # no closed drawers today — nothing to disturb
        return False
    return payments.filter(windows).exists()


def void_allowed(business, user, *, on_date, rung_at, payments, created_by_id):
    """Whether `user` may void this record right now. Shared by sales and purchases.

    A void says "this never happened" — revenue drops, stock goes back, and the money
    comes out of expected_cash. That is also the shape of a cash skim, so a void is legal
    only while the drawer it touches is still open and uncounted. Once counted, the
    correction route is a Return, which leaves a trail.

    What makes a late void dangerous is that a reconciliation is half stored and half
    computed: counted_cash is frozen at clock-out, but expected_cash is a live property
    that re-sums payments on every read, excluding voided sales. So voiding out of a
    counted shift silently rewrites that shift's variance after the fact — and can
    manufacture a shortage against whoever put their name on the count.

    THE RULE (2026-07-16), keyed on the drawer rather than the day:
      - rung while a drawer was open  → voidable until that drawer closes
      - rung while no drawer was open → voidable until midnight
      - the drawer that counted it closed → sealed, for everyone, including the owner

    Deliberately NOT business-wide: the previous version asked "is ANY drawer open?",
    so an open PM shift re-opened voids on the AM staffer's already-counted drawer.

    `created_by_id` is the ringer — Sale.user is the OWNER on every row (tenancy), so
    keying ownership on it would match every sale for the owner and none for staff.
    """
    if on_date != timezone.localdate():
        return False                      # midnight closes the books

    if created_by_id is None or created_by_id != user.id:
        return False                      # you may only take back your own work

    # Owner is identified by the business, never by role — the `developer` role has
    # slipped through role checks before. Staff correct their own work while on shift;
    # where timecards don't exist there are no drawers to be absent from, so the
    # requirement is skipped rather than leaving staff sales nobody can void.
    if user != business.user and timecards_enabled(business):
        if not own_shift_open(business, user):
            return False

    return not sealed_by_counted_drawer(business, on_date, rung_at, payments)

def staff_seat_locked(user):
    """True when a staff user's every Employee seat is locked — i.e. the owner downgraded
    below their seat cap and this staff is one of the excess. Mirrors the owner-inactive guard."""
    if not getattr(user, 'is_authenticated', False) or getattr(user, 'role', None) != 'staff':
        return False
    from Employee.models import Employee
    states = list(Employee.objects.filter(staff_user=user).values_list('is_locked', flat=True))
    return bool(states) and all(states)
