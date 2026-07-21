"""The purchase search's empty-state "Most purchased" shortlist.

Clicking the box with 30–50 materials shouldn't drop a wall of names. On an empty query
the API returns the 5 materials bought most OFTEN — a restock shortcut — while a typed
query still searches the whole catalogue.

The ranking is a filtered annotation (count purchase lines, but only on non-void
purchases), which is exactly the kind of query that smoke-tests fine while ranking wrong:
a missing void filter would let a cancelled bulk buy top the list, and a bad join would
count another material's lines. Hence real purchase rows, not a mocked count.
"""
from decimal import Decimal

import pytest
from django.urls import reverse

from Supplier.models import Material
from Expense.models import Purchase, PurchaseItem
from tests.factories import make_business, make_purchase

pytestmark = pytest.mark.django_db


def _material(business, name):
    return Material.objects.create(
        user=business.user, business=business, name=name,
        price=Decimal('10'), quantity=100, unit='pc',
    )


def _buy(business, material, times, *, void=False):
    """Record `times` purchase lines for a material — each a separate posted purchase."""
    for _ in range(times):
        p = make_purchase(business)
        if void:
            p.is_void = True
            p.save(update_fields=['is_void'])
        PurchaseItem.objects.create(purchase=p, material=material, quantity=1)


def _search(client, business, q=''):
    url = reverse('pcart-search', kwargs={'business_slug': business.slug})
    return client.get(url, {'q': q}).json()


@pytest.fixture
def stocked(client, owner):
    biz, _ = make_business(owner, plan='pro')
    client.force_login(owner)
    return biz


def test_empty_query_returns_the_five_most_purchased_most_bought_first(client, stocked):
    biz = stocked
    # Names chosen so alphabetical order would give the OPPOSITE ranking — only frequency
    # should put Zulu first.
    zulu   = _material(biz, 'Zulu');    _buy(biz, zulu, 3)
    yankee = _material(biz, 'Yankee');  _buy(biz, yankee, 2)
    xray   = _material(biz, 'Xray');    _buy(biz, xray, 1)
    _material(biz, 'Alpha')    # 0 buys
    _material(biz, 'Bravo')    # 0 buys
    _material(biz, 'Charlie')  # 0 buys — the 6th, must be sliced off

    res = _search(client, biz, q='')
    names = [m['name'] for m in res['materials']]

    assert res['suggested'] is True
    assert len(names) == 5, "the shortlist must cap at 5, not dump the catalogue"
    assert names[:3] == ['Zulu', 'Yankee', 'Xray'], "not ranked by how often bought"
    # the two zero-buy fillers come by name; Charlie is the one dropped
    assert names[3:] == ['Alpha', 'Bravo']
    assert 'Charlie' not in names


def test_voided_purchases_do_not_count_as_bought(client, stocked):
    biz = stocked
    real  = _material(biz, 'Real');  _buy(biz, real, 1)
    ghost = _material(biz, 'Ghost'); _buy(biz, ghost, 5, void=True)

    res = _search(client, biz, q='')
    ranked = [m['name'] for m in res['materials']]

    # One real buy must outrank five voided ones.
    assert ranked.index('Real') < ranked.index('Ghost'), (
        "a cancelled purchase inflated the ranking — void filter missing"
    )


def test_the_count_is_the_materials_own_lines_not_a_neighbours(client, stocked):
    """A guard against a bad join: buying Popular must not lift Lonely's rank."""
    biz = stocked
    popular = _material(biz, 'Popular'); _buy(biz, popular, 4)
    lonely  = _material(biz, 'Lonely')   # never bought

    res = _search(client, biz, q='')
    by_name = {m['name']: i for i, m in enumerate(res['materials'])}
    assert by_name['Popular'] < by_name['Lonely']


def test_a_typed_query_searches_the_whole_catalogue_not_just_the_top_five(client, stocked):
    """The 5-cap is for the empty shortcut only. A search must find a material even when
    it's never been bought and would never make the suggestions."""
    biz = stocked
    for i in range(8):
        hot = _material(biz, f'Popular {i}'); _buy(biz, hot, 3)
    _material(biz, 'Obscure Widget')   # 0 buys, alphabetically irrelevant

    res = _search(client, biz, q='obscure')
    names = [m['name'] for m in res['materials']]

    assert res['suggested'] is False
    assert names == ['Obscure Widget'], "typed search didn't reach an un-bought material"


def test_rows_still_carry_in_cart_quantity(client, stocked):
    """The suggestion path must keep the in-cart badge data the search rows rely on."""
    biz = stocked
    mat = _material(biz, 'Coke')
    session = client.session
    session['cart'] = {str(mat.id): {'quantity': 4, 'price': '10'}}
    session.save()

    res = _search(client, biz, q='')
    row = next(m for m in res['materials'] if m['name'] == 'Coke')
    assert row['in_cart'] == 4


def test_new_business_with_no_history_still_gets_a_nonempty_shortlist(client, stocked):
    """All materials rank 0 — the dropdown must still show something on focus, name-ordered,
    rather than going blank."""
    biz = stocked
    for name in ('Banana', 'Apple', 'Cherry'):
        _material(biz, name)

    res = _search(client, biz, q='')
    names = [m['name'] for m in res['materials']]
    assert names == ['Apple', 'Banana', 'Cherry']
