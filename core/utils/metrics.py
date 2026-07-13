"""Shared metric helpers.

Lives in core so the Dashboard (today vs yesterday) and Analytics (period vs
previous period) compute their deltas with the SAME function. Two copies of this
drift apart silently — the arrow points one way on one page and the other way on
the next, and nobody notices until a number is already being trusted.
"""


def pct_delta(current, previous):
    """Return ('up'|'down'|'flat', delta_string) or (None, None) if no comparison.

    Direction is driven by the raw change (current - previous), NOT the sign of the
    percentage. Net Cash / Net Profit can be negative, and dividing by a negative
    base flips the percentage's sign — so a genuine improvement (e.g. -900 -> +1246)
    would wrongly show a down arrow. When the base is negative a percentage is also
    meaningless ("238% better than -900"?), so we show the peso swing instead.
    """
    current  = float(current or 0)
    previous = float(previous or 0)

    if previous == 0:
        return (None, None)  # no base to compare against — template hides the row

    change = current - previous
    if abs(change) < 0.005:
        return ('flat', '0.0%')

    direction = 'up' if change > 0 else 'down'

    if previous < 0:
        # Base is negative — a percentage would mislead; show the peso change.
        return (direction, f"₱{abs(change):,.0f}")

    pct = abs(change / previous) * 100
    return (direction, f"{pct:.1f}%")
