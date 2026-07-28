from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import Shift, ShiftEmployee, OpeningCashOverride, DrawerSession


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

    # Owner-closed OR system-auto-closed shifts the staff hasn't confirmed — PERSISTS (no
    # expiry filter): created AT closure, so it must survive past time-out until the staff
    # reviews it. auto_closed leaves closed_by NULL (there's no System user), so both cases
    # are OR'd in — mirrors ShiftEmployee.close_needs_ack.
    closes = ShiftEmployee.objects.filter(
        Q(closed_by__isnull=False) | Q(auto_closed=True),
        employee__staff_user=user,
        shift__business=business,
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


def open_shift_for_user(user):
    """This user's currently-open shift ANYWHERE, or None.

    Unlike own_shift_open (one business, today only), this spans every business the
    user is an employee of and ignores the date — it backs the logout guard, which has
    no business context and must catch a shift left open from a prior day too. An owner
    has no Employee seat, so this is always None for them (they never clock in).
    """
    return (
        ShiftEmployee.objects.filter(
            employee__staff_user=user,
            clock_in__isnull=False,
            clock_out__isnull=True,
        )
        .select_related('shift', 'shift__business', 'employee')
        .order_by('clock_in')
        .first()
    )


def shift_needs_drawer_count(shift_emp):
    """Whether closing this shift requires counting a cash drawer.

    True only for a cashier on a business with reconciliation switched on and a plan that
    includes it — the exact condition the clock-out page uses for `needs_reconciliation`.
    For everyone else (sales clerks, or cashiers where reconciliation is off) timing out is
    a one-tap formality with nothing to count, so the logout guard can skip straight past it.
    """
    business = shift_emp.shift.business
    plan = getattr(business, 'plan', None)
    return bool(
        shift_emp.is_cashier
        and business is not None and business.enable_cash_reconciliation
        and plan is not None and plan.has_cash_reconciliation()
    )


def clock_out_shift_now(shift_emp, now=None):
    """A clean, immediate time-out with no cash count — the staff's OWN close, used when a
    non-cashier (or a reconciliation-off shift) logs out and there's nothing to count.

    Unlike `_auto_close_shift`, this leaves NO review flag behind (no auto_closed, no
    close_reason, no ack): the staff member is closing their own shift on purpose, so
    there's nothing for anyone to dispute. Closes any open drawer session this shift holds
    (a non-cashier won't have one; the guard just makes it safe either way).
    """
    now = now or timezone.now()
    with transaction.atomic():
        shift_emp.clock_out = now
        shift_emp.save()  # full save: mirrors the clock-out view's own time-out path
        session = shift_emp.drawer_session
        if session and session.is_open and session.current_holder_id == shift_emp.id:
            session.status = 'closed'
            session.closed_at = now
            session.save(update_fields=['status', 'closed_at'])


def open_fresh_shift(business, employee):
    """Clock `employee` in for a FRESH drawer open (no hand-over) and return the ShiftEmployee.

    Shared by the clock-in page and the point-of-sale clock-in modal so both snapshot opening
    cash identically. This is deliberately the FRESH path only — a shared-drawer HAND-OVER needs
    a blind recount and lives in Employee.views.clock_in; callers must rule that out first.
    For cashiers the caller must have confirmed the opening cash; attendance-only (non-cashier)
    shifts start at 0 and skip all cash.

    ⚠ Keep in sync with the fresh branch of Employee.views.clock_in — the opening-cash snapshot
    logic is intentionally identical. If one changes, change both.
    """
    today = timezone.localdate()
    is_cashier = employee.is_cashier

    with transaction.atomic():
        shift, _ = Shift.objects.get_or_create(
            business=business, date=today, user=business.user,
            defaults={'amount': Decimal('0')},
        )

        opening_cash = Decimal('0')
        opening_bills = Decimal('0')
        opening_coins = Decimal('0')
        drawer_session = None
        if is_cashier:
            opening_cash = get_opening_cash_for_today(business)['amount']
            if business.track_coins_separately:
                opening_bills = business.default_opening_bills
                opening_coins = business.default_opening_coins
                opening_cash = opening_bills + opening_coins
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
        if drawer_session is not None:
            drawer_session.current_holder = shift_emp
            drawer_session.save(update_fields=['current_holder'])

    return shift_emp


def counted_drawers(business, on_date):
    """The day's CLOSED timecards — drawers whose cash has been counted and signed for."""
    return ShiftEmployee.objects.filter(
        shift__business=business,
        shift__date=on_date,
        clock_in__isnull=False,
        clock_out__isnull=False,
    )


# ──────────────────────────────────────────────────────────────
# Auto clock-out of shifts a staff member forgot to close.
#
# A forgotten clock-out doesn't cost payroll (the daily rate is earned at clock-in,
# see Shift.recompute_amount), but it leaves the shift `is_active` forever — which
# blocks the next day's clock-in and lets expected_cash keep absorbing every later
# sale (ShiftEmployee._shift_window_end falls back to "now" while clock_out is None).
# The system closes such shifts at the business's closing time and flags them for the
# same acknowledge/dispute review an owner-close gets.
#
# Two callers share this: the nightly `close_stale_shifts` management command (the
# precise-midnight path once a scheduler runs it) and a lazy sweep on clock-in /
# timecard load (works today with no scheduler). Both must agree, so the rule lives here.
# ──────────────────────────────────────────────────────────────

def shift_auto_close_cutoff(shift_emp, business):
    """When an unclosed shift should be auto-closed — an aware datetime, or None.

    - closing_time SET: the first occurrence of that wall-clock time AT OR AFTER clock-in.
      Rolling to the next day when needed lets a store that closes after midnight (e.g.
      a 2 AM bar) still get the correct same-session cutoff.
    - closing_time NULL (open 24 hours): there's no daily close, so cap the shift at 24
      hours after clock-in — nobody works more than a day straight.
    """
    if not shift_emp.clock_in:
        return None
    closing = getattr(business, 'closing_time', None) if business else None
    if not closing:
        return shift_emp.clock_in + timedelta(hours=24)
    # Work in local wall-clock so "closing time" means the owner's clock, not UTC.
    ci_local = timezone.localtime(shift_emp.clock_in)
    cutoff_local = ci_local.replace(
        hour=closing.hour, minute=closing.minute, second=0, microsecond=0
    )
    if cutoff_local <= ci_local:
        cutoff_local += timedelta(days=1)
    return cutoff_local


def close_stale_shifts(business=None, now=None):
    """Auto-close every open shift whose cutoff has passed. Idempotent — only ever
    touches shifts with clock_in set and clock_out NULL, so re-running does nothing.

    Pass a `business` to scope it (the lazy sweep); omit it to sweep all businesses
    (the nightly command). Returns the count of shifts closed.
    """
    now = now or timezone.now()
    qs = ShiftEmployee.objects.filter(
        clock_in__isnull=False, clock_out__isnull=True,
    ).select_related('shift', 'shift__business', 'drawer_session')
    if business is not None:
        qs = qs.filter(shift__business=business)

    closed = 0
    for shift_emp in qs:
        biz = shift_emp.shift.business
        if biz is None:
            continue
        cutoff = shift_auto_close_cutoff(shift_emp, biz)
        if cutoff is None or now < cutoff:
            continue  # still within its window — leave it open
        _auto_close_shift(shift_emp, cutoff, now)
        closed += 1
    return closed


def _auto_close_shift(shift_emp, cutoff, now):
    """Stamp the auto clock-out and flag it for review. counted_* are left NULL on
    purpose: nobody physically counted the drawer, so a cashier's expected_cash shows
    as 'what should be there, unverified' until the staff/owner reconciles — we never
    fabricate a ₱0 count that would read as a false shortage.

    Two audiences (see [design notes]): the STAFF get the shift flagged for a
    read-and-dismiss on their side (close_needs_ack, reusing the owner-close banner);
    the OWNER gets a bell notification — but only for a CASHIER shift with an actual
    drawer, since an attendance-only shift has nothing to reconcile."""
    business = shift_emp.shift.business
    with transaction.atomic():
        shift_emp.clock_out = cutoff
        shift_emp.auto_closed = True
        shift_emp.auto_closed_at = now
        shift_emp.close_reason = "Auto-closed by the system — no clock-out was recorded."
        shift_emp.close_acknowledged = False
        shift_emp.save()  # ShiftEmployee.save sums counts; the post-save signal recomputes payroll

        # Release a shared drawer this shift was holding (mirrors the clock_out view).
        session = shift_emp.drawer_session
        if session and session.is_open and session.current_holder_id == shift_emp.id:
            session.status = 'closed'
            session.closed_at = cutoff
            session.save(update_fields=['status', 'closed_at'])

    # Notify the OWNER — cashier drawers only (nothing to reconcile on an attendance shift,
    # and no drawer when reconciliation is off). The peso figure lives on the shift page
    # (access-controlled), NOT in the bell text, which is shared with staff by design.
    plan = getattr(business, 'plan', None)
    drawer_to_check = (
        shift_emp.is_cashier
        and business.enable_cash_reconciliation
        and plan is not None and plan.has_cash_reconciliation()
    )
    if drawer_to_check:
        from activity.utils import log_activity   # local import avoids a circular load
        name = shift_emp.name or (shift_emp.employee.name if shift_emp.employee else 'A staff member')
        day = timezone.localtime(cutoff).strftime('%b %d')
        log_activity(
            business, actor=None, verb='shift.auto_closed', target=shift_emp,
            description=(
                f"System closed {name}'s shift ({day}) — no time-out was recorded, so the "
                f"cash drawer wasn't counted. Tap to check it against the expected amount."
            )[:255],
            metadata={'expected_cash': f"{shift_emp.expected_cash:.2f}"},
            important=True,
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
