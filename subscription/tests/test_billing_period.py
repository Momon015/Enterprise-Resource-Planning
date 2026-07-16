"""The owner-level billing period, and the repricing warning that rides on it."""
from decimal import Decimal
from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from subscription.models import BusinessPlan
from tests.factories import make_owner, make_business


# ── The period is owner-level ────────────────────────────────────────────────

def test_the_first_paid_business_opens_the_owners_term():
    owner, sub = make_owner(billing_cycle='monthly')
    assert sub.has_active_period is False       # all-free owner: no clock running

    _biz, bp = make_business(owner, plan='free')
    bp.upgrade_to('pro')                        # no days= — the bug used to leave NULL here
    sub.refresh_from_db()
    bp.refresh_from_db()

    assert sub.has_active_period is True
    assert sub.current_period_end is not None
    assert bp.expires_at == sub.current_period_end, "expires_at mirrors the owner's period"


def test_a_second_business_joins_the_term_instead_of_starting_its_own():
    """One owner, one clock. Two businesses must not bill on two different dates."""
    owner, sub = make_owner(billing_cycle='yearly')
    _a, bp_a = make_business(owner, plan='free')
    bp_a.upgrade_to('pro')
    sub.refresh_from_db()
    first_end = sub.current_period_end

    _b, bp_b = make_business(owner, plan='free')
    bp_b.upgrade_to('standard')
    sub.refresh_from_db()
    bp_a.refresh_from_db(); bp_b.refresh_from_db()

    assert sub.current_period_end == first_end, "adding a business must not move the term"
    assert bp_a.expires_at == bp_b.expires_at == first_end


def test_a_yearly_cycle_runs_for_a_year_and_monthly_for_a_month():
    owner_y, sub_y = make_owner(billing_cycle='yearly')
    _b, bp = make_business(owner_y, plan='free')
    bp.upgrade_to('pro')
    sub_y.refresh_from_db()
    assert (sub_y.current_period_end - sub_y.current_period_start).days == 365

    owner_m, sub_m = make_owner(billing_cycle='monthly')
    _b2, bp2 = make_business(owner_m, plan='free')
    bp2.upgrade_to('pro')
    sub_m.refresh_from_db()
    assert (sub_m.current_period_end - sub_m.current_period_start).days == 30


def test_losing_the_last_paid_business_stops_the_clock():
    """The biller must skip owners with nothing to charge."""
    owner, sub = make_owner(billing_cycle='monthly')
    _biz, bp = make_business(owner, plan='pro')
    sub.open_period()
    assert sub.has_active_period is True

    bp.downgrade_to_free()
    sub.refresh_from_db()
    assert sub.has_active_period is False


def test_renewing_advances_the_term_and_restarts_the_refund_clock():
    """A yearly renewal is a NEW upfront payment — year two must not be refunded as if
    the customer had been paying since year one."""
    owner, sub = make_owner(billing_cycle='yearly')
    _biz, bp = make_business(owner, plan='pro')
    sub.open_period()
    sub.refresh_from_db()
    first_end = sub.current_period_end

    sub.renew()
    sub.refresh_from_db(); bp.refresh_from_db()

    assert sub.current_period_start == first_end, "the new term starts where the old ended"
    assert (sub.current_period_end - first_end).days == 365
    assert bp.plan_started_at == first_end, "the refund clock restarts on renewal"
    assert bp.expires_at == sub.current_period_end


def test_a_trial_keeps_its_own_expiry_and_is_not_dragged_onto_the_billing_term():
    owner, sub = make_owner(billing_cycle='yearly')
    _a, bp_paid = make_business(owner, plan='free')
    _b, bp_trial = make_business(owner, plan='free')

    bp_trial.start_trial('premium', days=14)
    bp_paid.upgrade_to('pro')                 # opens a 365-day term
    sub.refresh_from_db(); bp_trial.refresh_from_db()

    assert bp_trial.is_trial is True
    assert bp_trial.expires_at != sub.current_period_end
    assert 13 <= (bp_trial.expires_at - timezone.now()).days <= 14


def test_the_biller_can_tell_when_an_owner_is_due():
    owner, sub = make_owner(billing_cycle='monthly')
    make_business(owner, plan='pro')
    sub.open_period()
    assert sub.period_is_due is False

    sub.current_period_end = timezone.now() - timedelta(seconds=1)
    sub.save(update_fields=['current_period_end'])
    assert sub.period_is_due is True


# ── Bug 2, now fixed ─────────────────────────────────────────────────────────

def test_a_paid_business_can_always_be_cancelled():
    """Was BROKEN: upgrade_to() with no explicit term left expires_at NULL, and
    request_cancellation() rejected NULL as 'no active billing cycle' — so a paying
    customer literally could not cancel. The period now always supplies a term end."""
    owner, _sub = make_owner(billing_cycle='monthly')
    _biz, bp = make_business(owner, plan='free')

    bp.upgrade_to('pro')          # how the app actually provisions — no days= passed
    bp.refresh_from_db()

    invoice = bp.request_cancellation()
    assert invoice is not None
    assert invoice.cycle_end_at is not None


# ── The repricing warning ────────────────────────────────────────────────────

def test_cancelling_the_base_business_promotes_the_survivor_to_the_base_rate():
    owner, sub = make_owner(billing_cycle='monthly')
    _a, bp_pro = make_business(owner, plan='pro', name='Main Store')
    _b, bp_std = make_business(owner, plan='standard', name='Side Store')

    changes = sub.reprice_preview(bp_pro)

    assert len(changes) == 1
    bp, old_price, new_price = changes[0]
    assert bp.pk == bp_std.pk
    assert old_price == Decimal('150.00')     # surcharge, while Pro carried the base
    assert new_price == Decimal('300.00')     # promoted to base — the bill goes UP


def test_cancelling_a_surcharge_business_changes_nothing_for_the_others():
    """No warning should fire when nothing actually moves."""
    owner, sub = make_owner(billing_cycle='monthly')
    _a, bp_pro = make_business(owner, plan='pro', name='Main Store')
    _b, bp_std = make_business(owner, plan='standard', name='Side Store')

    assert sub.reprice_preview(bp_std) == []


def test_a_lone_business_has_nothing_to_reprice():
    owner, sub = make_owner(billing_cycle='monthly')
    _biz, bp = make_business(owner, plan='pro')
    assert sub.reprice_preview(bp) == []


def test_the_reprice_preview_does_not_touch_the_database():
    """It's a PREVIEW — shown before the owner has agreed to anything."""
    owner, sub = make_owner(billing_cycle='monthly')
    _a, bp_pro = make_business(owner, plan='pro')
    _b, bp_std = make_business(owner, plan='standard')

    sub.reprice_preview(bp_pro)

    bp_std.refresh_from_db()
    assert bp_std.plan == 'standard'
    assert sub.get_monthly_price() == Decimal('1649.00')   # unchanged


# ── The email ────────────────────────────────────────────────────────────────

def test_the_cancellation_email_spells_out_the_price_rise():
    from subscription.views import _send_cancellation_emails

    owner, sub = make_owner(billing_cycle='monthly')
    owner.email = 'owner@example.com'
    owner.save(update_fields=['email'])

    biz_pro, bp_pro = make_business(owner, plan='pro', name='Main Store')
    _b, _bp_std = make_business(owner, plan='standard', name='Side Store')

    reprice = sub.reprice_preview(bp_pro)
    invoice = bp_pro.request_cancellation()

    mail.outbox = []
    _send_cancellation_emails(owner, biz_pro, invoice, reprice)

    owner_mail = next(m for m in mail.outbox if 'owner@example.com' in m.to)
    assert 'Side Store' in owner_mail.body
    assert '₱150/mo → ₱300/mo' in owner_mail.body
    assert 'paKITA' in owner_mail.body
    assert 'Swift ERP' not in owner_mail.body


def test_no_price_warning_in_the_email_when_no_price_moves():
    from subscription.views import _send_cancellation_emails

    owner, sub = make_owner(billing_cycle='monthly')
    owner.email = 'owner@example.com'
    owner.save(update_fields=['email'])
    biz, bp = make_business(owner, plan='pro')

    reprice = sub.reprice_preview(bp)
    invoice = bp.request_cancellation()

    mail.outbox = []
    _send_cancellation_emails(owner, biz, invoice, reprice)

    owner_mail = next(m for m in mail.outbox if 'owner@example.com' in m.to)
    assert 'HEADS UP' not in owner_mail.body


# ── The modal the owner actually sees ────────────────────────────────────────

def test_the_confirm_modal_renders_the_price_rise(client):
    """Drives the real view and template — the model can be right while the page that
    shows it is broken, and the page is the only part the owner ever reads."""
    owner, _sub = make_owner(billing_cycle='monthly')
    biz_pro, bp_pro = make_business(owner, plan='pro', name='Main Store')
    make_business(owner, plan='standard', name='Side Store')

    client.force_login(owner)
    url = reverse('subscription-cancel-confirm', kwargs={'business_slug': biz_pro.slug})
    resp = client.get(url, {'target_business_id': biz_pro.id}, HTTP_HX_REQUEST='true')
    body = resp.content.decode()

    assert resp.status_code == 200
    assert 'back to the regular price' in body
    assert 'Side Store' in body
    assert '150' in body and '300' in body
