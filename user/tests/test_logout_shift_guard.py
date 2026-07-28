"""Logout guard — a staff member still clocked in must be steered to time out first.

Walking away without timing out leaves the cash drawer uncounted until the nightly
auto-close, which can only ever record the EXPECTED amount, never what was really in the
till. So the logout link routes through a guard: if the user has an open shift anywhere,
they get the "time out first" modal instead of being logged straight out. `?force=1` is
the deliberate escape hatch the "Log out anyway" button uses.

Owners have no Employee seat, so the guard is invisible to them — pinned here so a future
change to open_shift_for_user can't quietly start blocking an owner's logout.
"""
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from Employee.utils import open_shift_for_user
from tests.factories import make_business, make_staff, make_timecard


@pytest.fixture
def business(owner):
    biz, _plan = make_business(owner, plan='standard')
    return biz


def _logged_in(client):
    return '_auth_user_id' in client.session


# ── The helper ─────────────────────────────────────────────────

def test_open_shift_for_user_finds_an_open_shift(business):
    user, employee = make_staff(business)
    card = make_timecard(business, clock_in=timezone.now(), employee=employee)

    assert open_shift_for_user(user) == card


def test_open_shift_for_user_ignores_a_closed_shift(business):
    user, employee = make_staff(business)
    make_timecard(
        business, employee=employee,
        clock_in=timezone.now() - timedelta(hours=8), clock_out=timezone.now(),
    )

    assert open_shift_for_user(user) is None


def test_open_shift_for_user_is_none_for_an_owner(business, owner):
    """Owners never clock in — the guard must never see them as on-shift."""
    assert open_shift_for_user(owner) is None


# ── The guard on the logout view ───────────────────────────────

def test_clocked_in_staff_gets_the_guard_not_a_logout(client, business):
    user, employee = make_staff(business, is_cashier=True)
    make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    response = client.get(reverse('logout'))

    assert response.status_code == 200          # rendered the guard, did NOT log out
    assert _logged_in(client)                    # still authenticated
    assert b'Time out before you leave' in response.content


def test_cashier_guard_mentions_the_drawer(client, business):
    user, employee = make_staff(business, is_cashier=True)
    make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    response = client.get(reverse('logout'))

    assert response.context['show_drawer'] is True
    assert b'cash drawer can be counted' in response.content


def test_non_cashier_is_timed_out_and_logged_out_without_a_guard(client, business):
    """A sales clerk has no drawer to count, so logout times them out and logs them out
    in one step — no guard modal, no clock-out screen."""
    user, employee = make_staff(business, is_cashier=False)
    card = make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    response = client.get(reverse('logout'))

    assert response.status_code == 302
    assert response.url == reverse('landing')
    assert not _logged_in(client)
    card.refresh_from_db()
    assert card.clock_out is not None            # timed out on the way out
    assert card.auto_closed is False             # their own clean close, not an auto-close
    assert card.close_needs_ack is False         # nothing to review


def test_non_cashier_htmx_logout_times_out_and_hx_redirects(client, business):
    user, employee = make_staff(business, is_cashier=False)
    card = make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    response = client.get(reverse('logout'), HTTP_HX_REQUEST='true')

    assert response.status_code == 204
    assert response['HX-Redirect'] == reverse('landing')
    assert not _logged_in(client)
    card.refresh_from_db()
    assert card.clock_out is not None


def test_cashier_with_reconciliation_off_skips_the_guard(client, owner):
    """No drawer count configured → treat like a non-cashier: immediate time-out + logout."""
    biz, _plan = make_business(owner, plan='standard')
    biz.enable_cash_reconciliation = False
    biz.save(update_fields=['enable_cash_reconciliation'])
    user, employee = make_staff(biz, is_cashier=True)
    card = make_timecard(biz, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    response = client.get(reverse('logout'))

    assert response.status_code == 302
    assert response.url == reverse('landing')
    assert not _logged_in(client)
    card.refresh_from_db()
    assert card.clock_out is not None


def test_htmx_guard_renders_the_modal_partial(client, business):
    user, employee = make_staff(business, is_cashier=True)
    make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    response = client.get(reverse('logout'), HTTP_HX_REQUEST='true')

    assert response.status_code == 200
    assert 'user/partials/_logout_shift_guard_modal.html' in {t.name for t in response.templates}


def test_force_logs_the_clocked_in_staff_out(client, business):
    user, employee = make_staff(business, is_cashier=True)
    make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    response = client.get(reverse('logout') + '?force=1')

    assert response.status_code == 302
    assert response.url == reverse('landing')
    assert not _logged_in(client)


def test_staff_with_no_open_shift_logs_out_normally(client, business):
    user, _employee = make_staff(business)
    client.force_login(user)

    response = client.get(reverse('logout'))

    assert response.status_code == 302
    assert response.url == reverse('landing')
    assert not _logged_in(client)


def test_htmx_logout_with_no_open_shift_sends_hx_redirect(client, business):
    user, _employee = make_staff(business)
    client.force_login(user)

    response = client.get(reverse('logout'), HTTP_HX_REQUEST='true')

    assert response.status_code == 204
    assert response['HX-Redirect'] == reverse('landing')
    assert not _logged_in(client)


def test_owner_logs_out_straight_through(client, business, owner):
    client.force_login(owner)

    response = client.get(reverse('logout'))

    assert response.status_code == 302
    assert response.url == reverse('landing')
    assert not _logged_in(client)


# ── Time-out → log out (the "Time out now" button carries ?next=logout) ──────

def test_clock_out_page_flags_the_logout_intent(client, business):
    user, employee = make_staff(business, is_cashier=False)
    card = make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    url = reverse('shift-clock-out', kwargs={'business_slug': business.slug, 'shift_id': card.id})
    response = client.get(url + '?next=logout')

    assert response.context['logout_after'] is True
    assert b'name="logout_after"' in response.content


def test_timing_out_through_the_logout_flow_logs_the_staff_out(client, business):
    """The whole point: come from logout → time out → count drawer → actually be logged out."""
    user, employee = make_staff(business, is_cashier=False)
    card = make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    url = reverse('shift-clock-out', kwargs={'business_slug': business.slug, 'shift_id': card.id})
    response = client.post(url, {'logout_after': '1'})

    assert response.status_code == 302
    assert response.url == reverse('logout')        # clock-out hands off to logout
    card.refresh_from_db()
    assert card.clock_out is not None               # the shift really closed first

    final = client.get(response.url)                # follow into the (now shift-free) logout
    assert final.status_code == 302
    assert final.url == reverse('landing')
    assert not _logged_in(client)


# ── Time Out renders as a modal over htmx, full page otherwise ──────────────

def test_time_out_opens_a_modal_over_htmx(client, business):
    user, employee = make_staff(business, is_cashier=False)
    card = make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    url = reverse('shift-clock-out', kwargs={'business_slug': business.slug, 'shift_id': card.id})
    response = client.get(url, HTTP_HX_REQUEST='true')

    names = {t.name for t in response.templates}
    assert 'Employee/partials/_clock_out_modal.html' in names
    assert 'Employee/partials/_clock_out_fields.html' in names   # shared fields, not duplicated


def test_time_out_is_a_full_page_without_htmx(client, business):
    user, employee = make_staff(business, is_cashier=False)
    card = make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    url = reverse('shift-clock-out', kwargs={'business_slug': business.slug, 'shift_id': card.id})
    response = client.get(url)

    names = {t.name for t in response.templates}
    assert 'Employee/clock_out.html' in names
    assert 'Employee/partials/_clock_out_fields.html' in names


def test_htmx_time_out_post_hx_redirects_to_the_shift(client, business):
    user, employee = make_staff(business, is_cashier=False)
    card = make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    url = reverse('shift-clock-out', kwargs={'business_slug': business.slug, 'shift_id': card.id})
    response = client.post(url, {}, HTTP_HX_REQUEST='true')

    assert response.status_code == 204
    assert response['HX-Redirect'] == reverse(
        'shift-detail', kwargs={'business_slug': business.slug, 'shift_id': card.id})
    card.refresh_from_db()
    assert card.clock_out is not None


def test_a_plain_time_out_does_not_log_out(client, business):
    """Without the flag, timing out mid-day lands on the shift page and keeps the session."""
    user, employee = make_staff(business, is_cashier=False)
    card = make_timecard(business, clock_in=timezone.now(), employee=employee)
    client.force_login(user)

    url = reverse('shift-clock-out', kwargs={'business_slug': business.slug, 'shift_id': card.id})
    response = client.post(url, {})

    assert response.status_code == 302
    assert response.url == reverse('shift-detail',
                                   kwargs={'business_slug': business.slug, 'shift_id': card.id})
    assert _logged_in(client)
