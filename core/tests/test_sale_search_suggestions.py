"""The sale search island — best-seller suggestions, typed search, and add.

The sale-side twin of the purchase search. On focus (empty query) it offers a best-seller
shortlist: top 3 goods + top 3 services, or top 5 goods when the shop sells no services.
A typed query searches the whole catalogue, capped at 10; adding respects stock, services,
and locked/session products.

The ranking is a filtered annotation (count sale lines, but only on completed non-void
sales) — the exact query that smoke-tests fine while ranking wrong. So: real sales, and
assertions on order, not just presence.
"""
from decimal import Decimal

import pytest
from django.urls import reverse

from Sales.models import Sale
from tests.factories import make_business, make_product, make_service, make_sale

pytestmark = pytest.mark.django_db


def _search(client, business, q=''):
    url = reverse('sale-search', kwargs={'business_slug': business.slug})
    return client.get(url, {'q': q}).json()


def _add(client, business, product):
    url = reverse('sale-add', kwargs={'business_slug': business.slug})
    return client.post(url, {'product_id': product.id}).json()


@pytest.fixture
def shop(client, owner):
    biz, _ = make_business(owner, plan='pro')
    client.force_login(owner)
    return biz


def test_empty_query_returns_the_five_best_sellers_most_sold_first(client, shop):
    biz = shop
    # Names chosen so alphabetical order would invert the ranking — only sales volume
    # should put Zebra first.
    zebra  = make_product(biz, name='Zebra', stock=100)
    yak    = make_product(biz, name='Yak', stock=100)
    xerus  = make_product(biz, name='Xerus', stock=100)
    make_product(biz, name='Ant', stock=100)     # never sold
    make_product(biz, name='Bee', stock=100)     # never sold
    make_product(biz, name='Cat', stock=100)     # never sold — the 6th, sliced off

    make_sale(biz, [(zebra, 1)])
    make_sale(biz, [(zebra, 1)])
    make_sale(biz, [(zebra, 1)])   # 3 sale lines
    make_sale(biz, [(yak, 1)])
    make_sale(biz, [(yak, 1)])     # 2
    make_sale(biz, [(xerus, 1)])   # 1

    res = _search(client, biz, q='')
    names = [p['name'] for p in res['products']]

    assert res['suggested'] is True
    assert len(names) == 5, "the shortlist must cap at 5, not dump the catalogue"
    assert names[:3] == ['Zebra', 'Yak', 'Xerus'], "not ranked by how often sold"
    assert names[3:] == ['Ant', 'Bee']    # zero-sale fillers by name; Cat dropped
    assert 'Cat' not in names


def _enable_services(biz):
    biz.offers_services = True
    biz.save(update_fields=['offers_services'])


def test_empty_query_splits_top_three_goods_and_top_three_services(client, shop):
    """With services in the catalogue, the shortlist is 3 + 3 so services aren't buried
    under fast-moving goods."""
    biz = shop
    _enable_services(biz)
    goods = [make_product(biz, name=f'Good {i}', stock=100) for i in range(4)]
    svcs  = [make_service(biz, name=f'Svc {i}') for i in range(4)]

    # Higher index sells more, so the top sellers are the alphabetically-LAST names —
    # which is what proves the shortlist is ranked by sales, not by name.
    for n, g in enumerate(goods):      # Good 3 sells most (4 sales), Good 0 least (1)
        for _ in range(n + 1):
            make_sale(biz, [(g, 1)])
    for n, s in enumerate(svcs):       # Svc 3 sells most
        for _ in range(n + 1):
            make_sale(biz, [(s, 1)])

    res = _search(client, biz, q='')
    assert res['suggested'] is True
    assert [p['name'] for p in res['products']] == ['Good 3', 'Good 2', 'Good 1']
    assert [p['name'] for p in res['services']] == ['Svc 3', 'Svc 2', 'Svc 1']
    assert all(p['is_service'] for p in res['services'])
    assert not any(p['is_service'] for p in res['products'])
    # 6 total suggestions on click
    assert len(res['products']) + len(res['services']) == 6


def test_no_services_gives_goods_all_five_slots(client, shop):
    """No point leaving three empty service slots — goods take the whole shortlist."""
    biz = shop
    for i in range(6):
        make_product(biz, name=f'Good {i}', stock=100)

    res = _search(client, biz, q='')
    assert res['services'] == []
    assert len(res['products']) == 5


def test_service_fees_toggle_off_hides_existing_services(client, shop):
    """Owner disabled Service Fees (gone from the navbar) while service products still
    exist — the search must not keep offering them, on focus OR when typed. A hidden
    feature that's still sellable through the box is the bug being guarded here."""
    biz = shop               # offers_services defaults False
    for i in range(6):
        make_product(biz, name=f'Good {i}', stock=100)
    make_service(biz, name='Xerox')
    make_service(biz, name='GCash Cash-in')

    # On focus: no services section, goods take all 5 slots.
    res = _search(client, biz, q='')
    assert res['services'] == []
    assert len(res['products']) == 5
    assert not any(p['is_service'] for p in res['products'])

    # Typed: a service that matches the query is still excluded.
    typed = _search(client, biz, q='xerox')
    assert typed['products'] == []


def test_service_fees_toggle_on_surfaces_them_again(client, shop):
    """The mirror of the above — flipping the toggle on brings services back."""
    biz = shop
    _enable_services(biz)
    make_product(biz, name='Good', stock=100)
    make_service(biz, name='Xerox')

    assert [p['name'] for p in _search(client, biz, q='')['services']] == ['Xerox']
    assert [p['name'] for p in _search(client, biz, q='xerox')['products']] == ['Xerox']


def test_voided_and_draft_sales_do_not_count(client, shop):
    biz = shop
    real  = make_product(biz, name='Real', stock=100)
    ghost = make_product(biz, name='Ghost', stock=100)
    draft = make_product(biz, name='Draft', stock=100)

    make_sale(biz, [(real, 1)])                       # 1 real sale
    void = make_sale(biz, [(ghost, 1)])               # 5 sale lines, but voided
    void.is_void = True
    void.save(update_fields=['is_void'])
    for _ in range(4):
        v = make_sale(biz, [(ghost, 1)]); v.is_void = True; v.save(update_fields=['is_void'])
    for _ in range(5):
        make_sale(biz, [(draft, 1)], status='pending')  # never completed

    ranked = [p['name'] for p in _search(client, biz, q='')['products']]
    assert ranked.index('Real') < ranked.index('Ghost'), "a void inflated the ranking"
    assert ranked.index('Real') < ranked.index('Draft'), "a draft counted as a sale"


def test_typed_query_searches_the_whole_catalogue_capped_at_ten(client, shop):
    biz = shop
    for i in range(8):
        hot = make_product(biz, name=f'Popular {i}', stock=100)
        make_sale(biz, [(hot, 1)])
    make_product(biz, name='Obscure Widget', stock=100)   # never sold

    res = _search(client, biz, q='obscure')
    names = [p['name'] for p in res['products']]
    assert res['suggested'] is False
    assert names == ['Obscure Widget'], "typed search didn't reach an unsold product"


def test_typed_search_never_returns_more_than_ten(client, shop):
    biz = shop
    for i in range(15):
        make_product(biz, name=f'Widget {i:02d}', stock=100)

    res = _search(client, biz, q='widget')
    assert len(res['products']) == 10


def test_rows_carry_image_or_fall_back_to_initials_and_in_cart(client, shop):
    """The sale side keeps a real image field (unlike materials). in_cart rides along too."""
    biz = shop
    p = make_product(biz, name='Coke', stock=100)

    session = client.session
    session['sale'] = {str(p.id): {'quantity': 3, 'selling_price': '50', 'cost_price': '30'}}
    session.save()

    row = next(r for r in _search(client, biz, q='')['products'] if r['name'] == 'Coke')
    assert 'image' in row          # present (empty string when no photo → island shows initials)
    assert row['in_cart'] == 3


def test_add_puts_the_product_in_the_sale_and_reports_it(client, shop):
    biz = shop
    p = make_product(biz, name='Skyflakes', selling_price='50', stock=100)

    res = _add(client, biz, p)
    assert res['added'] == 'Skyflakes'
    assert res['item_count'] == 1
    assert client.session['sale'][str(p.id)]['quantity'] == 1


def test_adding_the_same_product_twice_bumps_quantity(client, shop):
    biz = shop
    p = make_product(biz, name='Skyflakes', stock=100)
    _add(client, biz, p)
    res = _add(client, biz, p)
    assert res['item_count'] == 1
    assert client.session['sale'][str(p.id)]['quantity'] == 2


def test_add_refuses_an_out_of_stock_product_with_a_warning(client, shop):
    biz = shop
    p = make_product(biz, name='Empty', stock=0)
    res = _add(client, biz, p)
    assert 'warning' in res
    assert str(p.id) not in client.session.get('sale', {})


def test_new_shop_with_no_sales_still_gets_a_nonempty_shortlist(client, shop):
    biz = shop
    for name in ('Banana', 'Apple', 'Cherry'):
        make_product(biz, name=name, stock=100)

    names = [p['name'] for p in _search(client, biz, q='')['products']]
    assert names == ['Apple', 'Banana', 'Cherry']
