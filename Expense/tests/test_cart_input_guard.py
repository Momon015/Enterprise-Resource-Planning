"""§6 input-crash guard for the purchase-cart JSON API.

cart_set_line converts total_price/discount with Decimal(). A non-numeric value
used to raise InvalidOperation → 500; it must now return a 400 and leave the line
unchanged.
"""
import pytest
from django.urls import reverse

from tests.factories import make_stock


def _seed_cart(client, material):
    session = client.session
    session['cart'] = {
        str(material.id): {'quantity': 1, 'price': '10', 'discount': '0'}
    }
    session.save()


def test_non_numeric_total_price_returns_400_not_500(client, owner, business):
    client.force_login(owner)
    material = make_stock(business).material
    _seed_cart(client, material)

    url = reverse('pcart-set-line', kwargs={'business_slug': business.slug})
    resp = client.post(url, {'material_id': str(material.id), 'total_price': 'abc'})

    assert resp.status_code == 400          # graceful, not an unhandled 500


def test_valid_total_price_still_updates_the_line(client, owner, business):
    client.force_login(owner)
    material = make_stock(business).material
    _seed_cart(client, material)

    url = reverse('pcart-set-line', kwargs={'business_slug': business.slug})
    resp = client.post(url, {'material_id': str(material.id), 'total_price': '50'})

    assert resp.status_code == 200
    # 50 total / qty 1 → unit price 50
    assert client.session['cart'][str(material.id)]['price'] == '50'
