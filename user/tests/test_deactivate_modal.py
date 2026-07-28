"""Deactivate Account serves a centered modal over htmx and a full page otherwise.

The Settings danger row hx-gets it into #confirmBody; a plain navigation gets the full page. Both
draw from one shared body partial. A confirmed POST deactivates the account, logs the user out, and
answers htmx with an HX-Redirect to landing instead of swapping a page into the modal.
"""
import pytest
from django.urls import reverse

from tests.factories import make_business


@pytest.fixture
def business(owner):
    """Owners always have a business in practice; the full-page fallback's navbar reverses
    business-scoped URLs off current_business, so give the owner one."""
    biz, _plan = make_business(owner, plan='standard')
    return biz


def _url(u):
    return reverse('user-deactivate', kwargs={'user_id': u.id, 'slug': u.slug})


def _logged_in(client):
    return '_auth_user_id' in client.session


def test_deactivate_renders_a_modal_over_htmx(client, owner):
    client.force_login(owner)

    response = client.get(_url(owner), HTTP_HX_REQUEST='true')

    names = {t.name for t in response.templates}
    assert 'user/partials/_user_deactivate_modal.html' in names
    assert 'user/partials/_user_deactivate_body.html' in names   # shared body, not duplicated


def test_deactivate_is_a_full_page_without_htmx(client, owner, business):
    client.force_login(owner)

    response = client.get(_url(owner))

    names = {t.name for t in response.templates}
    assert 'user/user_deactivate.html' in names
    assert 'user/partials/_user_deactivate_body.html' in names


def test_htmx_post_deactivates_logs_out_and_hx_redirects(client, owner):
    client.force_login(owner)

    response = client.post(_url(owner), {}, HTTP_HX_REQUEST='true')

    assert response.status_code == 204
    assert response['HX-Redirect'] == reverse('landing')
    assert not _logged_in(client)
    owner.refresh_from_db()
    assert owner.is_active is False


def test_plain_post_deactivates_and_redirects(client, owner):
    client.force_login(owner)

    response = client.post(_url(owner), {})

    assert response.status_code == 302
    assert response.url == reverse('landing')
    owner.refresh_from_db()
    assert owner.is_active is False
