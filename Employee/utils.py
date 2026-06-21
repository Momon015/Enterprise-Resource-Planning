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

def void_window_open(business):
    """Whether voids are currently allowed for this business.
    - Has staff/timecards: open only while a shift is still clocked in today
      (closes once ALL staff clock out).
    - Free/solo, or a no-shift day: open until midnight (same-day only)."""
    today = timezone.localdate()
    todays = ShiftEmployee.objects.filter(
        shift__business=business,
        shift__date=today,
        clock_in__isnull=False,
    )
    if todays.exists():
        return todays.filter(clock_out__isnull=True).exists()
    return True
