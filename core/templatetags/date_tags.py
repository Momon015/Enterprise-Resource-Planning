"""Date/time presentation filters.

Kept separate from the money and pagination libs because these are purely about how a
timestamp READS, never about what it means.
"""
from datetime import datetime

from django import template
from django.utils import timezone
from django.utils.dateformat import format as dateformat

register = template.Library()


def _local(value):
    """An aware datetime moved into the project timezone; anything else untouched.

    Naive values pass through rather than raising, so these filters stay safe on any field
    that predates USE_TZ. Plain `date` objects pass through too — `is_aware` reads
    `.tzinfo`, which a date doesn't have.
    """
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


def _calendar_day(value):
    return value.date() if isinstance(value, datetime) else value


@register.filter
def when(value, since):
    """`value` as a bare time when it falls on the same calendar day as `since`, else with
    its date.

    ★ The comparison is against ANOTHER VALUE ON THE SAME ROW — not against today. On the
    canceled-sales list the row already prints when the sale was rung up, so repeating that
    date beside "canceled by" says nothing; what the reader actually wants to spot is a
    sale that was rung up one day and killed on a LATER one. Rung up and canceled on Jun 20
    reads `7:47 PM`; rung up Jun 20 and canceled Jun 21 reads `Jun 21, 7:47 PM`.

    That also means the output is stable — it does not silently change tomorrow, the way a
    comparison against `today` would.

    Compared in LOCAL time (Asia/Manila), not UTC: the project stores aware datetimes and
    PH is UTC+8, so a sale rung up at 9 AM and canceled at 2 AM the next morning is the
    same UTC date but two different local days. Comparing raw would hide the date on
    exactly the overnight case this filter exists to surface.
    """
    if not value:
        return '—'
    value = _local(value)
    if since and _calendar_day(value) == _calendar_day(_local(since)):
        return dateformat(value, 'g:i A')
    return dateformat(value, 'M d, g:i A')
