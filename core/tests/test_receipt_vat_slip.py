"""The receipt is driven by TWO independent switches, and the billing-statement slip has to honour
both separately:

  is_vat_registered  → whether the 12% VAT breakdown shows (a VAT-registered seller charges VAT on
                       every sale, so it shows on the everyday slip too, not only the official invoice)
  is_bir_active      → whether the OFFICIAL chrome (SI- number, PTU, "SALES INVOICE") shows

The bug this guards: a VAT-registered seller in internal mode (is_bir_active=False) was getting a
slip with NO VAT breakdown at all, because the VAT block lived only on the official invoice.
"""
import pytest
from django.urls import reverse

from tests.factories import make_business, make_product, make_sale


pytestmark = pytest.mark.django_db


def _receipt(client, biz, sale):
    return client.get(reverse('sale-receipt',
                              kwargs={'business_slug': biz.slug, 'sale_id': sale.id})).content.decode()


def _sale(biz):
    return make_sale(biz, [(make_product(biz, name='Skyflakes', selling_price='50'), 1)])


def test_vat_registered_internal_slip_shows_vat_but_keeps_ord(client, owner):
    biz, _ = make_business(owner, plan='pro')
    biz.is_vat_registered = True
    biz.is_bir_active = False           # internal mode → ORD-, billing statement, no SI/PTU
    biz.save()
    sale = _sale(biz)
    client.force_login(owner)
    html = _receipt(client, biz, sale)

    # VAT breakdown shows — keyed on is_vat_registered
    assert 'VATable Sale' in html and 'VAT (12%)' in html
    # ...but the document is still the unofficial slip — keyed on is_bir_active
    assert 'BILLING STATEMENT' in html
    assert 'SALES INVOICE' not in html and 'PTU' not in html
    assert sale.reference.startswith('ORD-') and sale.reference in html
    # a supplementary doc showing VAT must disclaim input-tax use
    assert 'NOT VALID FOR CLAIM OF INPUT TAX' in html


def test_non_vat_internal_slip_shows_no_vat_block(client, owner):
    biz, _ = make_business(owner, plan='pro')
    biz.is_vat_registered = False       # most of our clients
    biz.is_bir_active = False
    biz.save()
    sale = _sale(biz)
    client.force_login(owner)
    html = _receipt(client, biz, sale)

    assert 'VATable Sale' not in html and 'VAT (12%)' not in html
    assert 'NOT VALID FOR CLAIM OF INPUT TAX' not in html   # no VAT → no such claim to disclaim
    assert 'BILLING STATEMENT' in html                      # still the everyday slip
