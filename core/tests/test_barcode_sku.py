"""Barcode + SKU groundwork for scanning (Phase 1).

Retail is material ≡ product 1:1, so the barcode/SKU identity lives on the MATERIAL and
mirrors onto the auto-created product: MAT-0007 ↔ PRD-0007. Both series draw from ONE
per-business number space so a standalone product (a service, a manual good) can never
collide with a mirrored one. Barcode/SKU is a non-food concept — gated OFF for cafe/
restaurant on both forms.
"""
import pytest
from django.urls import reverse

from Product.forms import ProductForm, ServiceForm
from Product.models import Product
from Supplier.forms import MaterialForm
from Supplier.models import Material
from tests.factories import make_business, make_product


pytestmark = pytest.mark.django_db


def _material(business, *, name='Coke 1.5L', barcode=None):
    return Material.objects.create(
        user=business.user, business=business, name=name,
        price='50', quantity=1, unit='pc', barcode=barcode,
    )


def _linked_product(business, material):
    return Product.objects.create(
        user=business.user, business=business, name=material.name,
        material=material, selling_price='60', prepared_quantity=1,
    )


# ── SKU generation ────────────────────────────────────────────────────────────

def test_material_gets_mat_sku(owner):
    biz, _ = make_business(owner, plan='pro')
    assert _material(biz).sku == 'MAT-0001'


def test_standalone_product_gets_prd_sku(owner):
    biz, _ = make_business(owner, plan='pro')
    assert make_product(biz).sku == 'PRD-0001'


def test_linked_product_mirrors_its_material_number(owner):
    biz, _ = make_business(owner, plan='pro')
    m = _material(biz)                       # MAT-0001
    p = _linked_product(biz, m)
    assert m.sku == 'MAT-0001'
    assert p.sku == 'PRD-0001'               # mirror: same number, PRD- prefix


def test_shared_counter_prevents_standalone_vs_mirror_collision(owner):
    """The exact bug the shared counter kills: a standalone product numbered on its own
    counter could later collide with a mirrored product."""
    biz, _ = make_business(owner, plan='pro')
    standalone = make_product(biz, name='Special')   # PRD-0001 (consumes 1)
    m = _material(biz)                                # MAT-0002 (next in shared space)
    linked = _linked_product(biz, m)                 # mirrors → PRD-0002, not PRD-0001
    assert standalone.sku == 'PRD-0001'
    assert m.sku == 'MAT-0002'
    assert linked.sku == 'PRD-0002'


# ── form gates (barcode is non-food only) ──────────────────────────────────────

def test_product_form_drops_barcode_for_retail(owner):
    biz, _ = make_business(owner, plan='pro')          # default retail
    assert 'barcode' not in ProductForm(business=biz, user=owner).fields


def test_product_form_keeps_barcode_for_cafe(owner):
    biz, _ = make_business(owner, plan='pro')
    biz.business_type = 'cafe'; biz.save()
    assert 'barcode' in ProductForm(business=biz, user=owner).fields


def test_material_form_keeps_barcode_for_retail(owner):
    biz, _ = make_business(owner, plan='pro')
    assert 'barcode' in MaterialForm(business=biz).fields


def test_material_form_drops_barcode_for_cafe(owner):
    biz, _ = make_business(owner, plan='pro')
    biz.business_type = 'restaurant'; biz.save()
    assert 'barcode' not in MaterialForm(business=biz).fields


def test_service_form_has_no_barcode_or_sku(owner):
    biz, _ = make_business(owner, plan='pro')
    fields = ServiceForm(business=biz, user=owner).fields
    assert 'barcode' not in fields and 'sku' not in fields


# ── search matches a scanned barcode ────────────────────────────────────────────

def test_sale_search_exact_barcode_is_flagged_a_scan(client, owner):
    biz, _ = make_business(owner, plan='pro')
    p = make_product(biz, name='Skyflakes')
    p.barcode = '4800016641503'; p.save()
    client.force_login(owner)
    res = client.get(reverse('sale-search', kwargs={'business_slug': biz.slug}),
                     {'q': '4800016641503'}).json()
    assert res['exact_match'] is True
    assert len(res['products']) == 1 and res['products'][0]['id'] == p.id


def test_sale_search_partial_barcode_still_lists_without_scan_flag(client, owner):
    biz, _ = make_business(owner, plan='pro')
    p = make_product(biz, name='Skyflakes')
    p.barcode = '4800016641503'; p.save()
    client.force_login(owner)
    res = client.get(reverse('sale-search', kwargs={'business_slug': biz.slug}),
                     {'q': '480001'}).json()          # a fragment, not a full scan
    assert res.get('exact_match') is not True
    assert any(row['id'] == p.id for row in res['products'])
