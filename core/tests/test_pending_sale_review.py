"""A parked sale must be SHOWN before it can be posted.

Confirm and Cancel used to sit on the pending row, so a sale parked days earlier was
confirmed off a reference, a line count and a total — nobody could tell whether it was
the sale the payment actually belonged to. Both buttons now live at the bottom of a
review modal, which is the only way to reach them.

That makes the modal load-bearing rather than decorative, so these tests cover the two
things that would quietly break it: the row must still be able to open it, and the
staff scoping on the list must not stop at the list.
"""
import pytest
from django.urls import reverse
from django.utils import timezone

from Sales.models import Sale
from tests.factories import make_business, make_product, make_staff


pytestmark = pytest.mark.django_db

HX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def parked_sale(client, owner):
    """One pending GCash sale with a PWD discount, rung up through the real checkout."""
    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='50', stock=10)
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 2, 'cost_price': '30',
                                         'selling_price': '50'}}
    session['sale_discount_type'] = 'pwd'
    session['sale_discount_id_no'] = 'RR-0123-456-7890123'
    session['sale_discount_name'] = 'Maria Santos'
    session.save()

    client.post(reverse('sale-confirm-summary', kwargs={'business_slug': biz.slug}), {
        'payment_status': 'full', 'payment_method': 'gcash', 'sale_status': 'pending',
    })
    return biz, Sale.objects.get(business=biz), product


def _review(client, biz, sale, **extra):
    return client.get(reverse('sale-draft-review', kwargs={
        'business_slug': biz.slug, 'sale_id': sale.id}), **extra)


def test_the_modal_shows_the_lines_the_total_and_the_statutory_id(client, parked_sale):
    """The whole reason the screen exists: what am I about to post, and who for."""
    biz, sale, product = parked_sale

    resp = _review(client, biz, sale, **HX)
    body = resp.content.decode()

    assert resp.status_code == 200
    assert product.name in body, "the review modal didn't list what's in the sale"
    assert '80.00' in body, "the discounted total is missing"          # 100 − 20%
    assert 'RR-0123-456-7890123' in body, (
        "the statutory ID isn't shown — a discount granted against the wrong ID is exactly "
        "what this step is for, and after confirmation the row locks"
    )
    assert 'Maria Santos' in body


def test_the_pending_row_can_open_it(client, parked_sale):
    """The row is the ONLY entry point now, so a broken link means a queue nobody can
    action at all — not merely a missing convenience."""
    biz, sale, _product = parked_sale

    resp = client.get(reverse('sale-draft-list', kwargs={'business_slug': biz.slug}))
    body = resp.content.decode()
    review_url = reverse('sale-draft-review',
                         kwargs={'business_slug': biz.slug, 'sale_id': sale.id})

    assert review_url in body, "the pending row lost its way into the review modal"


def test_confirm_and_cancel_are_not_reachable_from_the_row(client, parked_sale):
    """The point of the change. If either button creeps back onto the list, blind
    confirmation is back and this whole feature is decorative."""
    biz, sale, _product = parked_sale

    body = client.get(reverse('sale-draft-list',
                              kwargs={'business_slug': biz.slug})).content.decode()

    assert reverse('sale-draft-confirm', kwargs={
        'business_slug': biz.slug, 'sale_id': sale.id}) not in body, (
        "Confirm is back on the row — a parked sale can be posted without being read"
    )
    assert reverse('sale-draft-cancel', kwargs={
        'business_slug': biz.slug, 'sale_id': sale.id}) not in body


def test_a_direct_visit_bounces_to_the_list(client, parked_sale):
    """Without the htmx header this renders a bare partial with no page around it, so
    the view redirects instead of painting an unstyled fragment."""
    biz, sale, _product = parked_sale

    resp = _review(client, biz, sale)          # no HX header

    assert resp.status_code == 302
    assert resp.url == reverse('sale-draft-list', kwargs={'business_slug': biz.slug})


def test_a_completed_sale_has_nothing_to_review(client, parked_sale):
    """Once confirmed the sale belongs to the record, not the verify queue — and its
    Confirm button must not still be live in a stale tab."""
    biz, sale, _product = parked_sale
    client.post(reverse('sale-draft-confirm', kwargs={
        'business_slug': biz.slug, 'sale_id': sale.id}))

    assert _review(client, biz, sale, **HX).status_code == 404


def test_a_canceled_sale_detail_shows_no_nulls(client, parked_sale):
    """A canceled draft never got a reference or a transaction date — both are stamped at
    confirmation. The detail page printed the literal string "None" in its breadcrumb and
    left the masthead as a bare "· 08:57 AM" with no day against it."""
    biz, sale, _product = parked_sale
    client.post(reverse('sale-draft-cancel', kwargs={
        'business_slug': biz.slug, 'sale_id': sale.id}), {'cancel_reason': 'no_payment'})
    sale.refresh_from_db()
    assert sale.status == 'canceled' and sale.reference is None and sale.date is None

    body = client.get(reverse('sale-detail', kwargs={
        'business_slug': biz.slug, 'sale_id': sale.id})).content.decode()

    assert '>None<' not in body, "the breadcrumb is printing the literal string 'None'"
    assert sale.created_at.astimezone(timezone.get_current_timezone()).strftime('%Y') in body, (
        "the masthead has no date at all — sale.date is null on a draft, so it must fall "
        "back to when the sale was rung up"
    )


def test_staff_cannot_review_someone_elses_parked_sale(client, parked_sale):
    """The list is scoped with `filter_to_own_if_staff`; the modal has to be scoped the
    same way or the restriction is one edited URL deep. The sale below belongs to the
    OWNER, so a restricted staff member must not be able to read it — let alone reach
    the Confirm button at the bottom of it."""
    biz, sale, _product = parked_sale
    staff, _employee = make_staff(biz)          # staff are two rows: User + Employee

    client.force_login(staff)
    resp = _review(client, biz, sale, **HX)

    assert resp.status_code in (302, 404), (
        "staff read the owner's parked sale by id — the modal skipped the scoping the "
        "list applies, so Confirm was reachable for a sale they can't even see listed"
    )
