"""A draft is not a sale, on every surface that counts sales.

The invariant: a sale is real only when it is neither voided NOR a draft.
SaleQuerySet.active() (`is_void=False, status='completed'`) is where that rule lives,
and its docstring says to use it for ALL revenue/count aggregations.

The trap is that a query reaching across the FK from SaleItem can't inherit the
manager — it has to spell the rule out — and `sale__is_void=False` on its own LOOKS
complete. It isn't, and it silently counts pending + canceled drafts as sales.

This actually shipped: product_detail reported 94 units / P4,690 for a product that
Sales Analytics (built on Sale.objects.active()) reported as 93 / P4,640 — a single
canceled draft of 1 unit, counted by one page and not the other. Every filter in
product_detail predated the 07-11 draft feature and was never revisited; the Dashboard's
away banner had the same hole, counting a draft's ITEMS beside a sale count that excluded
it. Two pages disagreeing about the same product is the bug users report; the money being
wrong is the bug that matters.
"""
import pytest
from django.urls import reverse

from tests.factories import make_business, make_product, make_sale


@pytest.fixture
def business(owner):
    biz, _plan = make_business(owner)
    return biz


@pytest.fixture
def product(business):
    return make_product(business, selling_price='50', cost_price='30')


def detail_context(client, owner, product):
    """Drive the real view — the point is what product_detail PUTS ON THE PAGE."""
    client.force_login(owner)
    response = client.get(reverse('product-detail', kwargs={
        'business_slug': product.business.slug,
        'product_slug': product.slug,
        'product_id': product.id,
    }))
    assert response.status_code == 200
    return response.context


@pytest.mark.parametrize('draft_status', ['pending', 'canceled'])
def test_a_draft_sale_is_not_counted_as_units_sold(client, owner, business, product,
                                                   draft_status):
    make_sale(business, [(product, 10)])
    make_sale(business, [(product, 3)], status=draft_status)

    ctx = detail_context(client, owner, product)

    assert ctx['units_sold_all'] == 10
    assert ctx['units_sold_30d'] == 10


def test_a_draft_sale_does_not_add_to_total_sales_value(client, owner, business, product):
    make_sale(business, [(product, 10)])          # 10 x P50 = P500
    make_sale(business, [(product, 3)], status='canceled')   # never happened

    ctx = detail_context(client, owner, product)

    assert ctx['total_sales_value'] == 500


def test_a_product_sold_only_on_a_draft_has_never_sold(client, owner, business, product):
    """The whole-number version of the bug: 0 must not read as 3.

    A never-sold product is a restock decision (and a `?velocity=never` filter row),
    so a draft inflating it from nothing to something is the worst shape of this.
    """
    make_sale(business, [(product, 3)], status='pending')

    ctx = detail_context(client, owner, product)

    assert ctx['units_sold_all'] == 0
    assert ctx['total_sales_value'] == 0
    assert ctx['last_sold'] is None
