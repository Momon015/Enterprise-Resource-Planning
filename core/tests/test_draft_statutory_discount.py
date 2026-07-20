"""A parked draft must remember WHO it was for.

GCash and bank payments park the sale as `pending` until the money is verified, so the
statutory discount has to survive that round trip. If it doesn't, a PWD's sale confirms
the next day as an ordinary one: the 20% silently disappears, the ID vanishes from the
invoice, and the customer was charged the wrong amount.

Worth testing rather than reasoning about, because this path already drops things by
design — `pending_method`, `pending_status`, `pending_amount` and `pending_note` all
exist precisely because the payment intent does NOT survive parking on its own. The
discount fields happen to be written before the pending branch, so they should persist;
"should" is what this file replaces.

Driven through the real checkout and the real draft-confirm view, not by building rows.
"""
from decimal import Decimal

import pytest
from django.urls import reverse

from Sales.models import Sale
from activity.models import AccumulatedGrandSalesEntry
from tests.factories import make_business, make_product


pytestmark = pytest.mark.django_db


@pytest.fixture
def cart_with_a_pwd_customer(client, owner):
    """A ₱50 item in the cart, customer flagged PWD, at a non-VAT seller.

    Non-VAT because that is the common case: ₱50 → 20% off → ₱40, no VAT anywhere.
    """
    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='50', stock=10)
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '30',
                                         'selling_price': '50'}}
    session['sale_discount_type'] = 'pwd'
    session['sale_discount_id_no'] = 'RR-0123-456-7890123'
    session['sale_discount_name'] = 'Maria Santos'
    session.save()
    return biz, product


def test_a_gcash_draft_keeps_the_pwd_discount_while_parked(client, cart_with_a_pwd_customer):
    """Park it. The draft must already hold the discount, the ID and the reduced total —
    not wait until confirmation to work them out."""
    biz, _product = cart_with_a_pwd_customer

    client.post(reverse('sale-confirm-summary', kwargs={'business_slug': biz.slug}), {
        'payment_status': 'full', 'payment_method': 'gcash', 'sale_status': 'pending',
    })

    draft = Sale.objects.get(business=biz)
    assert draft.status == 'pending'
    assert draft.discount_type == Sale.DISCOUNT_PWD, "the draft forgot who it was for"
    assert draft.discount_id_no == 'RR-0123-456-7890123'
    assert draft.discount_name == 'Maria Santos'
    assert draft.discount_percent == Decimal('20.00')
    assert draft.discount_amount == Decimal('10.00')
    assert draft.total_revenue == Decimal('40.00')
    # a draft holds no accountable number yet
    assert draft.reference is None
    assert draft.date is None


def test_confirming_the_draft_preserves_the_discount_and_issues_the_invoice(
        client, cart_with_a_pwd_customer):
    """IMPORTANT: The round trip. Confirmation flips status, stamps the date and the SI number, and
    posts to the odometer — none of which may disturb the discount already agreed with
    the customer."""
    biz, _product = cart_with_a_pwd_customer

    client.post(reverse('sale-confirm-summary', kwargs={'business_slug': biz.slug}), {
        'payment_status': 'full', 'payment_method': 'gcash', 'sale_status': 'pending',
    })
    draft = Sale.objects.get(business=biz)

    client.post(reverse('sale-draft-confirm', kwargs={
        'business_slug': biz.slug, 'sale_id': draft.id}))
    draft.refresh_from_db()

    assert draft.status == 'completed'
    assert draft.discount_type == Sale.DISCOUNT_PWD, (
        "confirmation dropped the PWD discount — the customer was charged as regular"
    )
    assert draft.discount_id_no == 'RR-0123-456-7890123'
    assert draft.total_revenue == Decimal('40.00'), "the 20% vanished on confirmation"
    assert draft.reference, "confirmation must issue the SI number"
    assert draft.date is not None


def test_the_odometer_gets_the_gross_not_the_discounted_total(client,
                                                              cart_with_a_pwd_customer):
    """BIR's accumulated grand total is GROSS — ₱50, not the ₱40 the customer paid. The
    discount is reported separately on the Z reading, so posting the net here would
    under-report every discounted sale forever."""
    biz, _product = cart_with_a_pwd_customer

    client.post(reverse('sale-confirm-summary', kwargs={'business_slug': biz.slug}), {
        'payment_status': 'full', 'payment_method': 'gcash', 'sale_status': 'pending',
    })
    draft = Sale.objects.get(business=biz)

    assert AccumulatedGrandSalesEntry.total_for(biz) == Decimal('0.00'), (
        "a parked draft posted to the odometer — only real sales may"
    )

    client.post(reverse('sale-draft-confirm', kwargs={
        'business_slug': biz.slug, 'sale_id': draft.id}))

    assert AccumulatedGrandSalesEntry.total_for(biz) == Decimal('50.00'), (
        "the odometer took the discounted total instead of the gross"
    )


def test_the_parked_payment_method_survives_alongside_the_discount(
        client, cart_with_a_pwd_customer):
    """The two mechanisms have to coexist: the payment intent is stashed in pending_*
    fields and consumed on confirm, while the discount rides on the row itself."""
    biz, _product = cart_with_a_pwd_customer

    client.post(reverse('sale-confirm-summary', kwargs={'business_slug': biz.slug}), {
        'payment_status': 'full', 'payment_method': 'gcash', 'sale_status': 'pending',
    })

    draft = Sale.objects.get(business=biz)
    assert draft.pending_method == 'gcash'
    assert draft.discount_type == Sale.DISCOUNT_PWD

    client.post(reverse('sale-draft-confirm', kwargs={
        'business_slug': biz.slug, 'sale_id': draft.id}))
    draft.refresh_from_db()

    # the payment landed for the DISCOUNTED amount, not the sticker price
    assert draft.amount_paid == Decimal('40.00')
    assert draft.payments.first().method == 'gcash'
