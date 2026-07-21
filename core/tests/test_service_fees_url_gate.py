"""Service Fees is opt-in — when it's off, the URLs must be closed, not just the sidebar.

Reported bug: an owner turned Service Fees ON, copied the /services/ URL, turned it OFF,
and could still open the service catalogue by pasting the link. Hiding the nav entry is
not access control. These pin that every service management URL 404s with the toggle off,
and that a hand-crafted add can't sneak a service into the sale either.
"""
import pytest
from django.urls import reverse

from tests.factories import make_business, make_product, make_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def biz_off(client, owner):
    """Business with Service Fees OFF (the default) and one existing service."""
    biz, _ = make_business(owner, plan='pro')
    client.force_login(owner)
    return biz


def _on(biz):
    biz.offers_services = True
    biz.save(update_fields=['offers_services'])


def test_service_list_404s_when_the_feature_is_off(client, biz_off):
    url = reverse('service-list', kwargs={'business_slug': biz_off.slug})
    assert client.get(url).status_code == 404


def test_service_list_opens_when_the_feature_is_on(client, biz_off):
    _on(biz_off)
    url = reverse('service-list', kwargs={'business_slug': biz_off.slug})
    assert client.get(url).status_code == 200


def test_service_create_and_archived_pages_404_when_off(client, biz_off):
    for name in ('service-create', 'archived-services'):
        url = reverse(name, kwargs={'business_slug': biz_off.slug})
        assert client.get(url).status_code == 404, f"{name} was reachable with the toggle off"


def test_service_update_and_archive_404_when_off(client, biz_off):
    """Even with a real service id in hand (the exact copy-the-URL scenario)."""
    _on(biz_off)
    svc = make_service(biz_off, name='Xerox')
    biz_off.offers_services = False
    biz_off.save(update_fields=['offers_services'])

    for name in ('service-update', 'service-archive'):
        url = reverse(name, kwargs={'business_slug': biz_off.slug,
                                    'service_slug': svc.slug, 'service_id': svc.id})
        assert client.get(url).status_code == 404, f"{name} reachable with a copied URL"


def test_sale_add_refuses_a_service_when_the_feature_is_off(client, biz_off):
    """The search hides services when off; this guards the raw endpoint behind it."""
    _on(biz_off)
    svc = make_service(biz_off, name='Xerox')
    biz_off.offers_services = False
    biz_off.save(update_fields=['offers_services'])

    url = reverse('sale-add', kwargs={'business_slug': biz_off.slug})
    res = client.post(url, {'product_id': svc.id}).json()
    assert 'warning' in res
    assert str(svc.id) not in client.session.get('sale', {})


def test_sale_add_still_allows_a_normal_product_when_services_are_off(client, biz_off):
    """The gate is scoped to services — goods are unaffected."""
    p = make_product(biz_off, name='Skyflakes', stock=100)
    url = reverse('sale-add', kwargs={'business_slug': biz_off.slug})
    res = client.post(url, {'product_id': p.id}).json()
    assert 'warning' not in res
    assert client.session['sale'][str(p.id)]['quantity'] == 1


def test_topbar_search_hides_services_when_the_feature_is_off(client, biz_off):
    """The global topbar search (sale scope) must exclude services too — the last box a
    service could surface through with the feature off."""
    _on(biz_off)
    make_service(biz_off, name='Xerox Service')
    biz_off.offers_services = False
    biz_off.save(update_fields=['offers_services'])

    url = reverse('global-search', kwargs={'business_slug': biz_off.slug})
    html = client.get(url, {'q': 'xerox', 'scope': 'sale'}).content.decode()
    assert 'Xerox Service' not in html


def test_topbar_search_shows_services_when_the_feature_is_on(client, biz_off):
    _on(biz_off)
    make_service(biz_off, name='Xerox Service')

    url = reverse('global-search', kwargs={'business_slug': biz_off.slug})
    html = client.get(url, {'q': 'xerox', 'scope': 'sale'}).content.decode()
    assert 'Xerox Service' in html
