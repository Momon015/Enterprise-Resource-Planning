"""A refund must wear the same colour as the payment it reverses.

Both return lists hand-rolled their own chips and drifted: a CASH refund rendered amber
(`sl-badge-warning`) while the cash that paid for the sale rendered emerald on sale_list.
Same money, two colours, decided only by which list you were standing in.

The fix shares the `.pay-method` component, so these tests pin the two things that would
let it drift back — the colour slot, and the fact that a return's `credit` is a BALANCE
and must not borrow the payment pill's credit-CARD icon and wording.
"""
from decimal import Decimal

import pytest
from django.template import Context, Template
from django.urls import reverse

from tests.factories import make_business, make_product, make_purchase, make_sale


pytestmark = pytest.mark.django_db


def render(code):
    return Template(
        '{% load payment_tags %}{% refund_method_badge code %}'
    ).render(Context({'code': code}))


def test_a_cash_refund_uses_the_same_colour_slot_as_a_cash_payment():
    """The whole point. `.pay-method--cash` is emerald wherever it appears, so the refund
    and the payment it reverses finally read as the same kind of money."""
    out = render('cash')

    assert 'pay-method--cash' in out, "a cash refund left the shared cash colour slot"
    assert 'sl-badge-warning' not in out, "the old amber chip is back"
    assert 'bi-cash-stack' in out


def test_a_balance_refund_is_not_dressed_up_as_a_credit_card():
    """`credit` on a return means the customer's or supplier's outstanding BALANCE. It
    shares the payment pill's colour slot, but borrowing its card icon and the bare word
    'Credit' would tell the owner a card was involved when none was."""
    out = render('credit')

    assert 'pay-method--credit' in out, "the balance refund lost its colour slot"
    assert 'Balance' in out
    assert 'bi-credit-card' not in out, (
        "a balance refund is showing a credit-CARD icon — no card was ever used"
    )


def test_a_split_refund_reads_as_both():
    out = render('mixed')

    assert 'pay-method--mixed' in out
    assert 'Balance + cash' in out


def test_a_legacy_store_credit_row_still_renders_styled():
    """`store_credit` predates REFUND_METHOD_CHOICES (cash/credit/mixed). Falling through
    to `.pay-method--store_credit` would emit a class with no CSS behind it — an unstyled
    pill on old rows only, which is exactly the kind of thing nobody notices."""
    out = render('store_credit')

    assert 'pay-method--credit' in out
    assert 'pay-method--store_credit' not in out


def test_nothing_recorded_shows_a_dash_not_an_empty_pill():
    assert 'pay-method-empty' in render('')


@pytest.mark.parametrize('url_name', ['sales-return-list', 'purchase-return-list'])
def test_both_return_lists_still_render(client, owner, url_name):
    """Cheap guard on the `{% load payment_tags %}` both lists needed: a missing load is a
    TemplateSyntaxError at request time, and neither list had one before this change."""
    biz, _plan = make_business(owner, plan='pro')
    client.force_login(owner)

    resp = client.get(reverse(url_name, kwargs={'business_slug': biz.slug}))

    assert resp.status_code == 200


def test_a_sales_return_links_back_to_the_sale_it_reverses(client, owner):
    """A return is only ever read against its original, so the reference has to be
    reachable. The row itself is click-through to the RETURN, so this also pins the
    stopPropagation without which the link silently lands on the wrong page."""
    from Sales.models import SalesReturn

    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='100')
    sale = make_sale(biz, [(product, 1)])
    SalesReturn.objects.create(original_sale=sale, business=biz, date=sale.date,
                               refund_total=Decimal('40'), refund_cash=Decimal('40'))
    client.force_login(owner)

    body = client.get(reverse('sales-return-list',
                              kwargs={'business_slug': biz.slug})).content.decode()

    assert reverse('sale-detail', kwargs={
        'business_slug': biz.slug, 'sale_id': sale.id}) in body, (
        "the original sale isn't linked — the reference is a dead end again"
    )
    assert 'event.stopPropagation()' in body, (
        "without stopPropagation the row's own onclick wins and the link goes to the return"
    )
    assert 'pay-method--cash' in body, "the cash refund isn't wearing the shared cash colour"


def test_a_purchase_return_links_back_to_the_purchase_it_reverses(client, owner):
    """Twin of the sales-side test — the two lists drifted apart once already."""
    from Expense.models import PurchaseReturn

    biz, _plan = make_business(owner, plan='pro')
    purchase = make_purchase(biz, total_cost='500')
    PurchaseReturn.objects.create(original_purchase=purchase, business=biz,
                                  date=purchase.purchase_date,
                                  refund_total=Decimal('50'), refund_cash=Decimal('50'))
    client.force_login(owner)

    body = client.get(reverse('purchase-return-list',
                              kwargs={'business_slug': biz.slug})).content.decode()

    assert reverse('purchase-detail', kwargs={
        'business_slug': biz.slug, 'purchase_id': purchase.id}) in body
    assert 'event.stopPropagation()' in body
    assert 'pay-method--cash' in body
