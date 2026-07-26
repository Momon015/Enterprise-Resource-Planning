"""Sales Analytics top-seller boards: goods / services / rentals split, and the "View all" modal.

The three boards must PARTITION the products — every sold line belongs to exactly one board, so
their revenue subtotals sum to the Revenue KPI with no overlap (rentals are the session-based
subset of services, the easy double-count). The Services/Rentals panel is conditional: both →
toggle, one → no toggle, neither → absent. And "View all" spills a >10 board into a searchable modal.
"""
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from Product.models import Product
from analytics.views import (_rank_products, _top_board, _sales_in, _returns_in,
                             TOP_SELLERS_N, SR_TOP_N)
from tests.factories import make_business, make_product, make_service, make_sale


pytestmark = pytest.mark.django_db


def _rental(biz, *, name, price='100'):
    return Product.objects.create(
        user=biz.user, business=biz, name=name,
        selling_price=Decimal(price), is_service=True, is_session_based=True,
        prepared_quantity=0,
    )


@pytest.fixture
def shop(owner):
    biz, _plan = make_business(owner, plan='pro')
    biz.offers_services = True
    biz.save()
    return biz


def _window(biz):
    today = timezone.localdate()
    return _sales_in(biz, today, today), _returns_in(biz, today, today)


# ── The split ────────────────────────────────────────────────────────────────

def test_boards_partition_products_by_type(shop):
    g = make_product(shop, name='Coke', selling_price='100')
    s = make_service(shop, name='Xerox', selling_price='5')
    r = _rental(shop, name='Videoke', price='50')
    make_sale(shop, [(g, 2), (s, 10), (r, 3)])   # goods 200 · service 50 · rental 150

    sales, returns = _window(shop)

    goods_names    = {row['product__name'] for row in _rank_products(sales, returns, 'goods')[0]}
    service_names  = {row['product__name'] for row in _rank_products(sales, returns, 'services')[0]}
    rental_names   = {row['product__name'] for row in _rank_products(sales, returns, 'rentals')[0]}

    assert goods_names == {'Coke'}
    assert service_names == {'Xerox'}, "a rental must NOT count as a plain service"
    assert rental_names == {'Videoke'}


def test_board_subtotals_sum_to_total_revenue(shop):
    g = make_product(shop, name='Coke', selling_price='100')
    s = make_service(shop, name='Xerox', selling_price='5')
    r = _rental(shop, name='Videoke', price='50')
    make_sale(shop, [(g, 2), (s, 10), (r, 3)])   # 200 + 50 + 150 = 400
    sales, returns = _window(shop)

    subtotal = sum(_rank_products(sales, returns, b)[1] for b in ('goods', 'services', 'rentals'))
    assert subtotal == Decimal('400')


# ── The conditional Services/Rentals panel ────────────────────────────────────

def _page(client, biz):
    # The cards (Top Products, Services/Rentals boards, View-all links) now stream from the
    # lazy body endpoint — the page shell carries only the KPI strip + a skeleton. So the board
    # assertions below run against the body, fetched the way htmx fetches it (HX-Request header).
    return client.get(reverse('sales-analytics-body', kwargs={'business_slug': biz.slug}),
                      HTTP_HX_REQUEST='true').content.decode()


def test_goods_only_shop_shows_no_services_panel(client, owner):
    biz, _plan = make_business(owner, plan='pro')      # offers_services defaults False
    make_sale(biz, [(make_product(biz, name='Coke'), 1)])
    client.force_login(owner)
    html = _page(client, biz)

    assert 'data-sr-panel="' not in html          # bare token appears in the page JS; the tag doesn't
    assert 'Top Services' not in html and 'Top Rentals' not in html


def test_services_only_shows_panel_without_toggle(client, shop, owner):
    make_sale(shop, [(make_service(shop, name='Xerox'), 3)])
    client.force_login(owner)
    html = _page(client, shop)

    assert 'Top Services' in html
    assert 'data-sr-panel="services"' in html
    assert 'data-sr-tab="' not in html, "no toggle buttons when there are no rentals"


def test_both_services_and_rentals_show_the_toggle(client, shop, owner):
    make_sale(shop, [(make_service(shop, name='Xerox'), 3),
                     (_rental(shop, name='Videoke'), 1)])
    client.force_login(owner)
    html = _page(client, shop)

    assert 'data-sr-tab="services"' in html and 'data-sr-tab="rentals"' in html
    assert 'data-sr-panel="services"' in html and 'data-sr-panel="rentals"' in html


# ── "View all" link + modal ───────────────────────────────────────────────────

def test_view_all_link_only_over_the_cap(client, shop, owner):
    items = [make_product(shop, name=f'P{i}', selling_price='10') for i in range(TOP_SELLERS_N + 1)]
    make_sale(shop, [(p, 1) for p in items])
    client.force_login(owner)
    html = _page(client, shop)

    assert f'View all {TOP_SELLERS_N + 1} products' in html
    assert 'kind=goods' in html


def test_view_all_absent_at_or_below_cap(client, shop, owner):
    items = [make_product(shop, name=f'P{i}', selling_price='10') for i in range(TOP_SELLERS_N)]
    make_sale(shop, [(p, 1) for p in items])
    client.force_login(owner)
    html = _page(client, shop)

    assert 'an-viewall' not in html          # the link class; "View all" also sits in the page JS comment


def test_services_board_caps_at_five_with_view_all(client, shop, owner):
    """SR boards use their own shorter cap (5) so the right column stays level with the left."""
    svcs = [make_service(shop, name=f'Svc{i}', selling_price='10') for i in range(SR_TOP_N + 1)]
    make_sale(shop, [(s, 1) for s in svcs])
    client.force_login(owner)
    html = _page(client, shop)

    assert f'View all {SR_TOP_N + 1}' in html
    assert 'kind=services' in html


def test_services_board_has_no_view_all_at_exactly_five(client, shop, owner):
    """The boundary: 'View all' shows only for MORE than 5 sold, never at 5 or fewer."""
    svcs = [make_service(shop, name=f'Svc{i}', selling_price='10') for i in range(SR_TOP_N)]
    make_sale(shop, [(s, 1) for s in svcs])
    client.force_login(owner)
    html = _page(client, shop)

    assert 'Top Services' in html           # the board itself is present
    assert 'an-viewall' not in html         # but no "View all" at exactly the cap


HX = {'HTTP_HX_REQUEST': 'true'}


def _modal(client, biz, kind):
    url = reverse('top-sellers-detail', kwargs={'business_slug': biz.slug})
    return client.get(url, {'kind': kind}, **HX)


def test_modal_lists_the_full_ranking_with_search(client, shop, owner):
    items = [make_product(shop, name=f'Item{i}', selling_price='10') for i in range(12)]
    make_sale(shop, [(p, 1) for p in items])
    client.force_login(owner)

    html = _modal(client, shop, 'goods').content.decode()

    assert 'data-tsm-search' in html                     # the filter box
    assert html.count('data-tsm-row') == 12              # ALL 12, not just the top 10
    assert 'Item7' in html and 'data-tsm-name="item7"' in html


def test_modal_scopes_to_its_board(client, shop, owner):
    make_sale(shop, [(make_service(shop, name='Xerox'), 3),
                     (_rental(shop, name='Videoke'), 1)])
    client.force_login(owner)

    html = _modal(client, shop, 'services').content.decode()
    assert 'Xerox' in html
    assert 'Videoke' not in html, "a rental must not appear on the services modal"


def test_modal_requires_the_hx_header(client, shop, owner):
    client.force_login(owner)
    resp = client.get(reverse('top-sellers-detail', kwargs={'business_slug': shop.slug}),
                      {'kind': 'goods'})
    assert resp.status_code == 302
    assert reverse('sales-analytics', kwargs={'business_slug': shop.slug}) in resp['Location']
