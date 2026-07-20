"""Which day does a draft parked Monday and confirmed Wednesday belong to?

Purely a question about what the code does — no regulation needed to read the answer.
It matters because three things key off Sale.date: which day's revenue it joins, which
day's Z reading it appears on, and (once the grand-total ledger is reporting per day)
which business_date its odometer entry carries.

If the date stays at parking time, a sale that closed Wednesday books to Monday — and
Monday may already be frozen by DailyClose, which is append-only.
"""
from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from Sales.models import Sale, SaleItem
from activity.models import AccumulatedGrandSalesEntry
from tests.factories import make_business, make_product


pytestmark = pytest.mark.django_db

MONDAY = date(2026, 7, 13)
WEDNESDAY = date(2026, 7, 15)


def test_where_a_parked_draft_lands_when_confirmed_later(client, owner, capsys):
    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='100', stock=50)
    client.force_login(owner)

    # parked on Monday — note NO date is passed, because a draft no longer has one
    draft = Sale.objects.create(
        user=biz.user, business=biz, created_by=biz.user,
        status='pending',
        total_revenue=100, total_salary_cost=0, line_count=1,
    )
    assert draft.date is None, "a parked draft must not carry a books date"
    assert draft.reference is None, "a parked draft must not hold an SI number"
    SaleItem.objects.create(sale=draft, product=product, name=product.name,
                            price_at_sale=100, cost_price=60, quantity=1)
    parked_date = draft.date
    parked_ref = draft.reference or "(none)"

    # confirmed on Wednesday (two days later, through the real view)
    client.post(reverse('sale-draft-confirm',
                        kwargs={'business_slug': biz.slug, 'sale_id': draft.id}))
    draft.refresh_from_db()

    entry = AccumulatedGrandSalesEntry.objects.filter(
        business=biz, channel=AccumulatedGrandSalesEntry.CHANNEL_SALE).first()

    print(f"\n\n  parked on          : {parked_date}  ({parked_ref})")
    print(f"  confirmed on       : {WEDNESDAY} (simulated)")
    print(f"  sale.date AFTER    : {draft.date}")
    print(f"  status AFTER       : {draft.status}")
    print(f"  odometer entry date: {entry.business_date if entry else '(none posted)'}")
    print(f"\n  -> books to        : {draft.date}")
    print(f"  -> date moved?     : {'YES' if draft.date != parked_date else 'NO'}\n")

    assert draft.status == 'completed'

    # The sale books to the day it was CONFIRMED, not the day it was parked. Today,
    # because that is when this test ran the confirmation.
    assert draft.date == timezone.localdate(), (
        "a draft confirmed today must book to today — booking it to the parking date "
        "puts revenue on a day that DailyClose may already have frozen"
    )
    assert draft.reference, "confirmation must stamp the SI number"

    # IMPORTANT: The update_fields trap: confirm_sale_draft saves with an explicit
    # update_fields list that does NOT mention date or reference. If save() didn't
    # widen that list, both would be set on the instance and never written — the
    # object in memory would look right and the database would hold NULL.
    from_db = Sale.objects.get(pk=draft.pk)
    assert from_db.date is not None, "date was stamped in memory but never persisted"
    assert from_db.reference, "reference was stamped in memory but never persisted"

    assert entry is not None and entry.business_date == from_db.date, (
        "the odometer entry must land on the same day the sale booked to"
    )
