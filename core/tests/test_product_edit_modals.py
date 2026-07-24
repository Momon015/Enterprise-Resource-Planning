"""Editing an existing product happens in two short modals, not the 12-field long form.

Pricing (A) owns what changes often; Details (B) owns the identity and is opened from A's
header. The long form is create-only now, so the pair must cover EVERY field it had —
anything missing from both becomes permanently uneditable, which is the failure mode these
tests exist to catch.
"""
import pytest
from django.urls import reverse

from Product.forms import ProductForm, ProductPricingForm, ProductDetailsForm
from Product.models import Product
from tests.factories import make_business, make_product


pytestmark = pytest.mark.django_db

HX = {'HTTP_HX_REQUEST': 'true'}


@pytest.fixture
def product(owner, client):
    biz, _plan = make_business(owner, plan='pro')
    client.force_login(owner)
    return biz, make_product(biz, name='Coke 1.5L', selling_price='75', cost_price='50')


def _url(name, biz, prod):
    return reverse(name, kwargs={'business_slug': biz.slug, 'product_id': prod.id})


def test_the_two_modals_together_cover_every_field_of_the_long_form():
    """★ The invariant that makes retiring the long form safe. Add a field to
    ProductForm.Meta.fields without putting it in one of these two and it silently becomes
    uneditable for every existing product — no error, just a field nobody can ever change."""
    long_form = set(ProductForm.Meta.fields)
    short = set(ProductPricingForm.Meta.fields) | set(ProductDetailsForm.Meta.fields)

    assert long_form - short == set(), (
        f"these fields have no home in either short modal: {sorted(long_form - short)}"
    )
    assert set(ProductPricingForm.Meta.fields) & set(ProductDetailsForm.Meta.fields) == set(), (
        "a field is in BOTH modals — two forms would race to save it"
    )


def test_cost_is_editable_in_the_pricing_modal_for_a_standalone_product():
    """Cost sits in modal A rather than being a read-only readout for two reasons: the
    margin bar needs it in the DOM, and with the long form retired a read-only cost would
    leave hand-made products permanently stuck at whatever they were created with."""
    assert 'cost_price' in ProductPricingForm.Meta.fields


def test_pricing_modal_saves_price_and_thresholds(client, product):
    biz, prod = product

    resp = client.post(_url('product-edit-pricing', biz, prod), {
        'cost_price': '50.00', 'selling_price': '90.00', 'target_margin': '40',
        'prepared_quantity': prod.prepared_quantity,
        'low_stock_threshold': '5', 'high_stock_threshold': '80',
    }, **HX)
    prod.refresh_from_db()

    assert resp.status_code == 204, "a successful save should close the modal"
    assert str(prod.selling_price) .startswith('90'), prod.selling_price
    assert prod.low_stock_threshold == 5
    assert prod.high_stock_threshold == 80


def test_details_modal_saves_and_hands_back_to_the_pricing_modal(client, product):
    """B is a detour from A, not a destination — saving returns to A so "fix the name,
    then carry on with the price" stays one flow."""
    biz, prod = product

    resp = client.post(_url('product-edit-details', biz, prod), {
        'name': 'Coke 1.5 Liter', 'vat_class': prod.vat_class,
        'category': prod.category_id or '', 'barcode': '', 'description': '',
    }, **HX)
    prod.refresh_from_db()

    assert resp.status_code == 200, "saving details should re-render, not close"
    assert prod.name == 'Coke 1.5 Liter'
    assert _url('product-edit-pricing', biz, prod) in resp.content.decode(), (
        "after saving details the user was not handed back to the pricing modal"
    )


def test_a_rename_does_not_strand_the_modal_on_a_dead_slug(client, product):
    """★ The slug trap. Renaming re-slugs the product and the product URLs carry that slug,
    so the modals are keyed on ID ALONE. If they took a slug, the request right after a
    rename would 404 against a URL this very modal had just invalidated."""
    biz, prod = product
    old_slug = prod.slug

    client.post(_url('product-edit-details', biz, prod), {
        'name': 'Totally Different Name', 'vat_class': prod.vat_class,
        'category': prod.category_id or '', 'barcode': '', 'description': '',
    }, **HX)
    prod.refresh_from_db()
    assert prod.slug != old_slug, "fixture didn't actually re-slug — the trap isn't tested"

    assert client.get(_url('product-edit-pricing', biz, prod), **HX).status_code == 200


def test_a_duplicate_name_is_reported_on_the_field_not_saved(client, product):
    biz, prod = product
    make_product(biz, name='Sprite 1.5L', selling_price='75')

    resp = client.post(_url('product-edit-details', biz, prod), {
        'name': 'Sprite 1.5L', 'vat_class': prod.vat_class,
        'category': prod.category_id or '', 'barcode': '', 'description': '',
    }, **HX)
    prod.refresh_from_db()

    assert resp.status_code == 200
    assert prod.name == 'Coke 1.5L', "the duplicate name was saved anyway"
    assert 'already exists' in resp.content.decode()


def test_the_pricing_modal_does_not_run_the_duplicate_name_check(client, product):
    """The pricing form never offers `name`, so it must not re-validate one. A product
    whose own name somehow collides would otherwise be unable to save a price change."""
    biz, prod = product

    resp = client.post(_url('product-edit-pricing', biz, prod), {
        'cost_price': '50.00', 'selling_price': '99.00', 'target_margin': '40',
        'prepared_quantity': prod.prepared_quantity,
        'low_stock_threshold': '5', 'high_stock_threshold': '80',
    }, **HX)

    assert resp.status_code == 204
    prod.refresh_from_db()
    assert prod.name == 'Coke 1.5L', "the pricing modal changed the name"


@pytest.mark.parametrize('url_name', ['product-edit-pricing', 'product-edit-details'])
def test_a_direct_visit_bounces_to_the_list(client, product, url_name):
    """Both render bare partials with no page around them."""
    biz, prod = product

    resp = client.get(_url(url_name, biz, prod))       # no HX header

    assert resp.status_code == 302
    assert resp.url == reverse('product-list', kwargs={'business_slug': biz.slug})


def test_the_list_and_detail_open_the_pricing_modal(client, product):
    """All the old entry points had to be repointed; a missed one still opens the long
    form, which is now create-only."""
    biz, prod = product
    pricing = _url('product-edit-pricing', biz, prod)

    list_body = client.get(reverse('product-list',
                                   kwargs={'business_slug': biz.slug})).content.decode()
    detail_body = client.get(reverse('product-detail', kwargs={
        'business_slug': biz.slug, 'product_slug': prod.slug,
        'product_id': prod.id})).content.decode()

    assert pricing in list_body, "the product list row still points at the long form"
    assert pricing in detail_body, "the detail page Edit button still points at the long form"
