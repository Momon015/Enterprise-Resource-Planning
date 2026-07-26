"""Analytics lazy-load split: an instant SHELL (KPI strip + skeleton) plus a deferred BODY that
carries the heavy cards. The point is that the page paints without waiting on the aggregates, so
the shell must NOT contain the charts, and the body must be reachable only the way htmx fetches
it (HX-Request header) — a bare visit bounces back to the page.
"""
import pytest
from django.urls import reverse

from tests.factories import make_business, make_product, make_sale


pytestmark = pytest.mark.django_db

HX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def shop(owner):
    biz, _plan = make_business(owner, plan='pro')
    return biz


def _shell(client, biz):
    return client.get(reverse('sales-analytics', kwargs={'business_slug': biz.slug})).content.decode()


def _body(client, biz, **extra):
    return client.get(reverse('sales-analytics-body', kwargs={'business_slug': biz.slug}), **extra)


# ── the shell ─────────────────────────────────────────────────────────────────

def test_shell_is_a_full_skeleton_with_no_real_data(client, shop, owner):
    make_sale(shop, [(make_product(shop, name='Coke', selling_price='100'), 3)])
    client.force_login(owner)
    html = _shell(client, shop)

    assert 'kpi-row' in html and 'skel' in html      # KPI + cards are ALL skeleton in the shell
    assert 'kpi-value' not in html                   # no real numbers yet — the body brings them
    assert 'hx-trigger="load"' in html               # ...which it fetches immediately
    assert 'sales/body' in html
    # the heavy stuff must NOT be in the shell — that's the whole point of deferring it
    assert 'trendChart' not in html
    assert 'Top Products' not in html


def test_shell_renders_even_with_no_sales(client, shop, owner):
    """An empty shop still gets the skeleton shell; the body resolves the empty state. The page
    must never block or 500 on a brand-new business."""
    client.force_login(owner)
    html = _shell(client, shop)
    assert 'skel' in html and 'kpi-row' in html      # full skeleton, even for an empty shop
    assert 'sales/body' in html


# ── the body ──────────────────────────────────────────────────────────────────

def test_body_returns_the_cards_on_hx(client, shop, owner):
    make_sale(shop, [(make_product(shop, name='Coke', selling_price='100'), 3)])
    client.force_login(owner)
    html = _body(client, shop, **HX).content.decode()

    assert 'kpi-value' in html                       # the KPI strip lives in the body now
    assert 'Collected' in html                       # ...including the Revenue dropdown's settle data
    assert 'trendChart' in html
    assert 'Top Products' in html
    assert '(function ()' in html, "chart init must be an IIFE — DOMContentLoaded won't fire on a swap"


def test_body_empty_state_when_no_sales(client, shop, owner):
    client.force_login(owner)
    html = _body(client, shop, **HX).content.decode()
    assert 'No sales in this period' in html
    assert 'trendChart' not in html                  # no charts drawn on an empty window


def test_body_bare_visit_bounces_to_the_page(client, shop, owner):
    """Without the HX-Request header the body is a naked fragment — bounce it to the full page
    so a hand-typed URL never shows chrome-less cards."""
    client.force_login(owner)
    resp = _body(client, shop)                        # no HX header
    assert resp.status_code == 302
    assert reverse('sales-analytics', kwargs={'business_slug': shop.slug}) in resp['Location']


# ── Profit Analytics gets the same split ──────────────────────────────────────

def _profit_shell(client, biz):
    return client.get(reverse('profit-analytics', kwargs={'business_slug': biz.slug})).content.decode()


def _profit_body(client, biz, **extra):
    return client.get(reverse('profit-analytics-body', kwargs={'business_slug': biz.slug}), **extra)


def test_profit_shell_has_kpi_and_skeleton_but_not_the_charts(client, shop, owner):
    make_sale(shop, [(make_product(shop, name='Coke', selling_price='100', cost_price='60'), 3)])
    client.force_login(owner)
    html = _profit_shell(client, shop)

    assert 'kpi-row' in html and 'skel' in html       # KPI + charts are ALL skeleton in the shell
    assert 'kpi-value' not in html                    # no real numbers until the body lands
    assert 'hx-trigger="load"' in html and 'profit/body' in html
    assert 'profitChart' not in html                  # every chart is deferred
    assert 'Profit by Product' not in html


def test_profit_body_returns_the_charts_on_hx(client, shop, owner):
    make_sale(shop, [(make_product(shop, name='Coke', selling_price='100', cost_price='60'), 3)])
    client.force_login(owner)
    html = _profit_body(client, shop, **HX).content.decode()

    assert 'kpi-value' in html                         # the KPI strip lives in the body now
    assert 'profitChart' in html and 'waterfallChart' in html
    assert 'Profit by Product' in html
    assert '(function ()' in html


def test_profit_body_bare_visit_bounces_to_the_page(client, shop, owner):
    client.force_login(owner)
    resp = _profit_body(client, shop)                 # no HX header
    assert resp.status_code == 302
    assert reverse('profit-analytics', kwargs={'business_slug': shop.slug}) in resp['Location']
