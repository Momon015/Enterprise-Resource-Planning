"""The BIR Accumulated Grand Total must only ever increase.

Every other revenue figure in this app is a `.active()` SUM, so voiding a sale walks
it backwards — correct for telling an owner what they earned, disqualifying for BIR.
RMO 24-2023 wants a perpetual odometer that does not move down, and "sales suppression
mechanism" is listed as grounds for revoking accreditation. So the interesting tests
here are not "does it add up" but "can anything make it go DOWN".

Three ways it could rewind, one test each: a void, a return, and a caller passing a
negative amount because they thought subtracting was how you express a reversal.
"""
from datetime import date
from decimal import Decimal

import pytest

from activity.models import AccumulatedGrandSalesEntry, AccumulatedGrandSalesCounter
from tests.factories import make_owner, make_business, make_product, make_sale


pytestmark = pytest.mark.django_db


@pytest.fixture
def biz():
    owner, _sub = make_owner()
    business, _bp = make_business(owner)
    return business


ANY_DAY = date(2026, 7, 20)


def post(business, channel, amount, **kwargs):
    """post() refuses to invent a business_date, and most tests here don't care
    which day it is — they're about the arithmetic. Supply one so the date guard
    isn't what they're accidentally testing. Tests that DO care about the date call
    AccumulatedGrandSalesEntry.post directly."""
    kwargs.setdefault('business_date', ANY_DAY)
    return AccumulatedGrandSalesEntry.post(business, channel, amount, **kwargs)


def sales_total(business):
    return AccumulatedGrandSalesEntry.total_for(business, AccumulatedGrandSalesEntry.CHANNEL_SALE)


# ── the odometer only climbs ────────────────────────────────────────────────

def test_a_void_does_not_reduce_the_sales_odometer(biz):
    """The whole reason this model exists instead of reusing Total Revenue."""
    post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('250.00'))
    before = sales_total(biz)

    # the void posts to its OWN channel
    post(biz, AccumulatedGrandSalesEntry.CHANNEL_VOID, Decimal('250.00'))

    assert sales_total(biz) == before == Decimal('250.00')
    assert AccumulatedGrandSalesEntry.total_for(biz, AccumulatedGrandSalesEntry.CHANNEL_VOID) == Decimal('250.00')


def test_a_return_does_not_reduce_the_sales_odometer(biz):
    post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('900.00'))

    post(biz, AccumulatedGrandSalesEntry.CHANNEL_RETURN, Decimal('300.00'))

    assert sales_total(biz) == Decimal('900.00')
    assert AccumulatedGrandSalesEntry.total_for(biz, AccumulatedGrandSalesEntry.CHANNEL_RETURN) == Decimal('300.00')


def test_a_negative_amount_is_refused_loudly(biz):
    """Not silently ignored — raised.

    A caller reaching for a negative is a caller who thinks reversals subtract. If
    that returned None the odometer would stay correct but the caller would carry on
    believing it had recorded the reversal, and the Z reading would under-report
    voids. Better to break at the call site.
    """
    with pytest.raises(ValueError, match='only ever increases'):
        post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('-50.00'))

    assert sales_total(biz) == Decimal('0.00')


def test_the_odometer_cannot_be_edited_or_deleted(biz):
    entry = post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('100.00'))

    entry.amount = Decimal('1.00')
    with pytest.raises(ValueError, match='append-only'):
        entry.save()

    with pytest.raises(ValueError, match='append-only'):
        entry.delete()


# ── the running total is auditable on its own ───────────────────────────────

def test_each_entry_records_the_total_as_of_itself(biz):
    """`running_total` is what lets a single row be audited without replaying the
    whole ledger: 'the total went 100 → 250 because this sale rang 150'."""
    first  = post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('100.00'))
    second = post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('150.00'))

    assert first.running_total == Decimal('100.00')
    assert second.running_total == Decimal('250.00')


def test_channels_are_independent_odometers(biz):
    """A void must not advance the sales counter, or the two would be one number."""
    post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('80.00'))
    post(biz, AccumulatedGrandSalesEntry.CHANNEL_VOID, Decimal('80.00'))
    post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('20.00'))

    assert sales_total(biz) == Decimal('100.00')
    assert AccumulatedGrandSalesEntry.total_for(biz, AccumulatedGrandSalesEntry.CHANNEL_VOID) == Decimal('80.00')


def test_the_counter_head_agrees_with_the_entries(biz):
    """The counter is a cache; the entries are the truth. If these ever disagree in
    production the counter is the bug, so pin the invariant here."""
    for amount in ('10.00', '25.50', '4.25'):
        post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal(amount))

    from django.db.models import Sum
    summed = AccumulatedGrandSalesEntry.objects.filter(
        business=biz, channel=AccumulatedGrandSalesEntry.CHANNEL_SALE,
    ).aggregate(t=Sum('amount'))['t']

    counter = AccumulatedGrandSalesCounter.objects.get(business=biz,
                                            channel=AccumulatedGrandSalesEntry.CHANNEL_SALE)
    assert counter.total == summed == Decimal('39.75')
    assert counter.entry_count == 3


def test_two_businesses_keep_separate_odometers(biz):
    """Per-business, like the invoice sequences — each business is its own MIN."""
    owner2, _ = make_owner()
    other, _ = make_business(owner2)

    post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('500.00'))
    post(other, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('70.00'))

    assert sales_total(biz) == Decimal('500.00')
    assert sales_total(other) == Decimal('70.00')


# ── what does and doesn't earn an entry ─────────────────────────────────────

def test_a_zero_peso_sale_posts_nothing(biz):
    """Checkout renders these as "Free". No money moved, so no accountable event."""
    assert post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, Decimal('0')) is None
    assert AccumulatedGrandSalesEntry.objects.filter(business=biz).count() == 0


def test_a_source_with_no_date_is_refused_rather_than_guessed(biz):
    """A parked draft carries date=None. If one ever reached the odometer, stamping
    it with today would file money onto the wrong Z reading — silently, and in an
    append-only row that can never be corrected. Refuse loudly instead."""
    from Sales.models import Sale

    draft = Sale.objects.create(
        user=biz.user, business=biz, created_by=biz.user,
        status='pending', total_revenue=100, line_count=1,
    )
    assert draft.date is None, "fixture assumption broke — drafts should have no date"

    with pytest.raises(ValueError, match='refusing to guess'):
        AccumulatedGrandSalesEntry.post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE,
                             Decimal('100.00'), source=draft)

    assert sales_total(biz) == Decimal('0.00')


def test_the_source_reference_is_snapshotted(biz):
    """Stored as text, not resolved through the FK at read time — the Z reading has to
    be able to name the invoice even if the row behind it is ever unreachable."""
    product = make_product(biz, selling_price='125')
    sale = make_sale(biz, [(product, 2)])

    entry = AccumulatedGrandSalesEntry.post(
        biz, AccumulatedGrandSalesEntry.CHANNEL_SALE, sale.total_revenue,
        source=sale, ref='SI-0000000019',
    )

    assert entry.source_model == 'Sale'
    assert entry.source_id == sale.pk
    assert entry.source_ref == 'SI-0000000019'
    assert entry.amount == Decimal('250.00')


def test_the_entry_lands_on_the_sales_business_date(biz):
    """Not the wall clock. A sale rung after midnight that books to yesterday has to
    reach yesterday's Z reading, so the date comes off the sale, not from now()."""
    from datetime import date

    product = make_product(biz, selling_price='60')
    sale = make_sale(biz, [(product, 1)], date=date(2026, 3, 14))

    entry = AccumulatedGrandSalesEntry.post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE,
                                 sale.total_revenue, source=sale)

    assert entry.business_date == date(2026, 3, 14)


def test_six_decimal_money_is_rounded_to_centavos(biz):
    """Sale.total_revenue is decimal_places=6 (a quirk of the existing schema). The
    odometer is a peso figure that gets printed on a reading, so it stores 2."""
    entry = post(biz, AccumulatedGrandSalesEntry.CHANNEL_SALE,
                                 Decimal('99.999999'))

    assert entry.amount == Decimal('100.00')
    assert sales_total(biz) == Decimal('100.00')
