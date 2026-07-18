"""The "while you were away" void count and its Review link must agree.

The banner prints "N sales voided while you were away" and links to the sales list. If those
two disagree, the dashboard contradicts itself on screen — the owner reads "1 sale voided",
clicks Review, and counts 2 rows. That shipped on 2026-07-18 and was user-caught.

WHY IT BROKE, and why day-granularity can never fix it: voids_count is computed from
`voided_at` over a TIME window (say 08:40–15:42). The link originally scoped itself with
start_date/end_date, which narrows to a whole CALENDAR DAY. A sale voided at 08:22 — before
the owner ever left — carries the same `date` as one voided at 14:15, so it slipped into the
list while being correctly excluded from the count. The reasoning that justified the shortcut
("voids are same-day gated, so voided_at's day == sale.date's day") is true but irrelevant:
the away window is a slice INSIDE a day, and a day-wide filter cannot express it.

So the link bounds the void INSTANT (`voided_from`/`voided_to`, epoch seconds). This test walks
the real wiring rather than re-deriving it: it renders the dashboard, pulls the href out of the
banner, follows it, and checks the rows match the number the banner printed. Anything that
re-breaks the pairing — swapping the params back to dates, dropping them, changing how the
count is computed — fails here.
"""
import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from Dashboard.models import DashboardSeen
from tests.factories import make_business, make_product, make_sale

ALERT_HREF = re.compile(r'away-banner__alert[^>]*href="([^"]+)"')
ALERT_COUNT = re.compile(r'<strong>(\d+) sale')
QUICK_RANGE_HREF = re.compile(r'<a href="([^"]*)"[^>]*class="quick-range-btn')


def _void(sale, at, by):
    """Void `sale` with an explicit voided_at. Sale.save() only permits the void fields."""
    sale.is_void = True
    sale.void_reason = 'Wrong price'
    sale.voided_by = by
    sale.voided_at = at
    sale.save(update_fields=['is_void', 'void_reason', 'voided_by', 'voided_at'])
    return sale


@pytest.fixture
def away_window(client, owner):
    """A business whose away window opened 2h ago, with the window start returned.

    First dashboard visit seeds DashboardSeen; backdating it past AWAY_GAP_MINUTES (30) means
    the NEXT visit opens a window running from that backdated instant to now.
    """
    biz, _plan = make_business(owner, plan='pro')     # pro → dashboard is available
    client.force_login(owner)
    client.get(reverse('dashboard', kwargs={'business_slug': biz.slug}))

    seen = DashboardSeen.objects.get(user=owner, business=biz)
    window_start = timezone.now() - timedelta(hours=2)
    seen.seen_at = window_start
    seen.save(update_fields=['seen_at'])
    return biz, window_start


def test_review_link_returns_exactly_the_sales_the_banner_counted(client, owner, away_window):
    """A void BEFORE the window must not appear behind a banner that excluded it."""
    biz, window_start = away_window
    product = make_product(biz, stock=50)

    # Voided 30 min BEFORE the owner left → outside the window, must NOT be counted or listed.
    before = _void(make_sale(biz, [(product, 1)]), window_start - timedelta(minutes=30), owner)
    # Voided 30 min AFTER the window opened → inside, must be counted AND listed.
    inside = _void(make_sale(biz, [(product, 2)]), window_start + timedelta(minutes=30), owner)

    # Both were rung today, so a date-scoped link would sweep up BOTH — that was the bug.
    assert before.date == inside.date

    dash = client.get(reverse('dashboard', kwargs={'business_slug': biz.slug})).content.decode()

    href_match = ALERT_HREF.search(dash)
    assert href_match, "no void alert rendered — the banner is not exercising this path"
    count_match = ALERT_COUNT.search(dash)
    assert count_match, "could not read the void count out of the banner"

    banner_count = int(count_match.group(1))
    assert banner_count == 1, f"banner counted {banner_count}, expected only the in-window void"

    listed = client.get(href_match.group(1).replace('&amp;', '&')).content.decode()
    assert inside.reference in listed, "the in-window void is missing from the Review list"
    assert before.reference not in listed, (
        f"{before.reference} was voided BEFORE the window but the Review link listed it — the "
        "link is scoping by calendar day again instead of the void instant"
    )


def test_void_instant_bounds_are_what_narrow_the_list(client, owner, away_window):
    """Guard the mechanism directly, so the pairing test can't pass for the wrong reason.

    Without voided_from/voided_to, ?void=1 is deliberately every void (that is what the manual
    "Voided" chip is). The bounds are the only thing that narrows it to a window.
    """
    biz, window_start = away_window
    product = make_product(biz, stock=50)
    before = _void(make_sale(biz, [(product, 1)]), window_start - timedelta(minutes=30), owner)
    inside = _void(make_sale(biz, [(product, 2)]), window_start + timedelta(minutes=30), owner)

    url = reverse('sale-list', kwargs={'business_slug': biz.slug})

    unbounded = client.get(url, {'void': '1'}).content.decode()
    assert before.reference in unbounded and inside.reference in unbounded, (
        "?void=1 alone should list every voided sale — that is the manual chip's behaviour"
    )

    bounded = client.get(url, {
        'void': '1',
        'voided_from': int(window_start.timestamp()),
        'voided_to': int(timezone.now().timestamp()),
    }).content.decode()
    assert inside.reference in bounded
    assert before.reference not in bounded, "voided_from did not exclude the earlier void"


def test_list_controls_clear_the_window_bounds(client, owner, away_window):
    """The bounds are the recap's one-shot narrowing — no control may carry them forward.

    Second user-caught bug, same day: `{% querystring %}` keeps every param it is not told to
    drop, and voided_from/voided_to were named by nothing. They rode through All, Reset and the
    quick ranges invisibly, so after arriving from the recap, re-picking Voided + Today showed 1
    of today's 2 voids — and only leaving the module (which rebuilds the query string from
    scratch) restored the other. Nothing on screen explained the missing row, which is what makes
    a silent carried-over filter worse than a wrong one.
    """
    biz, window_start = away_window
    product = make_product(biz, stock=50)
    before = _void(make_sale(biz, [(product, 1)]), window_start - timedelta(minutes=30), owner)
    inside = _void(make_sale(biz, [(product, 2)]), window_start + timedelta(minutes=30), owner)

    # Arrive the way the owner does: via the recap link, bounded to the away window.
    dash = client.get(reverse('dashboard', kwargs={'business_slug': biz.slug})).content.decode()
    bounded_url = ALERT_HREF.search(dash).group(1).replace('&amp;', '&')
    bounded_page = client.get(bounded_url).content.decode()

    # Only the SALES card's controls, so scan above the Receivables panel's live region. That
    # panel shares the query string but is deliberately hands-off toward the sales table — it
    # already carries period/payment/user forward so filtering the panel doesn't reshuffle the
    # rows above it, and the window bounds are just another part of the sales view the owner
    # arrived at. Dropping them there would silently WIDEN the sales table on a click meant for
    # the panel. (Filtering its hrefs by a `recv_` substring does NOT work: its "All" clears
    # every recv_ param, so the rendered href has no recv_ left in it to match on.)
    sales_card = bounded_page.split('id="recv-results"')[0]
    assert 'quick-range-btn' in sales_card, "sales card not found above the receivables panel"
    controls = QUICK_RANGE_HREF.findall(sales_card)
    assert controls, "no quick-range controls rendered — this test is not reaching them"
    leaking = [h for h in controls if 'voided_from' in h or 'voided_to' in h]
    assert not leaking, (
        f"{len(leaking)} control(s) carry the away-window bounds forward: {leaking[:3]} — "
        "every control in the card must name voided_from=None voided_to=None"
    )

    # And end-to-end: clearing back to All must really show both of today's voids again.
    # These controls are RELATIVE ("?a=b", often a bare "?" once everything is cleared), so they
    # have to be hung off the list path or the client never reaches the view.
    all_qs = [h for h in controls if 'void=' not in h and 'period=' not in h][0]
    all_url = reverse('sale-list', kwargs={'business_slug': biz.slug}) + all_qs.replace('&amp;', '&')
    reset = client.get(all_url).content.decode()
    assert before.reference in reset and inside.reference in reset, (
        "'All' did not restore the full list — a bound survived the reset"
    )


def test_garbage_bounds_do_not_500(client, owner, away_window):
    """The params come off a URL, so they must degrade instead of raising."""
    biz, _window_start = away_window
    product = make_product(biz, stock=50)
    _void(make_sale(biz, [(product, 1)]), timezone.now(), owner)

    url = reverse('sale-list', kwargs={'business_slug': biz.slug})
    for bad in ['abc', '', '9' * 30, '-1']:
        resp = client.get(url, {'void': '1', 'voided_from': bad, 'voided_to': bad})
        assert resp.status_code == 200, f"voided_from={bad!r} returned {resp.status_code}"
