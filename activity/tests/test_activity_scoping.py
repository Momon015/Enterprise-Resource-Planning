"""The Activity page shows the OWNER everything and staff only their own records.

Staff are blocked from the sale list, purchase history and waste/expense pages —
that's deliberate financial gating. But those transactions are logged as
ActivityEvents with the amounts in `description`, and this page used to hand back
`ActivityEvent.objects.filter(business=business)` with no viewer filter, so it
returned exactly what those gates hide, 20 rows at a time. The per-module
recent-activity panels had been scoped since they were built; this page was never
audited against them, so two subsystems disagreed about what staff may see.

The rule (user, 2026-07-19): "if owner is the user then the owner can see
everything, if staff their only records." `scope_events_for_user` is where that
lives — these tests hold the VIEWS to it.

★ Scoping the list alone is cosmetic, which is why the endpoints are tested too:
both take an event id straight off the URL, so anything hidden from the page was
still reachable by guessing a number.
"""
import pytest
from django.urls import reverse

from activity.models import ActivityEvent
from activity.utils import log_activity
from tests.factories import make_staff


@pytest.fixture
def staff(business):
    user, _employee = make_staff(business)
    return user


@pytest.fixture
def owner_event(business, owner):
    """A purchase the owner recorded — the amount is the thing staff must not read."""
    return log_activity(
        business, owner, 'purchase.recorded',
        description='Purchase PUR-2026-0001 recorded — P12,400.00',
        important=True,
    )


@pytest.fixture
def staff_event(business, staff):
    return log_activity(
        business, staff, 'sale.completed',
        description='Sale SAL-2026-0009 completed — P240.00',
        important=True,
    )


def page_events(client, user, business):
    client.force_login(user)
    response = client.get(reverse('view-all-activity',
                                  kwargs={'business_slug': business.slug}))
    assert response.status_code == 200
    return list(response.context['page_obj'].object_list)


def test_owner_sees_everything(client, owner, business, owner_event, staff_event):
    assert set(page_events(client, owner, business)) == {owner_event, staff_event}


def test_staff_sees_only_their_own(client, staff, business, owner_event, staff_event):
    events = page_events(client, staff, business)

    assert events == [staff_event]
    assert owner_event not in events, "the purchase amount is what the gate hides"


def test_unread_count_matches_what_the_viewer_can_see(client, staff, business,
                                                      owner_event, staff_event):
    """The count drives the 'Mark all read (N)' button. If it counts rows the viewer
    can't see, the button promises to clear more than it clears."""
    client.force_login(staff)
    response = client.get(reverse('view-all-activity',
                                  kwargs={'business_slug': business.slug}))

    assert response.context['unread_count'] == 1


def test_mark_all_read_does_not_clear_the_owners_alerts(client, staff, business,
                                                        owner_event, staff_event):
    """Staff clearing their own bell must not silently mark the owner's alerts read —
    the owner would never learn the alert existed."""
    client.force_login(staff)
    client.post(reverse('mark-all-read',
                        kwargs={'business_slug': business.slug}))

    owner_event.refresh_from_db()
    staff_event.refresh_from_db()
    assert staff_event.is_read
    assert not owner_event.is_read


def test_staff_cannot_open_an_event_by_id(client, staff, business, owner_event):
    """The id comes off the URL, so the list filter alone protects nothing."""
    client.force_login(staff)
    response = client.get(reverse('activity-click', kwargs={
        'business_slug': business.slug, 'event_id': owner_event.id,
    }))

    assert response.status_code == 404
    owner_event.refresh_from_db()
    assert not owner_event.is_read, "a 404 must not have marked it read on the way"


def test_staff_cannot_mark_one_read_by_id(client, staff, business, owner_event):
    client.force_login(staff)
    client.post(reverse('mark-one-read', kwargs={
        'business_slug': business.slug, 'event_id': owner_event.id,
    }))

    owner_event.refresh_from_db()
    assert not owner_event.is_read


def test_staff_still_see_stock_alerts(client, staff, business):
    """Deliberate exception in scope_events_for_user: a null-actor stock alert is
    nobody's "own record", but it is the one thing staff are expected to act on."""
    alert = log_activity(
        business, None, 'stock.out',
        description='Chippy is out of stock', important=True,
    )

    assert alert in page_events(client, staff, business)
