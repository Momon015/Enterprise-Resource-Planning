"""Cash tendered → change due.

The customer hands over ₱1000 for a ₱474 sale and gets ₱526 back. Three things have to
stay true at once, and they pull against each other:

  1. the receipt reports ₱1000 in and ₱526 out, because that is what happened;
  2. the SALE is still ₱474, because that is what was sold;
  3. the DRAWER gained ₱474, not ₱1000, because the change left the till.

(3) is the one that costs real money. If a tender ever reached `expected_cash`, every
cash sale would open a fake overage the size of the change, and a cashier skimming the
till would be invisible inside it. `Shift.expected_cash` sums `SalesPayment.amount`, so
keeping the tender in its own column makes this true by construction — this file pins
that, because "by construction" is only true until somebody adds a column to the sum.
"""
import re
from decimal import Decimal

import pytest
from django.urls import reverse

from Sales.models import Sale, SalesPayment
from tests.factories import make_business, make_product

pytestmark = pytest.mark.django_db


@pytest.fixture
def cart_474(client, owner):
    """A ₱474 sale sitting in the cart, ready to check out."""
    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='474', stock=10)
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '300',
                                         'selling_price': '474'}}
    session.save()
    return biz, product


def checkout(client, biz, **extra):
    payload = {'payment_status': 'full', 'payment_method': 'cash'}
    payload.update(extra)
    return client.post(
        reverse('sale-confirm-summary', kwargs={'business_slug': biz.slug}), payload)


def test_the_owners_example_1000_for_a_474_sale_gives_526_change(client, cart_474):
    """The exact case from the original ask."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='1000')

    sale = Sale.objects.get(business=biz)
    assert sale.cash_tendered == Decimal('1000.00')
    assert sale.cash_change == Decimal('526.00')
    assert sale.total_revenue == Decimal('474.00'), "the tender changed the sale value"


def test_the_tender_never_reaches_the_drawer(client, cart_474):
    """IMPORTANT: The expensive one. The payment row carries ₱474 — the sale — while the ₱1000
    lives in its own column. Anything that sums `amount` therefore sees ₱474."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='1000')

    payment = SalesPayment.objects.get(sale__business=biz)
    assert payment.amount == Decimal('474.00'), (
        "the tender was banked as the payment — the drawer now expects ₱526 too much, "
        "which is exactly the shape of a skim nobody would notice"
    )
    assert payment.tendered == Decimal('1000.00')
    assert payment.change_due == Decimal('526.00')


def test_exact_change_records_the_tender_but_no_change(client, cart_474):
    """Handing over exactly ₱474 is a real tender with zero change. Recorded, because
    the cashier typed it and the receipt should say so."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='474')

    sale = Sale.objects.get(business=biz)
    assert sale.cash_tendered == Decimal('474.00')
    assert sale.cash_change == Decimal('0.00')


def test_no_tender_typed_leaves_it_null(client, cart_474):
    """The field is optional. NULL, not zero — the receipt prints nothing rather than
    'CASH 0.00', and the two states have to stay distinguishable."""
    biz, _ = cart_474
    checkout(client, biz)

    sale = Sale.objects.get(business=biz)
    assert sale.cash_tendered is None
    assert sale.cash_change is None
    assert SalesPayment.objects.get(sale=sale).tendered is None


def test_a_short_tender_is_ignored_not_recorded_as_negative_change(client, cart_474):
    """₱400 against a ₱474 sale isn't change, it's a shortfall — which the partial and
    utang paths already model. Dropped silently: a fat-fingered tender must never cost
    the cashier the sale."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='400')

    sale = Sale.objects.get(business=biz)
    assert sale.cash_tendered is None, "a short tender was stored anyway"
    assert sale.total_revenue == Decimal('474.00')
    assert sale.is_fully_paid, "the sale itself must be unaffected"


def test_gcash_never_carries_a_tender(client, cart_474):
    """E-payments are exact. Even a hand-crafted POST carrying a tender must not
    produce a CHANGE line on a GCash receipt."""
    biz, _ = cart_474
    checkout(client, biz, payment_method='gcash', cash_tendered='1000')

    sale = Sale.objects.get(business=biz)
    assert sale.cash_tendered is None


def test_switching_a_payment_off_cash_clears_a_stale_tender(client, cart_474):
    """`save()` clears it on the way past, so a method change can't strand a figure
    that would then print change against a bank transfer."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='1000')

    payment = SalesPayment.objects.get(sale__business=biz)
    payment.method = 'bank'
    payment.save()
    payment.refresh_from_db()

    assert payment.tendered is None
    assert payment.change_due is None


def test_the_clear_survives_a_narrow_update_fields(client, cart_474):
    """IMPORTANT: The trap that bit Sale.save(): a caller passing update_fields that omits
    'tendered' would clear it in memory only, leaving the stale value in the database
    to print on the next receipt."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='1000')

    payment = SalesPayment.objects.get(sale__business=biz)
    payment.method = 'gcash'
    payment.save(update_fields=['method'])

    payment.refresh_from_db()
    assert payment.tendered is None, (
        "tendered was cleared in memory but never written — the receipt still prints "
        "change on a GCash sale"
    )


def test_a_partial_payment_measures_change_against_what_was_paid(client, cart_474):
    """₱500 handed over for a ₱200 down payment: ₱300 change AND ₱274 still owed. Two
    different numbers that must not be confused for each other."""
    biz, _ = cart_474
    checkout(client, biz, payment_status='partial', amount_paid='200',
             cash_tendered='500')

    sale = Sale.objects.get(business=biz)
    assert sale.amount_paid == Decimal('200.00')
    assert sale.cash_change == Decimal('300.00'), "change was measured against the total"
    assert sale.outstanding == Decimal('274.00')


def test_the_receipt_prints_cash_and_change(client, cart_474):
    """Both rows or neither — CASH alone reads like a second total."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='1000')
    sale = Sale.objects.get(business=biz)

    html = client.get(reverse('sale-receipt',
                              kwargs={'business_slug': biz.slug,
                                      'sale_id': sale.id})).content.decode()

    assert 'CASH' in html and 'CHANGE' in html
    assert '1000.00' in html or '1,000.00' in html
    assert '526.00' in html


def test_the_checkout_screen_actually_renders_the_input(client, cart_474):
    """IMPORTANT: Rendered, not grepped. Twice now a field has been 'added' to a form that the
    cashier never saw, because the template building the inputs by hand was a different
    file from the one I edited. If this passes, the box is on the screen."""
    biz, _ = cart_474
    html = client.get(reverse('view-session-summary',
                              kwargs={'business_slug': biz.slug})).content.decode()

    assert 'name="cash_tendered"' in html, "the input never reached the checkout screen"
    assert 'change_due_value' in html, "no change readout"
    assert 'SALE_TOTAL = 474' in html, (
        "the total handed to the change maths is missing or comma-formatted — "
        "parseFloat stops at a comma, so ₱1,250.00 would silently become 1"
    )


def test_the_reference_field_is_wired_to_hide_for_cash(client, cart_474):
    """Reference is an e-payment thing — cash has no reference number, and on Debt no
    SalesPayment row exists to hang the note on, so it was being silently discarded.

    Asserts the wiring is present, not the final visibility: the show/hide is JS, which
    a Django test client never runs. The real check is opening the page.
    """
    biz, _ = cart_474
    html = client.get(reverse('view-session-summary',
                              kwargs={'business_slug': biz.slug})).content.decode()

    assert 'payment_note_group' in html, "the note field has no handle to hide it by"
    assert "method === 'gcash'" in html, "nothing gates the note on the method"


def test_a_note_sent_with_cash_anyway_does_not_break_the_sale(client, cart_474):
    """The hiding is client-side, so a hand-crafted POST can still carry one. It should
    just ride along harmlessly rather than error — not worth a server guard, since an
    unwanted note is cosmetic where a rejected sale is not."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='1000', payment_note='typed anyway')

    sale = Sale.objects.get(business=biz)
    assert sale.is_fully_paid
    assert sale.cash_change == Decimal('526.00')


def test_the_quick_tender_buttons_render_and_cannot_submit_the_form(client, cart_474):
    """IMPORTANT: type="button" is load-bearing. A bare <button> inside a form defaults to
    submit, so every tap on a denomination would post the sale — and the cashier would
    discover it only after the stock moved and the invoice number burned."""
    biz, _ = cart_474
    html = client.get(reverse('view-session-summary',
                              kwargs={'business_slug': biz.slug})).content.decode()

    for note in ('100', '200', '500', '1000'):
        assert f'data-tender="{note}"' in html, f"the ₱{note} button is missing"

    tags = re.findall(r'<button[^>]*\btender-quick\b[^>]*>', html)
    assert len(tags) == 4, f"expected 4 quick-tender buttons, found {len(tags)}"
    for tag in tags:
        assert 'type="button"' in tag, (
            "a quick-tender button can submit the form — tapping ₱500 would post the sale"
        )


def test_the_post_checkout_screen_shows_the_change(client, cart_474):
    """IMPORTANT: This screen matters more than the receipt: it is the moment the cashier counts
    money back into the customer's hand, and the receipt may never be printed."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='1000')
    sale = Sale.objects.get(business=biz)

    html = client.get(reverse('sale-summary',
                              kwargs={'business_slug': biz.slug,
                                      'sale_id': sale.id})).content.decode()

    assert 'Cash received' in html
    assert 'Change' in html
    assert '526.00' in html, "the cashier can't see what to hand back"


def test_the_sale_detail_shows_the_change_later(client, cart_474):
    """Historical record — a sale looked up next week still explains its own cash."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='1000')
    sale = Sale.objects.get(business=biz)

    html = client.get(reverse('sale-detail',
                              kwargs={'business_slug': biz.slug,
                                      'sale_id': sale.id})).content.decode()

    assert 'Cash Received' in html
    assert '526.00' in html


def test_neither_screen_invents_a_change_row_when_no_tender_was_typed(client, cart_474):
    """The field is optional, so most sales have none. Printing 'Change ₱0.00' on every
    one of them would bury the rows that mean something."""
    biz, _ = cart_474
    checkout(client, biz)
    sale = Sale.objects.get(business=biz)

    for name in ('sale-summary', 'sale-detail'):
        html = client.get(reverse(name, kwargs={'business_slug': biz.slug,
                                                'sale_id': sale.id})).content.decode()
        assert 'Cash received' not in html and 'Cash Received' not in html, (
            f"{name} shows a cash row for a sale with no recorded tender"
        )


def test_a_reprint_still_shows_the_change(client, cart_474):
    """IMPORTANT: Why the tender is persisted rather than computed at checkout. A reprint that
    silently drops CHANGE is a different document from the one the customer was handed."""
    biz, _ = cart_474
    checkout(client, biz, cash_tendered='1000')
    sale = Sale.objects.get(business=biz)

    url = reverse('sale-receipt', kwargs={'business_slug': biz.slug, 'sale_id': sale.id})
    first = client.get(url).content.decode()
    second = client.get(url).content.decode()

    assert '526.00' in first and '526.00' in second
