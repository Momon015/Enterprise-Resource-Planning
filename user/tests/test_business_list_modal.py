"""View All Businesses serves a full-page modal over htmx and a full page otherwise.

The Settings row hx-gets the list into #fsModalBody (a full-page modal layer below the confirm
modal, so a card's own archive / new-code confirms stack on top). A plain navigation gets the
full page. Both draw the grid from one shared body partial so they can't drift.
"""
import pytest
from django.urls import reverse

from tests.factories import make_business


@pytest.fixture
def business(owner):
    biz, _plan = make_business(owner, plan='standard')
    return biz


def test_businesses_render_a_full_page_modal_over_htmx(client, owner, business):
    client.force_login(owner)

    response = client.get(reverse('business-list'), HTTP_HX_REQUEST='true')

    names = {t.name for t in response.templates}
    assert 'user/partials/_business_list_modal.html' in names
    assert 'user/partials/_business_list_body.html' in names   # shared body, not duplicated


def test_businesses_are_a_full_page_without_htmx(client, owner, business):
    client.force_login(owner)

    response = client.get(reverse('business-list'))

    names = {t.name for t in response.templates}
    assert 'user/business_list.html' in names
    assert 'user/partials/_business_list_body.html' in names
