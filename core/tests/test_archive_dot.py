"""The Archive button carries a subtle red dot when — and only when — the list has at
least one archived item, mirroring the bell's pinned dot.

Two things are being pinned here, and they're easy to regress into each other:
  1. The button is ALWAYS shown now (the unify decision). Material & Employee used to
     hide it when the archive was empty; if that guard creeps back, the empty-state case
     below stops finding the button.
  2. The dot (`archive-dot`) appears ONLY when count > 0. An always-on dot would be
     wallpaper; a never-on dot is the feature not working.

The count each view feeds the dot is scoped: the Product list must not light up for an
archived SERVICE, and vice versa — that's its own test because it's the subtle one.
"""
import pytest
from django.urls import reverse

from Product.models import Product
from Supplier.models import Material, Supplier
from Employee.models import Employee
from tests.factories import (
    make_business, make_product, make_service, make_stock, make_supplier,
    make_employee,
)


pytestmark = pytest.mark.django_db

DOT = 'archive-dot'


@pytest.fixture
def biz(owner, client):
    business, _plan = make_business(owner, plan='pro')
    business.offers_services = True
    business.save()
    client.force_login(owner)
    return business


def _get(client, name, biz):
    return client.get(reverse(name, kwargs={'business_slug': biz.slug}))


# --- Product -----------------------------------------------------------------

def test_product_list_button_shows_without_dot_when_nothing_archived(client, biz):
    make_product(biz, name='Coke')
    html = _get(client, 'product-list', biz).content.decode()
    assert 'archive-ico' in html   # the button is always present
    assert DOT not in html


def test_product_list_dot_appears_when_a_product_is_archived(client, biz):
    p = make_product(biz, name='Coke')
    p.is_active = False
    p.save()
    html = _get(client, 'product-list', biz).content.decode()
    assert DOT in html


def test_product_dot_ignores_an_archived_service(client, biz):
    """A product-list dot must reflect archived PRODUCTS, not services — they have their
    own list and their own dot on service_list."""
    s = make_service(biz, name='Xerox')
    s.is_active = False
    s.save()
    html = _get(client, 'product-list', biz).content.decode()
    assert DOT not in html


# --- Service -----------------------------------------------------------------

def test_service_list_dot_appears_only_for_archived_service(client, biz):
    make_service(biz, name='GCash cash-in')
    html_clean = _get(client, 'service-list', biz).content.decode()
    assert 'archive-ico' in html_clean
    assert DOT not in html_clean

    s = Product.services.filter(business=biz).first()
    s.is_active = False
    s.save()
    assert DOT in _get(client, 'service-list', biz).content.decode()


def test_service_dot_ignores_an_archived_product(client, biz):
    p = make_product(biz, name='Coke')
    p.is_active = False
    p.save()
    make_service(biz, name='Xerox')
    assert DOT not in _get(client, 'service-list', biz).content.decode()


# --- Supplier ----------------------------------------------------------------

def test_supplier_list_dot_toggles_with_an_archived_supplier(client, biz):
    make_supplier(biz, name='Nestle')
    clean = _get(client, 'supplier-list', biz).content.decode()
    assert 'archive-ico' in clean
    assert DOT not in clean

    s = make_supplier(biz, name='Coca-Cola')
    s.status = 'inactive'
    s.save()
    assert DOT in _get(client, 'supplier-list', biz).content.decode()


# --- Material ----------------------------------------------------------------

def test_material_list_button_always_shows_and_dot_toggles(client, biz):
    """Material used to HIDE the button when empty; unify made it always-visible."""
    make_stock(biz, name='Flour')
    clean = _get(client, 'material-list', biz).content.decode()
    assert 'archive-ico' in clean   # present even with nothing archived
    assert DOT not in clean

    m = Material.objects.filter(business=biz).first()
    m.status = 'inactive'
    m.save()
    assert DOT in _get(client, 'material-list', biz).content.decode()


# --- Employee ----------------------------------------------------------------

def test_employee_list_button_always_shows_and_dot_toggles(client, biz):
    """Employee also used to hide the Quick-links block when the archive was empty."""
    make_employee(biz, name='Ana')
    clean = _get(client, 'employee-list', biz).content.decode()
    assert 'archive-ico' in clean
    assert DOT not in clean

    e = make_employee(biz, name='Ben')
    e.status = 'inactive'
    e.save()
    assert DOT in _get(client, 'employee-list', biz).content.decode()
