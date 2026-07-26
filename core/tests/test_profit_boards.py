"""Profit Analytics goods / services / rentals split.

The main "Profit by Product" table is goods only; Services and Rentals get their own slim
board below it (Units · Profit — no Cost/Margin-% columns because a service carries cost 0).
Same conditional rule as the Sales page: both → toggle, one → no toggle, neither → absent.
And because services have no COGS, their PROFIT must equal their revenue — the board would be
lying if a phantom cost crept in.
"""
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from Product.models import Product
from analytics.views import _profit_by_product, _sales_in, _returns_in, SR_TOP_N
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

def test_goods_board_excludes_services_and_rentals(shop):
    g = make_product(shop, name='Coke', selling_price='100', cost_price='60')
    s = make_service(shop, name='Xerox', selling_price='5')
    r = _rental(shop, name='Videoke', price='50')
    make_sale(shop, [(g, 2), (s, 10), (r, 3)])

    sales, returns = _window(shop)
    goods = {row['name'] for row in _profit_by_product(sales, returns, 'goods')}

    assert goods == {'Coke'}, "a service or rental must never land on the goods profit table"


def test_service_and_rental_boards_are_disjoint(shop):
    s = make_service(shop, name='Xerox', selling_price='5')
    r = _rental(shop, name='Videoke', price='50')
    make_sale(shop, [(s, 10), (r, 3)])

    sales, returns = _window(shop)
    svc = {row['name'] for row in _profit_by_product(sales, returns, 'services')}
    rent = {row['name'] for row in _profit_by_product(sales, returns, 'rentals')}

    assert svc == {'Xerox'}, "a rental must NOT count as a plain service"
    assert rent == {'Videoke'}


def test_service_profit_equals_revenue_no_phantom_cost(shop):
    s = make_service(shop, name='Xerox', selling_price='5')
    make_sale(shop, [(s, 10)])                      # revenue 50, cost 0

    sales, returns = _window(shop)
    row = _profit_by_product(sales, returns, 'services')[0]

    assert row['revenue'] == Decimal('50')
    assert row['cost'] == Decimal('0')
    assert row['margin'] == Decimal('50'), "no stock cost, so profit is the whole revenue"


def test_goods_profit_still_subtracts_cost(shop):
    g = make_product(shop, name='Coke', selling_price='100', cost_price='60')
    make_sale(shop, [(g, 2)])                        # revenue 200, cost 120

    sales, returns = _window(shop)
    row = _profit_by_product(sales, returns, 'goods')[0]

    assert row['margin'] == Decimal('80')           # 200 − 120


def test_board_caps_at_sr_top_n(shop):
    svcs = [make_service(shop, name=f'Svc{i}', selling_price='10') for i in range(SR_TOP_N + 3)]
    make_sale(shop, [(s, 1) for s in svcs])

    sales, returns = _window(shop)
    assert len(_profit_by_product(sales, returns, 'services', limit=SR_TOP_N)) == SR_TOP_N


# ── The conditional card ──────────────────────────────────────────────────────

def _page(client, biz):
    # The boards (Profit by Product, Services/Rentals) now stream from the lazy body endpoint —
    # the page shell carries only the KPI strip + a skeleton. Fetch the body the way htmx does.
    return client.get(reverse('profit-analytics-body', kwargs={'business_slug': biz.slug}),
                      HTTP_HX_REQUEST='true').content.decode()


def test_goods_only_shop_shows_no_sr_card(client, owner):
    biz, _plan = make_business(owner, plan='pro')       # offers_services defaults False
    make_sale(biz, [(make_product(biz, name='Coke'), 1)])
    client.force_login(owner)
    html = _page(client, biz)

    assert 'data-sr-panel="' not in html
    assert 'Profit by Service' not in html and 'Profit by Rental' not in html


def test_services_only_shows_board_without_toggle(client, shop, owner):
    make_sale(shop, [(make_service(shop, name='Xerox'), 3)])
    client.force_login(owner)
    html = _page(client, shop)

    assert 'Profit by Service' in html
    assert 'data-sr-panel="services"' in html
    assert 'data-sr-tab="' not in html, "no toggle buttons when there are no rentals"


def test_both_show_the_toggle(client, shop, owner):
    make_sale(shop, [(make_service(shop, name='Xerox'), 3),
                     (_rental(shop, name='Videoke'), 1)])
    client.force_login(owner)
    html = _page(client, shop)

    assert 'data-sr-tab="services"' in html and 'data-sr-tab="rentals"' in html
    assert 'data-sr-panel="services"' in html and 'data-sr-panel="rentals"' in html
    assert 'Services &amp; Rentals' in html


def test_rental_appears_on_rentals_panel_not_services(client, shop, owner):
    make_sale(shop, [(make_service(shop, name='Xerox'), 3),
                     (_rental(shop, name='Videoke'), 1)])
    client.force_login(owner)
    html = _page(client, shop)

    services_panel = html.split('data-sr-panel="services"')[1].split('data-sr-panel="rentals"')[0]
    assert 'Videoke' not in services_panel, "a rental must not render inside the services panel"
