"""Does a cancelled draft leave a GAP in the invoice run?

The owner's scenario, run against the real models and the real draft views:

    1. two pending drafts        -> do they claim numbers?
    2. one completed sale
    3. confirm the first draft   -> does its number CHANGE on confirmation?
    4. another completed sale
    5. cancel the second draft   -> is its number RELEASED or RETAINED?
    6. one final completed sale  -> does it continue the run, or skip?

The thing being measured is the shape of the SI series afterwards. BIR wants a
sequential series of accountable documents; a HOLE in that series (a number that
exists nowhere) is the failure. A number that exists but is marked cancelled is a
different situation entirely, and the whole point of the test is to find out which
one this app produces.

Creation goes through the model rather than the checkout view because the reference
is assigned in Sale.save() ([Sales/models.py:137]) — the view only calls save(), so
the model is the honest place to exercise it. Confirm and cancel go through the real
views, because "is the number released on cancel?" is a question about what those
views DO to the row.
"""
import re

import pytest
from django.urls import reverse

from Sales.models import Sale, SaleItem
from tests.factories import make_business, make_product


pytestmark = pytest.mark.django_db


def _make(business, status):
    """One sale in `status`, built the way checkout builds it."""
    sale = Sale.objects.create(
        user=business.user, business=business, created_by=business.user,
        status=status, total_revenue=0, total_salary_cost=0, line_count=1,
    )
    return sale


def _num(reference):
    """'SI-0000000003' -> 3, so gaps are arithmetic instead of eyeballing zeros."""
    match = re.search(r'(\d+)$', reference or '')
    return int(match.group(1)) if match else None


def test_the_owners_draft_and_cancel_scenario(client, owner, capsys):
    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='100', stock=500)
    client.force_login(owner)

    log = []

    def note(step, sale):
        sale.refresh_from_db()
        log.append((step, sale.id, sale.status, sale.reference))

    # 1 — two pending drafts
    p1 = _make(biz, 'pending')
    note('1. draft A parked', p1)
    p2 = _make(biz, 'pending')
    note('2. draft B parked', p2)

    # 2 — a straight completed sale
    c1 = _make(biz, 'completed')
    note('3. sale C completed', c1)

    # 3 — confirm draft A through the real view
    SaleItem.objects.create(sale=p1, product=product, name=product.name,
                            price_at_sale=100, cost_price=60, quantity=1)
    p1.total_revenue = 100
    p1.save(update_fields=['total_revenue'])
    client.post(reverse('sale-draft-confirm',
                        kwargs={'business_slug': biz.slug, 'sale_id': p1.id}))
    note('4. draft A CONFIRMED', p1)

    # 4 — another completed sale
    c2 = _make(biz, 'completed')
    note('5. sale D completed', c2)

    # 5 — cancel draft B through the real view
    client.post(reverse('sale-draft-cancel',
                        kwargs={'business_slug': biz.slug, 'sale_id': p2.id}),
                {'cancel_reason': 'customer left'})
    note('6. draft B CANCELED', p2)

    # 6 — one more completed sale
    c3 = _make(biz, 'completed')
    note('7. sale E completed', c3)

    # ── report ──────────────────────────────────────────────────────────────
    print("\n\n  STEP                     ID   STATUS      REFERENCE")
    print("  " + "-" * 62)
    for step, sale_id, status, reference in log:
        print(f"  {step:<24} {sale_id:<4} {status:<11} {reference}")

    rows = Sale.objects.filter(business=biz).order_by('id')
    numbers = [_num(s.reference) for s in rows]

    print("\n  FINAL SERIES (every sale row, by id)")
    print("  " + "-" * 62)
    for sale in rows:
        number = _num(sale.reference)
        print(f"  {str(number) if number else '  -':>3}  "
              f"{str(sale.reference or '(none)'):<18} {sale.status:<11} {sale.date}")

    issued = [n for n in numbers if n is not None]
    expected = list(range(min(issued), max(issued) + 1)) if issued else []
    missing = sorted(set(expected) - set(issued))

    print(f"\n  numbers issued : {sorted(issued)}")
    print(f"  contiguous run : {expected}")
    print(f"  MISSING (gaps) : {missing if missing else 'none'}")
    print(f"  rows with no reference: "
          f"{[s.id for s in rows if not s.reference] or 'none'}\n")

    # ── the assertions the scenario was built to settle ─────────────────────
    # Numbers are stamped at COMPLETION, so the SI run contains only sales that were
    # actually invoiced, and it runs in the order customers received them.
    assert not missing, f"HOLE in the invoice run at {missing}"

    # A parked draft holds no number at all — so cancelling it cannot leave a hole,
    # because it never occupied a slot to begin with.
    canceled = rows.get(id=p2.id)
    assert canceled.status == 'canceled'
    assert canceled.reference is None, "a cancelled draft must not hold an SI number"
    assert canceled.date is None, "a cancelled draft never entered the books"

    # The ordering property, which is the real prize here. Invoices were issued in the
    # order C, A, D, E — draft A was parked FIRST but handed over SECOND, so it must
    # carry SI-2, not SI-1. Read the SI run and it matches the order customers were
    # actually served. Under the old rule A held SI-1 and went out after SI-3.
    confirmed = rows.get(id=p1.id)
    assert confirmed.status == 'completed'
    assert _num(confirmed.reference) == 2, (
        f"draft A was the 2nd sale completed but got {confirmed.reference} — the SI "
        f"series is out of chronological order again"
    )
    assert _num(rows.get(id=c1.id).reference) == 1, "sale C completed first, so SI-1"
    assert _num(rows.get(id=c2.id).reference) == 3
    assert _num(rows.get(id=c3.id).reference) == 4

    issued_in_order = [_num(s.reference) for s in
                       rows.filter(status='completed').order_by('reference')]
    assert issued_in_order == sorted(issued_in_order) == [1, 2, 3, 4], (
        "the completed sales should hold a contiguous 1..4 run with no holes"
    )
