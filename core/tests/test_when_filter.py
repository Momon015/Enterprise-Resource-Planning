"""`|when:reference` — show a timestamp's date only when it differs from a reference day.

On the canceled-sales list the row already prints when the sale was rung up, so repeating
that date beside "canceled by" says nothing. What the reader wants to spot is a sale rung
up one day and killed on a LATER one — so the date appears only then.

The comparison is against another value ON THE SAME ROW, never against today. That keeps
the output stable: a row rendered today reads the same tomorrow.
"""
from datetime import datetime, timedelta, timezone as dt_timezone

from django.template import Context, Template
from django.utils import timezone


def render(value, since):
    return Template('{% load date_tags %}{{ v|when:s }}').render(
        Context({'v': value, 's': since}))


def test_canceled_the_same_day_it_was_rung_up_shows_time_only():
    rung_up = timezone.localtime(timezone.now()).replace(hour=9, minute=7)
    canceled = rung_up + timedelta(hours=2)

    out = render(canceled, rung_up)

    assert ',' not in out, f"the date is redundant — the row already shows it: {out!r}"
    assert out == '11:07 AM', out


def test_canceled_the_NEXT_day_shows_the_date():
    """The case the filter exists for. Jun 20 → Jun 21 must not read as a same-day cancel."""
    rung_up = timezone.make_aware(datetime(2026, 6, 20, 19, 47))
    canceled = timezone.make_aware(datetime(2026, 6, 21, 19, 47))

    out = render(canceled, rung_up)

    assert out == 'Jun 21, 7:47 PM', out


def test_a_cancel_days_later_shows_the_date():
    rung_up = timezone.make_aware(datetime(2026, 6, 20, 9, 0))
    canceled = timezone.make_aware(datetime(2026, 7, 4, 16, 30))

    assert render(canceled, rung_up) == 'Jul 04, 4:30 PM'


def test_an_overnight_cancel_is_not_hidden_by_utc():
    """★ The UTC trap. Rung up 9 AM Jun 20 and canceled 2 AM Jun 21 (Manila) are the SAME
    UTC date — 01:00 and 18:00 on Jun 20 UTC. Comparing the stored values raw would call
    this a same-day cancellation and drop the date, which is precisely the overnight case
    the filter is meant to surface."""
    rung_up = timezone.make_aware(datetime(2026, 6, 20, 9, 0))
    canceled = timezone.make_aware(datetime(2026, 6, 21, 2, 0))

    assert rung_up.astimezone(dt_timezone.utc).date() == canceled.astimezone(dt_timezone.utc).date(), (
        "fixture no longer straddles midnight in local time — the trap isn't being tested"
    )
    assert render(canceled, rung_up) == 'Jun 21, 2:00 AM'


def test_it_does_not_drift_with_todays_date():
    """Two old timestamps on the same day stay a same-day render forever — the output must
    not depend on when the page happens to be viewed."""
    rung_up = timezone.make_aware(datetime(2024, 1, 15, 8, 0))
    canceled = timezone.make_aware(datetime(2024, 1, 15, 17, 30))

    assert render(canceled, rung_up) == '5:30 PM'


def test_a_missing_reference_falls_back_to_showing_the_date():
    """Better to show too much than to imply a same-day cancel we can't actually confirm."""
    canceled = timezone.make_aware(datetime(2026, 6, 21, 19, 47))

    assert render(canceled, None) == 'Jun 21, 7:47 PM'


def test_naive_datetimes_do_not_blow_up():
    """Safety net for any field predating USE_TZ — localtime() raises on naive values."""
    assert render(datetime(2020, 3, 1, 14, 30), datetime(2020, 3, 1, 9, 0)) == '2:30 PM'
    assert render(datetime(2020, 3, 2, 14, 30), datetime(2020, 3, 1, 9, 0)) == 'Mar 02, 2:30 PM'


def test_nothing_renders_a_dash():
    assert render(None, timezone.now()) == '—'
    assert render('', timezone.now()) == '—'
