"""The odometer has to be WIRED, not just correct.

test_accumulated_grand_sales.py proves AccumulatedGrandSalesEntry.post() behaves. That is a
different claim from "voiding a sale in the actual app records anything", and the
gap between those two claims is where a green suite hides a missing hook — the same
shape as the `pending_count` bug: both halves covered, intersection empty.

So this file never calls post() directly. It drives the real view through the real
URL and asks what the odometer says afterwards.

Covering checkout the same way is worth doing but is a bigger fixture (a session
cart, stock, a shift), so the finalize hook is currently pinned by reading rather
than by test. Void and returns are the paths where a missing hook would silently
UNDER-report to BIR, so they get the coverage first.
"""
from decimal import Decimal

import pytest
from django.urls import reverse

from activity.models import AccumulatedGrandSalesEntry
from tests.factories import make_business, make_product, make_sale


pytestmark = pytest.mark.django_db


@pytest.fixture
def sale_to_void(client, owner):
    """A completed sale, its business, and a logged-in owner who can still void it.

    can_void_sale() gates on the drawer, not the calendar — a business with no shift
    today has nothing to disturb, so a same-day sale stays voidable.
    """
    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='125')
    sale = make_sale(biz, [(product, 2)])       # 250.00
    client.force_login(owner)
    return biz, sale


def test_voiding_through_the_view_records_to_the_void_channel(client, sale_to_void):
    biz, sale = sale_to_void

    response = client.post(
        reverse('void-sale', kwargs={'business_slug': biz.slug, 'sale_id': sale.id}),
        {'void_reason': 'wrong_item', 'action': 'void'},
    )

    assert response.status_code in (200, 302)
    sale.refresh_from_db()
    assert sale.is_void is True, "the void itself didn't happen — test is measuring nothing"

    assert AccumulatedGrandSalesEntry.total_for(biz, AccumulatedGrandSalesEntry.CHANNEL_VOID) == Decimal('250.00')


def test_voiding_through_the_view_leaves_the_sales_odometer_alone(client, sale_to_void):
    """The end-to-end version of the rule. make_sale() builds its Sale directly rather
    than going through checkout, so the sales channel starts at zero here — which is
    exactly what makes this readable: after a void it must STILL be zero, never
    negative. A hook that subtracted would show -250.00.
    """
    biz, sale = sale_to_void
    AccumulatedGrandSalesEntry.post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('250.00'),
                         source=sale, ref=sale.reference)

    client.post(
        reverse('void-sale', kwargs={'business_slug': biz.slug, 'sale_id': sale.id}),
        {'void_reason': 'wrong_item', 'action': 'void'},
    )

    assert AccumulatedGrandSalesEntry.total_for(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE) == Decimal('250.00')


def test_a_void_is_dated_by_when_it_happened_not_by_the_sale(client, owner):
    """A void belongs to the day it was performed, not the day the sale was rung.

    NOT reachable through the view today, and worth being precise about why: the
    void gate refuses outright when `on_date != timezone.localdate()`
    ([Employee/utils.py:252] — "midnight closes the books"), so in production the
    two dates are always the same and the distinction never shows. This test drives
    post() directly instead of pretending otherwise.

    It is kept because the void hook passes business_date EXPLICITLY rather than
    letting it fall back to sale.date, and that choice is only load-bearing if the
    same-day rule ever loosens — which the parked period-lock design could do. If
    that happens, a Wednesday void of a Monday sale would otherwise file itself onto
    Monday's reading, a day DailyClose may already have frozen append-only.
    """
    from datetime import timedelta
    from django.utils import timezone as tz

    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='125')
    monday = tz.localdate() - timedelta(days=3)
    sale = make_sale(biz, [(product, 2)], date=monday)

    entry = AccumulatedGrandSalesEntry.post(
        biz, AccumulatedGrandSalesEntry.CHANNEL_VOID, sale.total_revenue,
        source=sale, ref=sale.reference,
        business_date=tz.localdate(),          # what the void hook passes
    )

    assert sale.date == monday, "the sale keeps its own date on its own entry"
    assert entry.business_date == tz.localdate(), (
        "an explicit business_date must win over source.date — otherwise the void "
        "files itself onto the day the sale was rung"
    )


def test_the_void_entry_names_the_invoice_it_reversed(client, sale_to_void):
    """A reading that says "voids: ₱250" without saying WHICH invoice is not an audit
    trail. The reference is snapshotted at post time."""
    biz, sale = sale_to_void
    reference = sale.reference

    client.post(
        reverse('void-sale', kwargs={'business_slug': biz.slug, 'sale_id': sale.id}),
        {'void_reason': 'wrong_item', 'action': 'void'},
    )

    entry = AccumulatedGrandSalesEntry.objects.get(business=biz, channel=AccumulatedGrandSalesEntry.CHANNEL_VOID)
    assert entry.source_model == 'Sale'
    assert entry.source_id == sale.id
    assert entry.source_ref == reference
