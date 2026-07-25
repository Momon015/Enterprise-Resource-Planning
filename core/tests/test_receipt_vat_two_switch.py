"""The receipt reads TWO independent switches, and they must not be conflated.

  is_vat_registered → whether the VAT breakdown (VATable / VAT / Exempt / Zero) prints
  is_bir_active     → whether it's an official SALES INVOICE (SI-, PTU/serial) or an
                      unofficial ORD- BILLING STATEMENT

The bug these pin: VAT figures used to live only inside the official-invoice template, so a
VAT-registered pharmacy running internally (is_vat_registered=True, is_bir_active=False) — the
"half-true" case — got a billing statement with NO VAT breakdown. VAT presentation follows VAT
registration, not accreditation.

All four corners are covered because the two switches are orthogonal: fixing the half-true case
must not start leaking SI-/official chrome onto the slip, and must not hide VAT from a non-VAT
compliant seller's invoice (which was already correct).
"""
import pytest
from django.urls import reverse

from tests.factories import make_business, make_product, make_sale


pytestmark = pytest.mark.django_db

VAT_LINE = 'VATable Sale (V)'
SLIP_MARK = 'BILLING STATEMENT'
OFFICIAL_MARK = 'SALES INVOICE'
# BIR-standard marking every supplementary document must bear (RR 18-2012). The slip is one;
# the official invoice is a PRINCIPAL document and must never carry it.
INPUT_TAX_DISCLAIMER = 'NOT VALID FOR CLAIM OF INPUT TAX'


def _receipt_html(client, biz, sale):
    return client.get(reverse('sale-receipt', kwargs={
        'business_slug': biz.slug, 'sale_id': sale.id})).content.decode()


def _biz(owner, *, vat, bir):
    """A business wired to a given (vat, bir) corner, with a sale already rung.

    is_bir_active must be set BEFORE the sale is made — Sale.save() picks the ORD-/SI-
    series off the business at creation time, so a business flipped afterwards would carry
    the wrong reference prefix and the reference assertions would be meaningless.
    """
    biz, _plan = make_business(owner, plan='pro')
    biz.is_vat_registered = vat
    biz.is_bir_active = bir
    if bir:
        biz.bir_ptu = 'FP012025-000123'
    biz.save()
    product = make_product(biz, selling_price='100')     # defaults to a VATable class
    sale = make_sale(biz, [(product, 1)])
    return biz, sale


def test_non_vat_internal_shows_neither_vat_nor_official_chrome(client, owner):
    """F/F — the sari-sari default. Plain slip, no VAT, ORD- number."""
    biz, sale = _biz(owner, vat=False, bir=False)
    client.force_login(owner)
    html = _receipt_html(client, biz, sale)

    assert VAT_LINE not in html
    assert SLIP_MARK in html
    assert INPUT_TAX_DISCLAIMER in html      # supplementary-doc marking, VAT or not
    assert OFFICIAL_MARK not in html
    assert sale.reference.startswith('ORD-')


def test_vat_registered_internal_shows_vat_but_stays_a_billing_statement(client, owner):
    """T/F — the half-true case, the whole point. VAT breakdown prints, but it's still the
    unofficial ORD- billing statement: no SALES INVOICE label, no PTU."""
    biz, sale = _biz(owner, vat=True, bir=False)
    client.force_login(owner)
    html = _receipt_html(client, biz, sale)

    assert VAT_LINE in html                 # ← the fix: VAT shows in internal mode
    assert 'VAT (12%)' in html
    assert SLIP_MARK in html                # still the billing statement
    assert INPUT_TAX_DISCLAIMER in html     # ...and marked so the VAT can't be claimed
    assert OFFICIAL_MARK not in html        # no official chrome
    assert 'PTU' not in html
    assert sale.reference.startswith('ORD-')


def test_vat_registered_accredited_is_a_full_official_invoice(client, owner):
    """T/T — the fully compliant supermarket. VAT breakdown AND official chrome, SI- number."""
    biz, sale = _biz(owner, vat=True, bir=True)
    client.force_login(owner)
    html = _receipt_html(client, biz, sale)

    assert VAT_LINE in html
    assert OFFICIAL_MARK in html
    assert 'PTU: FP012025-000123' in html
    assert INPUT_TAX_DISCLAIMER not in html   # a principal invoice must NEVER be marked this way
    assert sale.reference.startswith('SI-')


def test_non_vat_accredited_is_an_official_invoice_without_a_vat_block(client, owner):
    """F/T — a Non-VAT (percentage-tax) business accredited to issue invoices. Official SI-,
    but the VAT breakdown must stay hidden — it charges no VAT to break down."""
    biz, sale = _biz(owner, vat=False, bir=True)
    client.force_login(owner)
    html = _receipt_html(client, biz, sale)

    assert VAT_LINE not in html             # no VAT block for a non-VAT seller
    assert OFFICIAL_MARK in html            # but still a real invoice
    assert sale.reference.startswith('SI-')
