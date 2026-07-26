"""Expense Analytics → Supplier Spend panel.

Ranks vendors by stock spend in the chosen window, each with the date you LAST ordered from
them. Two behaviours are load-bearing and easy to get subtly wrong:
  • Spend is window-scoped (it must move with the period chips).
  • `last_order` is ALL-TIME, not window-clipped — otherwise it degenerates to "the window's
    last day" and stops meaning anything.
"""
from decimal import Decimal
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from Expense.models import PurchaseItem
from Supplier.models import Material
from analytics.views import _supplier_spend
from tests.factories import make_business, make_purchase, make_supplier


pytestmark = pytest.mark.django_db


def _material(biz, supplier, *, name):
    return Material.objects.create(
        user=biz.user, business=biz, name=name,
        price=Decimal('10'), quantity=1, unit='pc', supplier=supplier,
    )


def _line(purchase, material, *, price, qty, discount='0'):
    return PurchaseItem.objects.create(
        purchase=purchase, material=material,
        price=Decimal(str(price)), quantity=qty, discount=Decimal(str(discount)),
    )


@pytest.fixture
def shop(owner):
    biz, _plan = make_business(owner, plan='pro')
    return biz


def test_ranks_suppliers_by_window_spend_with_orders_and_share(shop):
    today = timezone.localdate()
    nestle = make_supplier(shop, name='Nestle')
    cocacola = make_supplier(shop, name='Coca-Cola')
    m_n = _material(shop, nestle, name='Bear Brand')
    m_c = _material(shop, cocacola, name='Coke 1.5L')

    p1 = make_purchase(shop, date=today)
    _line(p1, m_n, price='10', qty=5)     # Nestle 50
    _line(p1, m_c, price='20', qty=1)     # Coca-Cola 20
    p2 = make_purchase(shop, date=today)
    _line(p2, m_n, price='10', qty=3)     # Nestle +30  → 80

    rows = _supplier_spend(shop, today, today)

    assert [r['label'] for r in rows] == ['Nestle', 'Coca-Cola']   # ranked by spend
    nestle_row, coke_row = rows
    assert nestle_row['spent'] == Decimal('80')
    assert coke_row['spent'] == Decimal('20')
    assert nestle_row['orders'] == 2          # two distinct purchases
    assert coke_row['orders'] == 1
    # Share is of the supplier spend shown (80 + 20 = 100), an honest internal denominator.
    assert round(nestle_row['share']) == 80
    assert round(coke_row['share']) == 20


def test_line_discount_is_subtracted_like_the_supplier_list(shop):
    today = timezone.localdate()
    s = make_supplier(shop, name='Vendor')
    m = _material(shop, s, name='Item')
    p = make_purchase(shop, date=today)
    _line(p, m, price='10', qty=5, discount='7')     # 50 − 7 = 43

    rows = _supplier_spend(shop, today, today)
    assert rows[0]['spent'] == Decimal('43')


def test_spend_is_window_scoped_but_last_order_is_all_time(shop):
    today = timezone.localdate()
    earlier = today - timedelta(days=5)
    later = today + timedelta(days=5)
    s = make_supplier(shop, name='Vendor')
    m = _material(shop, s, name='Item')

    _line(make_purchase(shop, date=earlier), m, price='10', qty=1)   # inside the window below
    _line(make_purchase(shop, date=later), m, price='10', qty=9)     # OUTSIDE the window

    rows = _supplier_spend(shop, earlier, earlier)   # window = just the earlier day

    assert len(rows) == 1
    assert rows[0]['spent'] == Decimal('10'), "the later purchase must not count in this window"
    assert rows[0]['last_order'] == later, "last_order must be all-time, not clipped to the window"


def test_material_without_a_supplier_is_excluded(shop):
    today = timezone.localdate()
    orphan = Material.objects.create(
        user=shop.user, business=shop, name='No vendor',
        price=Decimal('10'), quantity=1, unit='pc',      # supplier left null
    )
    _line(make_purchase(shop, date=today), orphan, price='10', qty=4)

    assert _supplier_spend(shop, today, today) == []


def test_panel_renders_on_the_expense_analytics_page(client, shop, owner):
    today = timezone.localdate()
    s = make_supplier(shop, name='Nestle')
    m = _material(shop, s, name='Bear Brand')
    _line(make_purchase(shop, date=today), m, price='10', qty=5)
    client.force_login(owner)

    html = client.get(reverse('expense-analytics',
                              kwargs={'business_slug': shop.slug})).content.decode()

    assert 'Supplier Spend' in html
    assert 'Nestle' in html


# ── Supplier drill-down modal ────────────────────────────────────────────────

HX = {'HTTP_HX_REQUEST': 'true'}


def _modal(client, biz, supplier, **params):
    url = reverse('supplier-spend-detail',
                  kwargs={'business_slug': biz.slug, 'supplier_id': supplier.id})
    return client.get(url, params, **HX)


def test_modal_groups_a_vendors_items_under_their_order(client, shop, owner):
    today = timezone.localdate()
    s = make_supplier(shop, name='Monde Nissin')
    p = make_purchase(shop, date=today)
    _line(p, _material(shop, s, name='Skyflakes'), price='10', qty=20)   # 200
    _line(p, _material(shop, s, name='Chippy'), price='10', qty=50)      # 500
    client.force_login(owner)

    html = _modal(client, shop, s).content.decode()

    assert 'Monde Nissin' in html
    assert 'Skyflakes' in html and 'Chippy' in html
    assert '₱700.00' in html      # the order subtotal (both lines are this vendor's)


def test_modal_links_each_order_to_its_purchase_record(client, shop, owner):
    today = timezone.localdate()
    s = make_supplier(shop, name='Vendor')
    p = make_purchase(shop, date=today)
    _line(p, _material(shop, s, name='Item'), price='10', qty=2)
    client.force_login(owner)

    html = _modal(client, shop, s).content.decode()

    detail_url = reverse('purchase-detail',
                         kwargs={'business_slug': shop.slug, 'purchase_id': p.id})
    assert detail_url in html
    assert p.reference in html          # the link names the PO (auto-issued PO- ref)


def test_modal_header_total_ties_across_orders(client, shop, owner):
    today = timezone.localdate()
    s = make_supplier(shop, name='Vendor')
    m = _material(shop, s, name='Item')
    _line(make_purchase(shop, date=today), m, price='10', qty=20)    # 200
    _line(make_purchase(shop, date=today), m, price='10', qty=30)    # 300
    client.force_login(owner)

    html = _modal(client, shop, s).content.decode()

    assert '2 orders' in html
    assert '₱500.00' in html       # header total = 200 + 300, ties to the panel row


def test_modal_shows_only_this_vendors_slice_and_flags_the_rest(client, shop, owner):
    """A purchase can mix suppliers. The modal for one vendor shows only that vendor's
    items and subtotal (so it ties to the row), and notes that others shared the order."""
    today = timezone.localdate()
    monde = make_supplier(shop, name='Monde Nissin')
    nestle = make_supplier(shop, name='Nestle')
    p = make_purchase(shop, date=today)
    _line(p, _material(shop, monde, name='Skyflakes'), price='10', qty=20)   # 200 (Monde)
    _line(p, _material(shop, nestle, name='BearBrand'), price='10', qty=50)  # 500 (Nestle)
    client.force_login(owner)

    html = _modal(client, shop, monde).content.decode()

    assert 'Skyflakes' in html
    assert 'BearBrand' not in html, "the other supplier's item must not appear"
    assert '₱200.00' in html        # only Monde's slice, not the ₱700 whole order
    assert 'other supplier' in html  # the muted "+1 other supplier in this order" note


def test_modal_is_window_scoped(client, shop, owner):
    today = timezone.localdate()
    old = today - timedelta(days=40)
    s = make_supplier(shop, name='Vendor')
    m = _material(shop, s, name='Item')
    _line(make_purchase(shop, date=today), m, price='10', qty=2)     # 20, in window
    _line(make_purchase(shop, date=old), m, price='10', qty=99)      # 990, OUT of window
    client.force_login(owner)

    # A custom range covering only today.
    html = _modal(client, shop, s,
                  start=today.isoformat(), end=today.isoformat()).content.decode()

    assert '1 order' in html
    assert '₱20.00' in html
    assert '₱990.00' not in html


def test_modal_requires_the_hx_header_else_redirects(client, shop, owner):
    s = make_supplier(shop, name='Vendor')
    client.force_login(owner)
    url = reverse('supplier-spend-detail',
                  kwargs={'business_slug': shop.slug, 'supplier_id': s.id})

    resp = client.get(url)      # no HX header — a bare visit
    assert resp.status_code == 302
    assert reverse('expense-analytics', kwargs={'business_slug': shop.slug}) in resp['Location']
