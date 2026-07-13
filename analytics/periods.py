"""The period a page of Analytics is looking at.

One resolver, shared by Sales / Expense / Profit Analytics — so all three read the
same `?range=` params and compare against the same previous window. Filters are open
to every tier (only the PAGE is Pro-gated); see the analytics-gate decision.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

# The quick ranges, in the order they appear as chips: key, label, icon.
RANGE_CHOICES = [
    ('all',   'All time',     'bi-infinity'),
    ('week',  'This week',    'bi-calendar-week'),
    ('month', 'This month',   'bi-calendar-month'),
    ('30d',   'Last 30 days', 'bi-calendar3'),
]

RANGE_KEYS = {key for key, _label, _icon in RANGE_CHOICES}

# All time is the landing view: an owner opening Analytics cold wants the shape of the
# whole business first, then narrows. Its trade-off is that the KPI deltas have nothing
# to compare against and show their muted state — which is honest, not a bug.
DEFAULT_RANGE = 'all'

# A custom range longer than this is almost certainly a typo. All time is exempt — it
# is bounded by the data itself, not by a hand-typed date.
MAX_CUSTOM_DAYS = 366

# Where the trend chart stops drawing one point per day, then per week.
DAILY_BUCKET_LIMIT  = 62     # ~2 months
WEEKLY_BUCKET_LIMIT = 370    # ~1 year


def fmt_day(d):
    """'Jul 12' — written out rather than strftime('%-d') because %-d is not
    portable to Windows, where this project runs."""
    return f"{d.strftime('%b')} {d.day}"


def fmt_span(start, end):
    """'Jul 1 – Jul 12', or 'Aug 3, 2025 – Jul 12, 2026' once it crosses a year.

    A lifetime total means very little without the lifetime attached, so the year
    appears as soon as the window spans one.
    """
    if start == end:
        return fmt_day(start)
    if start.year != end.year:
        return f"{fmt_day(start)}, {start.year} – {fmt_day(end)}, {end.year}"
    return f"{fmt_day(start)} – {fmt_day(end)}"


@dataclass
class Period:
    key: str            # 'all' | 'week' | 'month' | '30d' | 'custom'
    start: date
    end: date           # INCLUSIVE
    prev_start: date
    prev_end: date      # INCLUSIVE
    days: int
    bucket: str         # 'day' | 'week' | 'month' — how the trend chart is grouped

    @property
    def label(self):
        return fmt_span(self.start, self.end)

    @property
    def prev_label(self):
        return fmt_span(self.prev_start, self.prev_end)

    @property
    def bucket_label(self):
        return {'day': 'Daily', 'week': 'Weekly', 'month': 'Monthly'}[self.bucket]

    @property
    def compares(self):
        """All time has nothing before it, so it shows no deltas at all — not even a
        muted "nothing to compare" row. An empty comparison against an impossible
        baseline is worse than no comparison."""
        return self.key != 'all'

    @property
    def compare_note(self):
        """Spelled out under the KPI strip, because a bare '↑ 12%' invites the
        reader to invent their own baseline."""
        if not self.compares:
            return None
        return f"vs previous {self.days} day{'s' if self.days != 1 else ''} ({self.prev_label})"


def _parse(value):
    """'2026-07-12' -> date, or None. Bad input is ignored, never raised — a
    hand-edited URL should fall back to the default range, not 500."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _bucket_for(days):
    if days <= DAILY_BUCKET_LIMIT:
        return 'day'
    if days <= WEEKLY_BUCKET_LIMIT:
        return 'week'
    return 'month'


def resolve_period(request, earliest=None):
    """Read ?range= / ?start= / ?end= into a Period.

    `earliest` is where 'All time' begins — the caller passes it because each Analytics
    page has its own first record (first sale, first purchase). With no records at all
    it collapses to today, and the page falls through to its empty state.

    A valid start+end always wins and is reported as 'custom', whatever ?range= says —
    otherwise the chips and the date inputs could disagree about what is on screen.
    """
    today = timezone.localdate()

    start = _parse(request.GET.get('start'))
    end   = _parse(request.GET.get('end'))

    if start and end and start <= end:
        key = 'custom'
        # Clamp rather than reject: the user still gets a page, ending where they asked.
        if (end - start).days + 1 > MAX_CUSTOM_DAYS:
            start = end - timedelta(days=MAX_CUSTOM_DAYS - 1)
    else:
        key = request.GET.get('range') or DEFAULT_RANGE
        if key not in RANGE_KEYS:
            key = DEFAULT_RANGE

        end = today
        if key == 'week':
            start = today - timedelta(days=today.weekday())   # Monday
        elif key == '30d':
            start = today - timedelta(days=29)
        elif key == 'month':
            start = today.replace(day=1)
        else:  # 'all'
            start = earliest or today
            # A record dated in the future (back-office typo) must not invert the window.
            start = min(start, today)

    days = (end - start).days + 1

    # Previous window = the same number of days, immediately before this one.
    # NOT "the whole of last month" — comparing a 12-day month-to-date against a
    # full 30-day June would make every month look like a collapse on the 2nd.
    # For 'all' this window is empty by definition, so every delta goes muted.
    prev_end   = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=days - 1)

    return Period(
        key=key,
        start=start,
        end=end,
        prev_start=prev_start,
        prev_end=prev_end,
        days=days,
        bucket=_bucket_for(days),
    )
