"""Cash Drawer settings serves a modal over htmx and a full page otherwise.

Same view, two shells: an htmx request (the Settings row's hx-get) gets the modal partial into
#confirmBody; a plain navigation (no-JS fallback) gets the full page. Both draw their form fields
from one shared partial so they can't drift, and a valid save answers htmx with an HX-Redirect
back to Settings instead of swapping a whole page into the modal.
"""
import pytest
from django.urls import reverse

from tests.factories import make_business


@pytest.fixture
def business(owner):
    biz, _plan = make_business(owner, plan='standard')
    return biz


def _url(business):
    return reverse('cash-drawer-settings',
                   kwargs={'business_id': business.id, 'business_slug': business.slug})


def test_cash_drawer_renders_a_modal_over_htmx(client, owner, business):
    client.force_login(owner)

    response = client.get(_url(business), HTTP_HX_REQUEST='true')

    names = {t.name for t in response.templates}
    assert 'user/partials/_cash_drawer_modal.html' in names
    assert 'user/partials/_cash_drawer_fields.html' in names   # shared fields, not duplicated


def test_cash_drawer_is_a_full_page_without_htmx(client, owner, business):
    client.force_login(owner)

    response = client.get(_url(business))

    names = {t.name for t in response.templates}
    assert 'user/cash_drawer_settings.html' in names
    assert 'user/partials/_cash_drawer_fields.html' in names


def test_htmx_save_hx_redirects_to_settings(client, owner, business):
    client.force_login(owner)

    response = client.post(
        _url(business),
        {'default_opening_cash': '750.00', 'enable_cash_reconciliation': 'on'},
        HTTP_HX_REQUEST='true',
    )

    assert response.status_code == 204
    assert response['HX-Redirect'] == reverse('settings', kwargs={'business_slug': owner.slug})
    business.refresh_from_db()
    assert business.default_opening_cash == 750
    assert business.enable_cash_reconciliation is True


def test_plain_save_redirects_to_settings(client, owner, business):
    client.force_login(owner)

    response = client.post(_url(business), {'default_opening_cash': '500.00'})

    assert response.status_code == 302
    assert response.url == reverse('settings', kwargs={'business_slug': owner.slug})
