"""The margin_low alert must survive the next refactor.

This alert has been silently deleted ONCE already: the 2026-07-05 stock rework
removed its emitter and left the pre_save that feeds it in place, so every product
save kept paying for state nobody read and no owner was ever told their margin had
eroded. Nothing failed. Nothing logged. It was found by accident months later.

That is the failure mode these tests exist for. They assert the BEHAVIOUR through
`product.save()` — not that any particular function is wired up — so the alert can
be re-implemented anywhere and still be held to the same contract:

  a rising COST that pushes the margin past a threshold bells the owner,
  and nothing else does.

Margin thresholds come from core/constants: target 30%, danger floor 10%.
At a selling price of 100, cost IS the margin's complement — cost 60 → 40% (good),
cost 80 → 20% (warning), cost 95 → 5% (critical).
"""
import pytest

from activity.models import ActivityEvent
from tests.factories import make_product, make_service


def margin_events(business):
    return ActivityEvent.objects.filter(business=business, verb='product.margin_low')


@pytest.fixture
def product(business):
    """40% margin — comfortably 'good', so any threshold it crosses is a real crossing."""
    return make_product(business, selling_price='100', cost_price='60')


def test_cost_rise_below_target_alerts(business, product):
    product.cost_price = 80              # 40% -> 20%, under the 30% target
    product.save()

    event = margin_events(business).get()
    assert event.is_important, "the whole point is that the owner is told NOW"
    assert event.actor is None, (
        "the supplier raised the price, not whoever received the delivery — and a "
        "null actor is what keeps this off staff feeds (scope_events_for_user)"
    )
    assert 'margin' in event.metadata


def test_owner_cutting_the_price_is_silent(business, product):
    """The discrimination this whole design rests on.

    Same margin drop, same thresholds crossed — but the owner is looking at the
    product form, which already shows a live badge and a suggested price. Belling
    someone about the number under their cursor is noise, and a rule keyed on
    'margin got worse' could not tell these two cases apart.
    """
    product.selling_price = 75           # 40% -> 20%, cost untouched
    product.save()

    assert not margin_events(business).exists()


def test_cost_falling_is_silent(business, product):
    product.cost_price = 40              # margin IMPROVES
    product.save()

    assert not margin_events(business).exists()


def test_alert_does_not_repeat_while_the_product_stays_thin(business, product):
    """Crossing, not state — or restocking a chronically thin item re-alerts forever."""
    product.cost_price = 80              # good -> warning: fires
    product.save()
    product.cost_price = 82              # still warning: silent
    product.save()

    assert margin_events(business).count() == 1


def test_getting_worse_alerts_again(business, product):
    """Warning -> critical is a NEW fact ('close to selling at no profit'), so it earns
    its own bell even though the product was already below target."""
    product.cost_price = 80              # good -> warning
    product.save()
    product.cost_price = 95              # warning -> critical, under the 10% floor
    product.save()

    events = margin_events(business).order_by('id')
    assert [e.metadata['status'] for e in events] == ['warning', 'critical']


def test_services_have_no_margin_to_lose(business):
    """A service has no cost of goods, so `current_margin` is None by design."""
    service = make_service(business, selling_price='20')
    service.cost_price = 19
    service.save()

    assert not margin_events(business).exists()


def test_a_brand_new_product_never_alerts(business):
    """Creation is not a crossing — there is no previous cost to have risen from.
    A thin product added deliberately is a pricing decision, not a surprise."""
    make_product(business, selling_price='100', cost_price='95')

    assert not margin_events(business).exists()
