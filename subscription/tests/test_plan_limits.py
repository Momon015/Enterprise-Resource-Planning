"""Couplings inside PLAN_LIMITS.

The limits are one flat config dict, so nothing stops two settings that depend on each
other from drifting apart in an edit. Each test here pins a pairing that code elsewhere
silently relies on, so the edit fails here rather than in production.
"""
from subscription.models import PLAN_LIMITS


def _has_seats(limits):
    """None means unlimited in this dict (cf. max_products), not zero."""
    seats = limits['max_staff']
    return seats is None or seats > 0


def test_every_plan_with_staff_seats_also_has_timecards():
    """Staff without timecards would be staff who can neither sell nor void.

    Employee.utils gates both selling (must_clock_in_to_sell) and voiding (void_allowed)
    on the staff member being clocked in. On a plan with seats but no timecards, clocking
    in is impossible — Employee.views.clock_in bounces on has_timecards() — so those staff
    could never ring a sale, and could never take back their own mistake.

    Free is safe today only because it has zero seats. Grant it a single seat without
    turning timecards on and both rules break at once, in two apps, silently.
    """
    for name, limits in PLAN_LIMITS.items():
        if _has_seats(limits):
            assert limits['timecards'] is True, (
                f"plan '{name}' has staff seats (max_staff={limits['max_staff']}) but "
                f"timecards are off — those staff could not sell or void"
            )


def test_free_has_no_staff_seats():
    """The other half of the pairing above, stated directly.

    Free is the only plan without timecards, so it must also be the plan without staff.
    If free ever gains a seat, the test above is the one that will explain why.
    """
    assert PLAN_LIMITS['free']['max_staff'] == 0
    assert PLAN_LIMITS['free']['timecards'] is False
