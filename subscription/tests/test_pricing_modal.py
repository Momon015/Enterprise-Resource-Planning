"""Pricing serves a full-page modal over htmx and a full page otherwise.

The Settings row / trial card / "View pricing" button hx-get the pricing page into #fsModalBody
(the full-page modal layer). A plain navigation gets the full page. Both draw from one shared body
partial so they can't drift.
"""
import pytest
from django.urls import reverse

from tests.factories import make_business


@pytest.fixture
def business(owner):
    biz, _plan = make_business(owner, plan='standard')
    return biz


def test_pricing_renders_a_full_page_modal_over_htmx(client, owner, business):
    client.force_login(owner)

    response = client.get(
        reverse('subscription-pricing', kwargs={'business_slug': business.slug}),
        HTTP_HX_REQUEST='true',
    )

    names = {t.name for t in response.templates}
    assert 'subscription/partials/_pricing_modal.html' in names
    assert 'subscription/partials/_pricing_body.html' in names   # shared body, not duplicated


def test_pricing_is_a_full_page_without_htmx(client, owner, business):
    client.force_login(owner)

    response = client.get(
        reverse('subscription-pricing', kwargs={'business_slug': business.slug}),
    )

    names = {t.name for t in response.templates}
    assert 'subscription/pricing.html' in names
    assert 'subscription/partials/_pricing_body.html' in names
