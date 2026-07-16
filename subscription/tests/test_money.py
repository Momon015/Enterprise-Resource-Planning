"""The subscription money math.

Every case here is a bug that actually shipped. They are written as tests so they can
only ship once.
"""
from decimal import Decimal
from datetime import timedelta

import pytest
from django.utils import timezone

from subscription.models import BusinessPlan
from tests.factories import make_owner, make_business


def collected_for(sub, business_plan):
    """A year's billing for ONE business, derived from the BILLING path.

    Deliberately reads plan_components() — what the biller charges — and never
    _yearly_monthly_rate(), which is the refund path. The whole point is to check the two
    against each other: the ₱1,500 over-refund existed precisely because the refund path
    priced a business off REGULAR_BASE while billing charged it a surcharge, and nothing
    ever compared them.
    """
    monthly = {bp.pk: m for bp, m in sub.plan_components()}[business_plan.pk]
    return sub._yearly_component(business_plan.plan, monthly) * 12


# ── The refund clock ─────────────────────────────────────────────────────────

def test_refund_clock_starts_when_they_PAID_not_when_the_business_was_created():
    """A shop that ran on Free before upgrading must not be billed for those months.

    BusinessPlan.started_at is the row's birthday — the signal creates it, on Free, the
    moment the BUSINESS is created. Anchoring the refund on it re-priced every month
    since signup rather than since payment, shorting this exact customer by ₱7,495.
    """
    owner, sub = make_owner(billing_cycle='yearly')
    biz, bp = make_business(owner, plan='free')

    # The business has existed on Free for six months...
    bp.started_at = timezone.now() - timedelta(days=180)
    bp.save(update_fields=['started_at'])

    # ...and only now commits to a year of Pro, paid upfront.
    bp.upgrade_to('pro', days=365)
    bp.refresh_from_db()

    assert bp.months_used_on_plan() == 1, "the refund clock must start at payment, not signup"
    assert bp.compute_refund_due() == Decimal('13429.00')


def test_upgrading_resets_the_term_but_never_the_business_birthday():
    owner, _sub = make_owner(billing_cycle='monthly')
    biz, bp = make_business(owner, plan='free')
    birthday = bp.started_at

    bp.upgrade_to('premium', days=30)
    bp.refresh_from_db()

    assert bp.plan_started_at is not None
    assert bp.started_at == birthday, "started_at is the business's birthday and is immutable"
    assert bp.plan_started_at > birthday


def test_downgrading_to_free_closes_the_term():
    owner, _sub = make_owner(billing_cycle='monthly')
    _biz, bp = make_business(owner, plan='pro', months_in=2)
    assert bp.plan_started_at is not None

    bp.downgrade_to_free()
    bp.refresh_from_db()

    assert bp.plan_started_at is None, "no billing term runs on Free"
    assert bp.expires_at is None


# ── Bundle pricing: base vs surcharge ────────────────────────────────────────

def test_only_the_highest_tier_business_pays_the_base_price():
    owner, sub = make_owner(billing_cycle='monthly')
    _a, bp_pro = make_business(owner, plan='pro', name='A')
    _b, bp_std = make_business(owner, plan='standard', name='B')

    components = dict((bp.pk, monthly) for bp, monthly in sub.plan_components())

    assert components[bp_pro.pk] == Decimal('1499')   # base
    assert components[bp_std.pk] == Decimal('150')    # surcharge, NOT the ₱300 base
    assert sub.get_monthly_price() == Decimal('1649.00')


def test_a_bundled_business_is_refunded_at_the_surcharge_it_was_billed():
    """The ₱1,500 leak: the 2nd business was billed ₱150/mo but refunded at ₱300/mo."""
    owner, sub = make_owner(billing_cycle='yearly')
    make_business(owner, plan='pro', name='A')
    _b, bp_std = make_business(owner, plan='standard', name='B', months_in=1)

    assert bp_std._standard_monthly_rate() == Decimal('150.00')
    assert bp_std._yearly_monthly_rate() * 12 == Decimal('1800')   # not ₱3,600
    assert bp_std.compute_refund_due() == Decimal('1650.00')       # not ₱3,300


def test_a_single_pro_business_is_priced_and_refunded_unchanged():
    """Regression guard: fixing the bundle case must not move the single-business case."""
    owner, sub = make_owner(billing_cycle='yearly')
    _biz, bp = make_business(owner, plan='pro', months_in=1)

    assert sub.get_yearly_price() == Decimal('14928.00')
    assert bp.compute_refund_due() == Decimal('13429.00')


# ── The invariant ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('plans', [
    ['pro'],
    ['standard'],
    ['premium'],
    ['pro', 'standard'],
    ['pro', 'premium'],
    ['premium', 'standard'],
    ['pro', 'premium', 'standard'],
])
@pytest.mark.parametrize('months_in', [0, 1, 5, 11])
def test_a_refund_never_exceeds_what_billing_actually_charged(plans, months_in):
    """The invariant that would have caught the surcharge bug on its own.

    Whatever the bundle, whatever the tier, whenever they cancel: we must never hand back
    more money than we took. Nothing else in the refund path enforces this.
    """
    owner, sub = make_owner(billing_cycle='yearly')
    for plan in plans:
        make_business(owner, plan=plan, months_in=months_in)

    for bp, _monthly in sub.plan_components():
        refund = bp.compute_refund_due()
        charged = collected_for(sub, bp)
        assert refund <= charged, (
            f"{bp.plan} in bundle {plans}: refunding ₱{refund} on ₱{charged} collected"
        )
        assert refund >= 0


def test_the_sum_of_the_parts_equals_the_bill():
    """Per-business rates and the account total must be the same arithmetic."""
    owner, sub = make_owner(billing_cycle='yearly')
    for plan in ('pro', 'premium', 'standard'):
        make_business(owner, plan=plan)

    parts = sum((bp._yearly_monthly_rate() for bp, _ in sub.plan_components()), Decimal('0'))
    assert parts * 12 == sub.get_yearly_price()


# ── Access checks must not cost queries ──────────────────────────────────────

def test_checking_a_free_plan_hits_the_database_zero_times(django_assert_num_queries):
    """SubscriptionExpiryMiddleware runs these on EVERY request, once per business.

    They used to call _owner_sub() — a query — before the local fields that already
    settle the answer for every Free plan.
    """
    owner, _sub = make_owner()
    _biz, bp = make_business(owner, plan='free')
    bp = BusinessPlan.objects.get(pk=bp.pk)   # fetched; nothing left to lazy-load

    with django_assert_num_queries(0):
        assert bp.is_expired() is False
        assert bp.is_plan_active() is True

# The cancel-guard bug that used to live here as an xfail is fixed — see
# tests/test_billing_period.py::test_a_paid_business_can_always_be_cancelled.
