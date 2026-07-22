"""The computed X/Z reading (core.utils.reading.compute_reading) + the owner-only page.

A Z reading is a legal document, so the tests here are about the two things that make it
one: the accumulated grand total behaves like an odometer (gross, never rewinds, carries
across days), and the deduction ladder RECONCILES down to active net revenue no matter
what happened in the window — discount, void, return. The render test exists because a
template that 500s is invisible to a pure-Python test of the same numbers.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from activity.models import AccumulatedGrandSalesEntry as AG
from core.utils.reading import compute_reading
from Sales.models import Sale
from tests.factories import (make_business, make_product, make_sale, make_payment,
                             make_staff)


DAY = date(2026, 7, 21)


def ring(business, sale):
    """Mirror checkout: post the sale's GROSS subtotal to the sales odometer."""
    return AG.post(business, AG.CHANNEL_SALE, sale.subtotal, source=sale)


def void_on_odometer(business, sale):
    """Mirror the void hook: post the same gross to the VOID channel."""
    return AG.post(business, AG.CHANNEL_VOID, sale.subtotal, source=sale,
                   business_date=sale.date)


# ── the odometer ────────────────────────────────────────────────────────────

def test_sales_for_day_is_present_minus_previous(business):
    p = make_product(business, selling_price='100')
    ring(business, make_sale(business, [(p, 1)], date=DAY))   # 100
    ring(business, make_sale(business, [(p, 2)], date=DAY))   # 200

    r = compute_reading(business, DAY)
    assert r['previous_accumulated'] == Decimal('0.00')
    assert r['present_accumulated'] == Decimal('300.00')
    assert r['sales_for_day'] == Decimal('300.00')
    assert r['net_amount'] == Decimal('300.00')
    assert r['transaction_count'] == 2


def test_previous_accumulated_carries_from_prior_days(business):
    """Continuity is the odometer's job: today's Previous must equal everything rung
    before today, so consecutive readings chain with no gap."""
    p = make_product(business, selling_price='100')
    ring(business, make_sale(business, [(p, 1)], date=DAY - timedelta(days=1)))  # 100 yest
    ring(business, make_sale(business, [(p, 1)], date=DAY))                      # 100 today

    r = compute_reading(business, DAY)
    assert r['previous_accumulated'] == Decimal('100.00')
    assert r['present_accumulated'] == Decimal('200.00')
    assert r['sales_for_day'] == Decimal('100.00')


# ── the deduction ladder reconciles ─────────────────────────────────────────

def test_discount_is_deducted_and_summarised_by_type(business):
    p = make_product(business, selling_price='100')
    s = make_sale(business, [(p, 1)], date=DAY, discount_percent=20)   # 20 off, net 80
    s.discount_type = 'sc'
    s.save()
    ring(business, s)

    r = compute_reading(business, DAY)
    assert r['gross'] == Decimal('100.00')          # subtotal is gross before discount
    assert r['less_discount'] == Decimal('20.00')
    assert r['net_amount'] == Decimal('80.00')

    sc = next(l for l in r['discount_lines'] if l['label'] == 'SC')
    other = next(l for l in r['discount_lines'] if l['label'] == 'Other')
    assert sc['amount'] == Decimal('20.00')
    assert other['amount'] == Decimal('0.00')


def test_a_void_is_carried_in_gross_then_deducted(business):
    """The odometer never rewinds for the void, and the ladder deducts it back out —
    a same-day ring-and-void must wash to zero, leaving only the good sale."""
    p = make_product(business, selling_price='100')
    good = make_sale(business, [(p, 1)], date=DAY)
    bad = make_sale(business, [(p, 1)], date=DAY)
    ring(business, good)
    ring(business, bad)

    bad.is_void = True
    bad.void_reason = 'test'
    bad.void_reference = 'VD-0000000001'
    bad.save(update_fields=['is_void', 'void_reason', 'void_reference'])
    void_on_odometer(business, bad)

    r = compute_reading(business, DAY)
    assert r['present_accumulated'] == Decimal('200.00')   # odometer keeps climbing
    assert r['sales_for_day'] == Decimal('200.00')         # gross includes the void
    assert r['less_void'] == Decimal('100.00')
    assert r['net_amount'] == Decimal('100.00')            # nets to just the good sale
    assert r['void_count'] == 1
    assert r['transaction_count'] == 1


def test_a_return_is_deducted_from_net(business):
    from Sales.models import SalesReturn

    p = make_product(business, selling_price='100')
    s = make_sale(business, [(p, 1)], date=DAY)
    ring(business, s)
    SalesReturn.objects.create(
        original_sale=s, business=business, date=DAY,
        refund_total=Decimal('40'), refund_cash=Decimal('40'),
    )

    r = compute_reading(business, DAY)
    assert r['less_return'] == Decimal('40.00')
    assert r['net_amount'] == Decimal('60.00')
    assert r['return_count'] == 1


def test_tender_groups_payments_by_method(business):
    p = make_product(business, selling_price='100')
    s1 = make_sale(business, [(p, 1)], date=DAY)
    s2 = make_sale(business, [(p, 1)], date=DAY)
    ring(business, s1)
    ring(business, s2)
    make_payment(s1, '100', method='cash')
    make_payment(s2, '100', method='gcash')

    r = compute_reading(business, DAY)
    labels = {row['label']: row['amount'] for row in r['tender']['payments']}
    assert labels['Cash'] == Decimal('100.00')
    assert labels['GCash'] == Decimal('100.00')
    assert r['tender']['payments_received'] == Decimal('200.00')


def test_mixed_class_statutory_sale_only_strips_vat_from_vatable_lines(business):
    """Regression (the ₱12.85 gap): a statutory VAT-exempt sale (PWD 20%) on a cart with
    BOTH a VATable and a VAT-exempt item must remove VAT from the VATable line only — not
    the whole gross. Otherwise the stored net understates the sale and the Z reading's VAT
    breakdown disagrees with its Net line. The invariant: stored total_revenue == the net
    that vat_summary() reports."""
    business.is_vat_registered = True
    business.save()
    vatable = make_product(business, selling_price='112')   # ₱100 + ₱12 VAT
    exempt = make_product(business, selling_price='50')
    exempt.vat_class = 'exempt'
    exempt.save()

    sale = make_sale(business, [(vatable, 1), (exempt, 1)], date=DAY)   # gross ₱162
    bd = Sale.price_breakdown(Decimal('162'), 'pwd', seller_charges_vat=True,
                              rate=Decimal('20'), vatable_gross=Decimal('112'))
    # Only the VATable line's ₱12 VAT is removed — NOT ₱162/1.12.
    assert bd['vat_adjustment'] == Decimal('12.00')
    sale.discount_type = 'pwd'
    sale.discount_percent = Decimal('20')
    sale.discount_amount = bd['discount_amount']
    sale.vat_adjustment = bd['vat_adjustment']
    sale.total_revenue = bd['total']
    sale.save()

    # The whole point: stored net matches the VAT breakdown's total, so a Z reading built
    # from this sale reconciles (net == VATable-incl + exempt + zero).
    vs = sale.vat_summary()
    assert sale.total_revenue.quantize(Decimal('0.01')) == vs['total'] == Decimal('120.00')


def test_statutory_exemption_keeps_zero_rated_lines_zero_rated(business):
    """A PWD/SC 20% exemption converts VATable sales to Exempt but must NOT sweep zero-rated
    lines into Exempt — they're distinct BIR categories. (The receipt showed a 'Z'-flagged
    line whose amount was counted under Exempt, with Zero-Rated printing 0.00.)"""
    business.is_vat_registered = True
    business.save()
    vatable = make_product(business, selling_price='112')
    zero = make_product(business, selling_price='150')
    zero.vat_class = 'zero'
    zero.save()

    sale = make_sale(business, [(vatable, 1), (zero, 1)], date=DAY)
    sale.discount_type = 'pwd'
    sale.discount_percent = Decimal('20')
    sale.save()

    vs = sale.vat_summary()
    assert vs['vatable'] == Decimal('0.00')            # VATable line moved to exempt
    assert vs['exempt'] == Decimal('80.00')            # 112/1.12=100, less 20% → 80
    assert vs['zero'] == Decimal('120.00')             # 150 stays zero-rated, less 20% → 120


def test_product_list_badges_only_exempt_and_zero_for_vat_seller(client, owner):
    """The list badges the EXCEPTIONS only — VAT-Exempt / Zero-Rated — not plain VATable
    (which is the default and would be noise on every row), and only for a VAT seller."""
    biz, _ = make_business(owner)
    biz.is_vat_registered = True
    biz.save()
    make_product(biz, name='Plain Item', selling_price='100')          # VATable (default)
    ex = make_product(biz, name='Medicine', selling_price='50')
    ex.vat_class = 'exempt'
    ex.save()
    zr = make_product(biz, name='Export Good', selling_price='75')
    zr.vat_class = 'zero'
    zr.save()
    client.force_login(owner)

    resp = client.get(reverse('product-list', kwargs={'business_slug': biz.slug}))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'VAT-Exempt' in html
    assert 'Zero-Rated' in html


def test_product_list_hides_vat_badges_for_non_vat_seller(client, owner):
    biz, _ = make_business(owner)   # non-VAT
    ex = make_product(biz, name='Medicine', selling_price='50')
    ex.vat_class = 'exempt'
    ex.save()
    client.force_login(owner)

    resp = client.get(reverse('product-list', kwargs={'business_slug': biz.slug}))
    assert resp.status_code == 200
    assert 'VAT-Exempt' not in resp.content.decode()


def test_sale_detail_shows_vat_breakdown_for_vat_seller(client, owner):
    """The detail page mirrors the receipt's V / VAT / Exempt / Zero block — including the
    Zero-Rated line that was previously missing there — for a VAT-registered seller."""
    biz, _ = make_business(owner)
    biz.is_vat_registered = True
    biz.save()
    vatable = make_product(biz, selling_price='112')
    zero = make_product(biz, selling_price='150')
    zero.vat_class = 'zero'
    zero.save()
    sale = make_sale(biz, [(vatable, 1), (zero, 1)], date=timezone.localdate())
    client.force_login(owner)

    resp = client.get(reverse('sale-detail', kwargs={'sale_id': sale.id, 'business_slug': biz.slug}))
    assert resp.status_code == 200
    html = resp.content.decode()
    for label in ('VATable Sale (V)', 'VAT (12%)', 'VAT-Exempt (E)', 'Zero-Rated (Z)'):
        assert label in html
    assert '150.00' in html   # the zero-rated line's amount (regular customer, no discount)


def test_sale_detail_hides_vat_breakdown_for_non_vat_seller(client, owner):
    """Non-VAT sellers (the common case) don't see the block — vat_summary is None."""
    biz, _ = make_business(owner)   # non-VAT by default
    p = make_product(biz, selling_price='100')
    sale = make_sale(biz, [(p, 1)], date=timezone.localdate())
    client.force_login(owner)

    resp = client.get(reverse('sale-detail', kwargs={'sale_id': sale.id, 'business_slug': biz.slug}))
    assert resp.status_code == 200
    assert 'VATable Sale (V)' not in resp.content.decode()


def test_mixed_statutory_cart_preview_and_summary_agree(client, owner):
    """The React cart preview strips VAT from the server's `vatable_subtotal`, and the
    summary that Confirm recomputes agrees. Guards the preview bug (₱212.89 vs the correct
    ₱57.54 on the live screen): a PWD 20% cart with a VATable + VAT-exempt item must relieve
    VAT on the VATable line only. ₱112 vatable + ₱50 exempt → net ₱120, not ₱115.71."""
    from django.urls import reverse
    biz, _ = make_business(owner, plan='pro')
    biz.is_vat_registered = True
    biz.save()
    vatable = make_product(biz, selling_price='112')
    exempt = make_product(biz, selling_price='50')
    exempt.vat_class = 'exempt'
    exempt.save()
    client.force_login(owner)

    session = client.session
    session['sale'] = {
        str(vatable.id): {'quantity': 1, 'cost_price': '0', 'selling_price': '112'},
        str(exempt.id):  {'quantity': 1, 'cost_price': '0', 'selling_price': '50'},
    }
    session.save()

    # The preview feeds off this: only the VATable line counts toward vatable_subtotal.
    state = client.get(reverse('cart-state', kwargs={'business_slug': biz.slug})).json()
    assert state['subtotal'] == '162.00'
    assert state['vatable_subtotal'] == '112.00'

    # The server summary (what Confirm stores) reconciles to the same numbers.
    resp = client.get(reverse('view-session-summary', kwargs={'business_slug': biz.slug}),
                      {'discount_type': 'pwd', 'discount_percent': '20',
                       'discount_id_no': 'PWD-1', 'discount_name': 'Juan'})
    assert resp.context['vat_adjustment'] == Decimal('12.00')   # not 162/1.12 worth
    assert resp.context['net_total'] == Decimal('120.00')       # not the buggy 115.71


def test_an_empty_day_reads_all_zeros(business):
    r = compute_reading(business, DAY)
    assert r['sales_for_day'] == Decimal('0.00')
    assert r['net_amount'] == Decimal('0.00')
    assert r['transaction_count'] == 0
    assert r['si_beg'] == '—' and r['si_end'] == '—'


# ── the owner-only page renders (guards against a template 500) ──────────────

def test_owner_can_open_the_z_reading_page(client, owner):
    biz, _ = make_business(owner)
    p = make_product(biz, selling_price='100')
    ring(biz, make_sale(biz, [(p, 1)], date=timezone.localdate()))
    client.force_login(owner)

    resp = client.get(reverse('z-reading', kwargs={'business_slug': biz.slug}))
    assert resp.status_code == 200
    assert b'Z READING' in resp.content


def test_staff_cannot_open_the_z_reading_page(client, business):
    """A Z reading is an owner-only financial surface — staff never render it."""
    staff_user, _emp = make_staff(business)
    client.force_login(staff_user)

    resp = client.get(reverse('z-reading', kwargs={'business_slug': business.slug}))
    assert resp.status_code in (302, 403, 404)
    assert b'END OF Z READING' not in resp.content


# ── the list landing + modal ─────────────────────────────────────────────────

def test_the_list_shows_a_row_per_trading_day(client, owner):
    biz, _ = make_business(owner)
    p = make_product(biz, selling_price='100')
    make_sale(biz, [(p, 1)], date=DAY)
    make_sale(biz, [(p, 1)], date=DAY - timedelta(days=2))
    client.force_login(owner)

    resp = client.get(reverse('z-reading-list', kwargs={'business_slug': biz.slug}))
    assert resp.status_code == 200
    days = resp.context['days']
    assert {d['date'] for d in days} == {DAY, DAY - timedelta(days=2)}

    # The View buttons live inside the htmx-boosted #z-reading-results region, so they MUST
    # cancel the inherited hx-select — otherwise htmx lifts that (absent) node out of the
    # modal response and the modal opens blank. Regression guard for that.
    html = resp.content.decode()
    assert 'id="z-reading-results"' in html and 'hx-select="#z-reading-results"' in html
    assert 'hx-select="unset"' in html, 'View button must clear inherited hx-select'


def test_the_list_net_matches_the_reading_it_opens(client, owner):
    """The figure on the list row must equal the Net Amount inside the reading — one
    number, computed one way, so the row and the document never disagree."""
    biz, _ = make_business(owner)
    p = make_product(biz, selling_price='100')
    s = make_sale(biz, [(p, 1)], date=DAY, discount_percent=20)   # net 80
    s.discount_type = 'sc'
    s.save()
    ring(biz, s)
    client.force_login(owner)

    resp = client.get(reverse('z-reading-list', kwargs={'business_slug': biz.slug}))
    row = next(d for d in resp.context['days'] if d['date'] == DAY)
    assert row['net'] == compute_reading(biz, DAY)['net_amount']


def test_the_modal_frames_the_reading(client, owner):
    biz, _ = make_business(owner)
    p = make_product(biz, selling_price='100')
    make_sale(biz, [(p, 1)], date=timezone.localdate())
    client.force_login(owner)

    resp = client.get(reverse('z-reading-modal', kwargs={'business_slug': biz.slug}))
    assert resp.status_code == 200
    assert b'quickview__panel' in resp.content   # opens in the global confirm modal
    assert b'zrFrame' in resp.content            # frames the printable doc


def test_staff_cannot_open_the_z_reading_list(client, business):
    staff_user, _emp = make_staff(business)
    client.force_login(staff_user)

    resp = client.get(reverse('z-reading-list', kwargs={'business_slug': business.slug}))
    assert resp.status_code in (302, 403, 404)


# ── sealing: the Z becomes a pen-not-pencil record ───────────────────────────
#
# What makes a Z a Z (vs the re-computable X preview): it FREEZES a finished day into an
# append-only record with a burned Z counter, and that snapshot never restates afterwards.

def _yesterday():
    return timezone.localdate() - timedelta(days=1)


def test_seal_freezes_a_finished_day_into_a_z(business, owner):
    from Sales.models import ZReading
    day = _yesterday()
    p = make_product(business, selling_price='100')
    ring(business, make_sale(business, [(p, 1)], date=day))
    ring(business, make_sale(business, [(p, 2)], date=day))

    zr, created = ZReading.seal(business, day, user=owner)
    assert created is True
    assert zr.z_counter == 1
    assert zr.reference == 'Z-0000000001'
    # The sealed figure equals the live reading it was sealed from.
    assert zr.net_amount == compute_reading(business, day)['net_amount'] == Decimal('300.00')
    # The whole reading is frozen in the snapshot, money kept exact as strings.
    assert zr.snapshot['net_amount'] == '300.00'
    assert zr.snapshot['present_accumulated'] == '300.00'


def test_seal_is_idempotent(business, owner):
    """Re-sealing the same day is a no-op — one Z per day, no second counter burned."""
    from Sales.models import ZReading
    day = _yesterday()
    p = make_product(business, selling_price='100')
    ring(business, make_sale(business, [(p, 1)], date=day))

    zr1, created1 = ZReading.seal(business, day, user=owner)
    zr2, created2 = ZReading.seal(business, day, user=owner)
    assert created1 is True and created2 is False
    assert zr1.pk == zr2.pk
    assert ZReading.objects.filter(business=business, date=day).count() == 1


def test_a_sealed_day_never_restates(client, business, owner):
    """Pen, not pencil: a sale rung into a day AFTER it was sealed must not change the
    sealed Z — the frozen snapshot renders, not a live recompute."""
    from Sales.models import ZReading
    day = _yesterday()
    p = make_product(business, selling_price='100')
    ring(business, make_sale(business, [(p, 1)], date=day))     # net 100

    zr, _ = ZReading.seal(business, day, user=owner)
    assert zr.net_amount == Decimal('100.00')

    # Something changes on that day afterwards (a late-posted sale).
    ring(business, make_sale(business, [(p, 5)], date=day))     # would push net to 600 live
    assert compute_reading(business, day)['net_amount'] == Decimal('600.00')  # live moved
    assert ZReading.for_day(business, day).net_amount == Decimal('100.00')    # sealed did not

    # The rendered document shows the FROZEN figure, not the recomputed one.
    client.force_login(owner)
    resp = client.get(reverse('z-reading', kwargs={'business_slug': business.slug}),
                      {'date': day.isoformat()})
    html = resp.content.decode()
    assert 'Z-0000000001' in html                # sealed → prints the Z counter
    assert 'PREVIEW' not in html                 # sealed → not a preview
    assert '100.00' in html and '600.00' not in html


def test_cannot_seal_today_or_the_future(business, owner):
    from Sales.models import ZReading
    p = make_product(business, selling_price='100')
    ring(business, make_sale(business, [(p, 1)], date=timezone.localdate()))
    with pytest.raises(ValueError):
        ZReading.seal(business, timezone.localdate(), user=owner)
    with pytest.raises(ValueError):
        ZReading.seal(business, timezone.localdate() + timedelta(days=1), user=owner)


def test_z_counter_increments_across_days(business, owner):
    from Sales.models import ZReading
    p = make_product(business, selling_price='100')
    d1, d2 = _yesterday() - timedelta(days=1), _yesterday()
    ring(business, make_sale(business, [(p, 1)], date=d1))
    ring(business, make_sale(business, [(p, 1)], date=d2))

    z1, _ = ZReading.seal(business, d1, user=owner)
    z2, _ = ZReading.seal(business, d2, user=owner)
    assert z1.z_counter == 1 and z2.z_counter == 2


# ── chronological seal guard — days must seal oldest-first ────────────────────

def test_cannot_seal_out_of_order(business, owner):
    """Sealing a newer day while an older one is still open is refused, so the Z counter
    can never run out of calendar order."""
    from Sales.models import ZReading
    p = make_product(business, selling_price='100')
    older, newer = _yesterday() - timedelta(days=1), _yesterday()
    ring(business, make_sale(business, [(p, 1)], date=older))
    ring(business, make_sale(business, [(p, 1)], date=newer))

    with pytest.raises(ValueError):
        ZReading.seal(business, newer, user=owner)          # older still open → blocked

    # Sealing in order works, and unblocks the next day.
    ZReading.seal(business, older, user=owner)
    zr, created = ZReading.seal(business, newer, user=owner)
    assert created is True and zr.z_counter == 2


def test_earliest_unsealed_day_ignores_voided_only_days(business, owner):
    """A day whose only sale was voided isn't a reachable trading day (it never shows on the
    list), so it must not become an un-sealable blocker for every later day."""
    from Sales.models import ZReading
    p = make_product(business, selling_price='100')
    void_only = _yesterday() - timedelta(days=1)
    good = _yesterday()

    bad = make_sale(business, [(p, 1)], date=void_only)
    ring(business, bad)
    bad.is_void = True
    bad.void_reason = 'test'
    bad.save(update_fields=['is_void', 'void_reason'])
    void_on_odometer(business, bad)

    ring(business, make_sale(business, [(p, 1)], date=good))

    # The all-void day is skipped; the good day is the earliest sealable one.
    assert ZReading.earliest_unsealed_day(business) == good
    zr, created = ZReading.seal(business, good, user=owner)   # not blocked by the void day
    assert created is True


def test_modal_only_offers_seal_on_the_earliest_open_day(client, owner):
    from Sales.models import ZReading
    biz, _ = make_business(owner)
    p = make_product(biz, selling_price='100')
    older, newer = _yesterday() - timedelta(days=1), _yesterday()
    ring(biz, make_sale(biz, [(p, 1)], date=older))
    ring(biz, make_sale(biz, [(p, 1)], date=newer))
    client.force_login(owner)

    url = reverse('z-reading-modal', kwargs={'business_slug': biz.slug})
    newer_resp = client.get(url, {'date': newer.isoformat()})
    assert newer_resp.context['can_seal'] is False
    assert newer_resp.context['earliest_unsealed'] == older
    assert b'id="zrSealStart"' not in newer_resp.content
    assert b'sealed in date order' in newer_resp.content     # tells them what to seal first

    older_resp = client.get(url, {'date': older.isoformat()})
    assert older_resp.context['can_seal'] is True
    assert b'id="zrSealStart"' in older_resp.content


def test_seal_view_refuses_out_of_order_post(client, owner):
    """Even a hand-crafted POST to a newer day is refused while an older day is open."""
    from Sales.models import ZReading
    biz, _ = make_business(owner)
    p = make_product(biz, selling_price='100')
    older, newer = _yesterday() - timedelta(days=1), _yesterday()
    ring(biz, make_sale(biz, [(p, 1)], date=older))
    ring(biz, make_sale(biz, [(p, 1)], date=newer))
    client.force_login(owner)

    seal_newer = f"{reverse('z-reading-seal', kwargs={'business_slug': biz.slug})}?date={newer.isoformat()}"
    resp = client.post(seal_newer)
    assert resp.status_code == 200
    assert not ZReading.objects.filter(business=biz, date=newer).exists()   # nothing sealed
    assert b'sealed in date order' in resp.content


def test_list_flags_the_ready_to_seal_day(client, owner):
    biz, _ = make_business(owner)
    p = make_product(biz, selling_price='100')
    older, newer = _yesterday() - timedelta(days=1), _yesterday()
    ring(biz, make_sale(biz, [(p, 1)], date=older))
    ring(biz, make_sale(biz, [(p, 1)], date=newer))
    client.force_login(owner)

    resp = client.get(reverse('z-reading-list', kwargs={'business_slug': biz.slug}))
    rows = {d['date']: d for d in resp.context['days']}
    assert rows[older]['ready_to_seal'] is True
    assert rows[newer]['ready_to_seal'] is False
    assert b'Ready to seal' in resp.content


def test_a_sealed_z_is_append_only(business, owner):
    from Sales.models import ZReading
    day = _yesterday()
    p = make_product(business, selling_price='100')
    ring(business, make_sale(business, [(p, 1)], date=day))
    zr, _ = ZReading.seal(business, day, user=owner)

    with pytest.raises(ValueError):
        zr.net_amount = Decimal('999')
        zr.save()
    with pytest.raises(ValueError):
        zr.delete()


# ── the seal flow through the views ──────────────────────────────────────────

def test_modal_offers_seal_for_a_past_unsealed_day(client, owner):
    biz, _ = make_business(owner)
    day = _yesterday()
    p = make_product(biz, selling_price='100')
    ring(biz, make_sale(biz, [(p, 1)], date=day))
    client.force_login(owner)

    resp = client.get(reverse('z-reading-modal', kwargs={'business_slug': biz.slug}),
                      {'date': day.isoformat()})
    assert resp.status_code == 200
    assert resp.context['can_seal'] is True
    assert b'id="zrSealStart"' in resp.content        # the Seal button is offered


def test_modal_offers_no_seal_for_today(client, owner):
    biz, _ = make_business(owner)
    p = make_product(biz, selling_price='100')
    ring(biz, make_sale(biz, [(p, 1)], date=timezone.localdate()))
    client.force_login(owner)

    resp = client.get(reverse('z-reading-modal', kwargs={'business_slug': biz.slug}),
                      {'date': timezone.localdate().isoformat()})
    assert resp.context['can_seal'] is False
    assert b'id="zrSealStart"' not in resp.content   # no Seal button (the JS ref stays)


def test_seal_view_seals_and_flags_the_list_to_refresh(client, owner):
    from Sales.models import ZReading
    biz, _ = make_business(owner)
    day = _yesterday()
    p = make_product(biz, selling_price='100')
    ring(biz, make_sale(biz, [(p, 1)], date=day))
    client.force_login(owner)

    # date rides the query string (that's how the hx-post URL supplies it).
    seal_url = f"{reverse('z-reading-seal', kwargs={'business_slug': biz.slug})}?date={day.isoformat()}"
    resp = client.post(seal_url)
    assert resp.status_code == 200
    assert ZReading.objects.filter(business=biz, date=day).exists()
    html = resp.content.decode()
    assert 'Sealed as' in html                       # modal re-rendered in sealed state
    assert 'data-reload-on-close' in html            # closing refreshes the list
    assert 'id="zrSealStart"' not in html            # no seal button once sealed

    # Re-posting is idempotent and no longer asks the list to reload.
    resp2 = client.post(seal_url)
    assert 'Sealed as' in resp2.content.decode()
    assert 'data-reload-on-close' not in resp2.content.decode()
    assert ZReading.objects.filter(business=biz, date=day).count() == 1


def test_staff_cannot_seal(client, business):
    from Sales.models import ZReading
    staff_user, _emp = make_staff(business)
    day = _yesterday()
    client.force_login(staff_user)

    seal_url = f"{reverse('z-reading-seal', kwargs={'business_slug': business.slug})}?date={day.isoformat()}"
    resp = client.post(seal_url)
    assert resp.status_code in (302, 403, 404)
    assert not ZReading.objects.filter(business=business, date=day).exists()


def test_list_shows_the_sealed_chip(client, owner):
    from Sales.models import ZReading
    biz, _ = make_business(owner)
    day = _yesterday()
    p = make_product(biz, selling_price='100')
    ring(biz, make_sale(biz, [(p, 1)], date=day))
    ZReading.seal(biz, day, user=owner)
    client.force_login(owner)

    resp = client.get(reverse('z-reading-list', kwargs={'business_slug': biz.slug}))
    row = next(d for d in resp.context['days'] if d['date'] == day)
    assert row['sealed'] is not None
    assert b'Sealed' in resp.content
