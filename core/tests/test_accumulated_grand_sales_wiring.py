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

from Sales.models import Sale
from activity.models import AccumulatedGrandSalesEntry
from tests.factories import make_business, make_product, make_sale


pytestmark = pytest.mark.django_db


@pytest.fixture
def sale_to_void(client, owner):
    """A completed sale, its business, and a logged-in owner who can still void it.

    can_void_sale() gates on the drawer, not the calendar — a business with no shift
    today has nothing to disturb, so a same-day sale stays voidable.

    is_bir_active=True because the odometer is official-mode machinery: an internal
    business (the default) records nothing, so a wiring test measuring the odometer
    has to run in the mode where the odometer exists.
    """
    biz, _plan = make_business(owner, plan='pro')
    biz.is_bir_active = True
    biz.save(update_fields=['is_bir_active'])
    product = make_product(biz, selling_price='125')
    sale = make_sale(biz, [(product, 2)])       # 250.00
    client.force_login(owner)
    return biz, sale


def test_an_internal_business_records_nothing_to_the_odometer(client, owner):
    """The mirror of the wiring tests: in internal mode (is_bir_active=False, the default)
    the odometer is not just correct, it is SILENT. No sale, void or return may post.

    This is what lets an SI-/Z run begin cleanly the day a business is accredited — with
    an internal history behind it, there is no accumulated grand total to reconcile or
    backfill, because none was ever kept. A void here is the sharpest case: it must not
    post a reversal to a channel whose matching sale was never recorded.
    """
    biz, _plan = make_business(owner, plan='pro')     # is_bir_active defaults to False
    assert biz.is_bir_active is False, "fixture drifted — this test needs internal mode"
    product = make_product(biz, selling_price='125')
    sale = make_sale(biz, [(product, 2)])
    client.force_login(owner)

    client.post(
        reverse('void-sale', kwargs={'business_slug': biz.slug, 'sale_id': sale.id}),
        {'void_reason': 'wrong_item', 'action': 'void'},
    )
    sale.refresh_from_db()
    assert sale.is_void is True, "the void didn't happen — test is measuring nothing"

    assert AccumulatedGrandSalesEntry.objects.filter(business=biz).count() == 0, (
        "an internal-mode business posted to the BIR odometer — the accreditation-day "
        "fresh start depends on internal history leaving no accumulated total behind"
    )


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


def test_the_odometer_records_gross_not_net_of_discount(client, owner):
    """RMO 24-2023 Annex D-2 prints "Present Accumulated Sales" and "Gross Amount" as
    the SAME figure, then deducts discount FROM it. So the odometer tracks gross and
    the Z reading nets down afterwards.

    Posting total_revenue instead would deduct the discount twice — once when the
    sale is recorded, and again on the reading's "Less Discount" line.

    Driven through the VOID view rather than by calling post() directly. Passing
    sale.subtotal in by hand would only prove that subtotal is 1000 — it would pass
    just as happily if the hook still used total_revenue. The void hook shares the
    sales hook's basis, so this is the one gross/net path reachable end-to-end
    without building a session cart.
    """
    biz, _plan = make_business(owner, plan='pro')
    biz.is_bir_active = True                     # odometer only runs in official mode
    biz.save(update_fields=['is_bir_active'])
    product = make_product(biz, selling_price='100')
    sale = make_sale(biz, [(product, 10)], discount_percent=20)   # 1000 gross, 200 off
    client.force_login(owner)

    assert sale.total_revenue == Decimal('800.000000'), "fixture isn't discounted"
    assert sale.subtotal == Decimal('1000.000000')

    client.post(
        reverse('void-sale', kwargs={'business_slug': biz.slug, 'sale_id': sale.id}),
        {'void_reason': 'wrong_item', 'action': 'void'},
    )

    assert AccumulatedGrandSalesEntry.total_for(
        biz, AccumulatedGrandSalesEntry.CHANNEL_VOID) == Decimal('1000.00'), (
        "the hook posted the NET figure — the discount would be deducted twice on "
        "the Z reading, once here and again on its 'Less Discount' line"
    )


def test_a_void_is_issued_its_own_document_number(client, sale_to_void):
    """Voids are numbered, not just flagged.

    Annex D-2 prints "Beg. VOID #" / "End. VOID #" beside the SI and RETURN runs, and
    p.4(k) classes void papers as supplementary invoices. The number comes from a
    series SEPARATE to SI — voiding does not consume a sales invoice number, it issues
    a different kind of document about one.
    """
    biz, sale = sale_to_void
    assert sale.void_reference is None, "an unvoided sale must hold no void number"
    sale_invoice = sale.reference

    client.post(
        reverse('void-sale', kwargs={'business_slug': biz.slug, 'sale_id': sale.id}),
        {'void_reason': 'wrong_item', 'action': 'void'},
    )
    sale.refresh_from_db()

    assert sale.void_reference == 'VD-0000000001'
    assert sale.reference == sale_invoice, "voiding must not disturb the SI number"


def test_void_numbers_run_independently_of_invoice_numbers(client, owner):
    """Two series, two runs. Three sales and one void must leave SI at 3 and VD at 1 —
    if they shared a counter the void would have burned an invoice number."""
    biz, _plan = make_business(owner, plan='pro')
    biz.is_bir_active = True   # the SI- accountable series only runs in official (BIR) mode
    biz.save(update_fields=['is_bir_active'])
    product = make_product(biz, selling_price='50')
    sales = [make_sale(biz, [(product, 1)]) for _ in range(3)]
    client.force_login(owner)

    client.post(
        reverse('void-sale', kwargs={'business_slug': biz.slug, 'sale_id': sales[1].id}),
        {'void_reason': 'wrong_item', 'action': 'void'},
    )
    sales[1].refresh_from_db()

    assert [s.reference for s in Sale.objects.filter(business=biz).order_by('id')] == [
        'SI-0000000001', 'SI-0000000002', 'SI-0000000003',
    ]
    assert sales[1].void_reference == 'VD-0000000001'


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
