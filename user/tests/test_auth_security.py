"""Security regressions for the auth surface.

Covers the fixes made 2026-07-28:
  * verify_otp used to call login() on a WRONG code, activating the session and
    making email verification bypassable (SECURITY.md 0.1).
  * OTP codes must be 6 digits from a secure RNG (0.2).
  * The public login view 500'd when POSTed without a username (§6).
"""
import pytest
from django.urls import reverse

from user.models import User, EmailOTP


@pytest.fixture
def pending_owner():
    """An owner who registered but has NOT verified their email yet."""
    return User.objects.create(username='pending_owner', role='owner', is_active=False)


def _start_verify(client, user, *, otp_code='123456'):
    """Put the session into the state verify_otp expects mid-flow."""
    otp = EmailOTP.objects.create(user=user, otp=otp_code)
    session = client.session
    session['user_id'] = user.id
    session['otp_id'] = otp.id
    session.save()
    return otp


# ── 0.1 the bypass ─────────────────────────────────────────────────────────

def test_wrong_otp_does_not_log_the_user_in(client, pending_owner):
    _start_verify(client, pending_owner, otp_code='123456')

    resp = client.post(reverse('verify-otp'), {'otp': '000000'})

    # The regression: a wrong OTP must NOT authenticate or activate the account.
    assert '_auth_user_id' not in client.session
    pending_owner.refresh_from_db()
    assert pending_owner.is_active is False
    assert resp.status_code == 200          # re-renders the verify page, no redirect in


def test_correct_otp_activates_and_logs_in_the_owner(client, pending_owner):
    _start_verify(client, pending_owner, otp_code='123456')

    resp = client.post(reverse('verify-otp'), {'otp': '123456'})

    pending_owner.refresh_from_db()
    assert pending_owner.is_active is True
    assert '_auth_user_id' in client.session
    assert resp.status_code == 302          # → business-profile-create


def test_five_wrong_otps_burn_the_code(client, pending_owner):
    otp = _start_verify(client, pending_owner, otp_code='123456')

    for _ in range(5):
        client.post(reverse('verify-otp'), {'otp': '000000'})

    # After the 5th miss the code is deleted → a 6-digit space can't be brute-forced.
    assert not EmailOTP.objects.filter(id=otp.id).exists()
    assert '_auth_user_id' not in client.session


# ── 0.2 code shape ─────────────────────────────────────────────────────────

def test_generate_otp_is_always_six_digits():
    codes = [EmailOTP.generate_otp() for _ in range(100)]
    assert all(len(c) == 6 and c.isdigit() for c in codes)


# ── §6 public crash guard ──────────────────────────────────────────────────

def test_login_without_username_does_not_500(client):
    # POST omitting the username field used to hit None.lower() → 500.
    resp = client.post(reverse('login'), {'password': 'whatever'})

    assert resp.status_code == 302          # clean redirect back to login
    assert '_auth_user_id' not in client.session
