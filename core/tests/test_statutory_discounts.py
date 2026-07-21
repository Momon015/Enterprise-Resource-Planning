"""SC / PWD / NAAC / Solo Parent discounts are not ordinary discounts.

Three things separate them, and each one is a way to get this wrong:

  1. The RATE IS FIXED BY LAW — 20% for SC, PWD and NAAC; 10% for Solo Parent. The
     cashier doesn't type it.
  2. VAT EXEMPTION DOES NOT RIDE ALONG WITH ALL OF THEM. SC, PWD and Solo Parent are
     exempt; NAAC is a discount only. RMO 24-2023 Annex D-2 backs this up — its VAT
     ADJUSTMENT block lists SC TRANS and PWD TRANS but not NAAC, while its DISCOUNT
     SUMMARY lists all four.
  3. On a VAT-registered seller the exemption must REMOVE the VAT, not relabel it.
     Prices here are VAT-inclusive, so ₱50 VATable is ₱44.64 + ₱5.36. Moving ₱50 into
     the exempt bucket would keep the VAT while calling it exempt — under-relieving the
     customer and overstating exempt sales to BIR.

Most of our clients are NON-VAT, where none of the VAT machinery applies at all and a
statutory discount is just a percentage off. Both worlds are covered below.
"""
from decimal import Decimal

import pytest

from Sales.models import Sale
from tests.factories import make_business, make_product, make_sale


pytestmark = pytest.mark.django_db


@pytest.fixture
def non_vat_business(owner):
    """Hangs off the shared `owner` fixture, NOT its own make_owner() — tests that log in
    and fetch a page need the business to belong to the user they logged in as, or every
    view 404s on the ownership check."""
    biz, _plan = make_business(owner)
    assert biz.is_vat_registered is False
    return biz


@pytest.fixture
def vat_business(owner):
    """plan='pro' because the OFFICIAL invoice is gated on has_receipt_print() as well as
    is_bir_active — on a free plan the receipt view renders the plain sales slip instead,
    and a test asserting on invoice-only markup would fail for the wrong reason."""
    biz, _plan = make_business(owner, plan='pro')
    biz.is_vat_registered = True
    biz.save(update_fields=['is_vat_registered'])
    return biz


# ── the rates are the law's, not the cashier's ──────────────────────────────

@pytest.mark.parametrize('discount_type,expected', [
    (Sale.DISCOUNT_SC,          Decimal('20')),
    (Sale.DISCOUNT_PWD,         Decimal('20')),
    (Sale.DISCOUNT_NAAC,        Decimal('20')),
    (Sale.DISCOUNT_SOLO_PARENT, Decimal('10')),
    (Sale.DISCOUNT_REGULAR,     Decimal('0')),
])
def test_each_type_carries_its_statutory_rate(discount_type, expected):
    assert Sale.statutory_rate(discount_type) == expected


def test_a_50_peso_item_at_a_non_vat_seller(non_vat_business):
    """The simple world, and the one most of our clients live in. No VAT anywhere —
    a statutory discount is just a percentage off the sticker."""
    product = make_product(non_vat_business, selling_price='50')

    pwd = make_sale(non_vat_business, [(product, 1)], discount_percent=20)
    solo = make_sale(non_vat_business, [(product, 1)], discount_percent=10)

    assert pwd.discount_amount == Decimal('10.00'), "20% of 50 is 10, not 20"
    assert pwd.total_revenue == Decimal('40.00')
    assert solo.discount_amount == Decimal('5.00'), "10% of 50 is 5, not 10"
    assert solo.total_revenue == Decimal('45.00')


# ── VAT exemption: who gets it ──────────────────────────────────────────────

@pytest.mark.parametrize('discount_type,exempt', [
    (Sale.DISCOUNT_SC,          True),
    (Sale.DISCOUNT_PWD,         True),
    (Sale.DISCOUNT_SOLO_PARENT, True),
    (Sale.DISCOUNT_NAAC,        False),   # IMPORTANT: discount only — the odd one out
    (Sale.DISCOUNT_REGULAR,     False),
])
def test_vat_exemption_does_not_ride_along_with_every_discount(discount_type, exempt):
    assert (discount_type in Sale.STATUTORY_VAT_EXEMPT) is exempt


def test_the_exemption_removes_the_vat_rather_than_relabelling_it(vat_business):
    """IMPORTANT: The subtle one. A ₱50 VATable sticker is ₱44.64 + ₱5.36 VAT. Under an SC
    exemption the EXEMPT figure must be 44.64 — if it reads 50.00 the VAT was kept and
    merely called exempt."""
    product = make_product(vat_business, selling_price='50')
    sale = make_sale(vat_business, [(product, 1)])
    sale.discount_type = Sale.DISCOUNT_SC
    sale.save(update_fields=['discount_type'])

    summary = sale.vat_summary()

    assert summary['exempt'] == Decimal('44.64'), "VAT was relabelled, not removed"
    assert summary['vatable'] == Decimal('0.00')
    assert summary['vat'] == Decimal('0.00'), "a senior must not be charged VAT"


def test_naac_keeps_the_vat_because_it_has_no_exemption(vat_business):
    """20% off, VAT still due. If this ever starts reading 0.00 VAT, someone has moved
    NAAC into STATUTORY_VAT_EXEMPT — which needs a legal basis, not a hunch."""
    product = make_product(vat_business, selling_price='50')
    sale = make_sale(vat_business, [(product, 1)])
    sale.discount_type = Sale.DISCOUNT_NAAC
    sale.save(update_fields=['discount_type'])

    summary = sale.vat_summary()

    assert summary['vat'] > Decimal('0'), "NAAC has no VAT exemption"
    assert summary['exempt'] == Decimal('0.00')


def test_a_non_vat_seller_strips_nothing(non_vat_business):
    """Its prices never contained VAT, so an exemption has nothing to remove. The ₱50
    stays ₱50 — dividing by 1.12 here would invent a discount nobody granted."""
    product = make_product(non_vat_business, selling_price='50')
    sale = make_sale(non_vat_business, [(product, 1)])
    sale.discount_type = Sale.DISCOUNT_SC
    sale.save(update_fields=['discount_type'])

    assert sale.vat_summary()['exempt'] == Decimal('50.00')


def test_an_already_exempt_line_is_not_divided_twice(vat_business):
    """Medicines are exempt by their own vat_class. They carry no VAT, so an SC sale
    must leave them whole — a second division would silently shrink the line."""
    product = make_product(vat_business, selling_price='50')
    sale = make_sale(vat_business, [(product, 1)])
    sale.sale_items.update(vat_class='exempt')
    sale.discount_type = Sale.DISCOUNT_SC
    sale.save(update_fields=['discount_type'])

    assert sale.vat_summary()['exempt'] == Decimal('50.00')


def test_the_full_senior_computation_at_a_vat_seller(vat_business):
    """End to end, the number a cashier would read off the screen: ₱50 sticker, senior,
    VAT-registered store → ₱35.71. That is 28.6% relief, NOT 20% — the VAT comes off
    as well, and an owner who expects ₱40 has misread the law rather than the app."""
    product = make_product(vat_business, selling_price='50')
    sale = make_sale(vat_business, [(product, 1)], discount_percent=20)
    sale.discount_type = Sale.DISCOUNT_SC
    sale.save(update_fields=['discount_type'])

    exempt_base = sale.vat_summary()['exempt']          # 44.64, after the 20% is applied
    assert exempt_base == Decimal('35.71')


# ── the three-line breakdown (Annex D-2) ────────────────────────────────────

def test_the_breakdown_splits_gross_discount_and_vat_adjustment(vat_business):
    """IMPORTANT: Annex D-2 deducts the discount and the VAT adjustment as SEPARATE lines:

        Gross Amount          ₱50.00
        Less Discount          -₱8.93
        Less VAT Adjustment    -₱5.36
        Net Amount            ₱35.71

    They cannot be merged into one number. The Z reading reports a DISCOUNT SUMMARY and
    a VAT ADJUSTMENT block independently, and the odometer accumulates the gross — so
    all three have to survive as distinct figures.
    """
    parts = Sale.price_breakdown(Decimal('50'), Sale.DISCOUNT_SC,
                                 seller_charges_vat=True)

    assert parts['gross'] == Decimal('50.00')
    assert parts['vat_adjustment'] == Decimal('5.36')
    assert parts['discount_amount'] == Decimal('8.93')
    assert parts['total'] == Decimal('35.71')
    # the identity the receipt depends on
    assert (parts['total'] + parts['discount_amount']
            + parts['vat_adjustment']) == parts['gross']


def test_a_non_vat_seller_has_no_vat_adjustment(non_vat_business):
    """The common case stays exactly as it always was: ₱50 sticker, 20% off, ₱40 paid."""
    parts = Sale.price_breakdown(Decimal('50'), Sale.DISCOUNT_SC,
                                 seller_charges_vat=False)

    assert parts['vat_adjustment'] == Decimal('0.00')
    assert parts['discount_amount'] == Decimal('10.00')
    assert parts['total'] == Decimal('40.00')


def test_naac_at_a_vat_seller_discounts_but_keeps_the_vat(vat_business):
    """No exemption, so nothing is stripped — 20% off the VAT-inclusive price."""
    parts = Sale.price_breakdown(Decimal('50'), Sale.DISCOUNT_NAAC,
                                 seller_charges_vat=True)

    assert parts['vat_adjustment'] == Decimal('0.00')
    assert parts['discount_amount'] == Decimal('10.00')
    assert parts['total'] == Decimal('40.00')


def test_subtotal_stays_the_true_gross_even_when_vat_was_stripped(vat_business):
    """`subtotal` is what the ODOMETER posts, and BIR's accumulated grand total is
    gross. If the VAT adjustment weren't added back, a senior's ₱50 sale would
    accumulate ₱44.64 and the odometer would silently under-report."""
    product = make_product(vat_business, selling_price='50')
    sale = make_sale(vat_business, [(product, 1)])

    sale.discount_type = Sale.DISCOUNT_SC
    sale.discount_percent = Decimal('20')
    sale.vat_adjustment = Decimal('5.36')
    sale.discount_amount = Decimal('8.93')
    sale.total_revenue = Decimal('35.71')
    sale.save()

    assert sale.subtotal == Decimal('50.00')


# ── the 5% basic-necessities band (SC / PWD on groceries) ───────────────────

@pytest.mark.parametrize('discount_type,requested,expected', [
    (Sale.DISCOUNT_SC,          '20', Decimal('20')),
    (Sale.DISCOUNT_SC,          '5',  Decimal('5')),
    (Sale.DISCOUNT_SC,          '3',  Decimal('20')),   # illegal band → default, not 3%
    (Sale.DISCOUNT_SC,          None, Decimal('20')),   # unnamed → default (highest relief)
    (Sale.DISCOUNT_PWD,         '5',  Decimal('5')),
    (Sale.DISCOUNT_SOLO_PARENT, '5',  Decimal('10')),   # Solo Parent has no 5% band
    (Sale.DISCOUNT_NAAC,        '5',  Decimal('20')),   # NAAC has no 5% band
    (Sale.DISCOUNT_REGULAR,     '5',  Decimal('0')),
    ('vip_friend_of_owner',     '5',  Decimal('0')),    # invented type mints nothing
])
def test_resolve_statutory_rate_only_allows_legal_bands(discount_type, requested, expected):
    """The cashier picks a band; a hand-typed rate that isn't legal for the type snaps to
    the type's default band rather than going through."""
    assert Sale.resolve_statutory_rate(discount_type, requested) == expected


@pytest.mark.parametrize('discount_type,rate,exempt', [
    (Sale.DISCOUNT_SC,          Decimal('20'), True),
    (Sale.DISCOUNT_SC,          Decimal('5'),  False),   # basic-necessities band KEEPS VAT
    (Sale.DISCOUNT_PWD,         Decimal('5'),  False),
    (Sale.DISCOUNT_PWD,         Decimal('20'), True),
    (Sale.DISCOUNT_NAAC,        Decimal('20'), False),
    (Sale.DISCOUNT_SOLO_PARENT, Decimal('10'), True),
    (Sale.DISCOUNT_SC,          Decimal('0'),  True),    # unmatched rate → default band
    (Sale.DISCOUNT_REGULAR,     Decimal('5'),  False),
])
def test_statutory_vat_exempt_follows_the_band_not_just_the_type(discount_type, rate, exempt):
    """The 20% and 5% bands of the SAME type differ on VAT: 20% exempts, 5% retains. So the
    exemption cannot key on the type alone — it must read the (type, rate) band."""
    assert Sale.statutory_vat_exempt(discount_type, rate) is exempt


def test_the_five_percent_band_comes_off_the_gross(vat_business):
    """The 5% basic-necessities band is off the GROSS shelf price with NO VAT machinery.
    ₱82 → ₱4.10 off → ₱77.90, and vat_adjustment stays 0 even at a VAT-registered seller —
    the exact opposite of the 20% band, which strips the VAT first."""
    parts = Sale.price_breakdown(Decimal('82'), Sale.DISCOUNT_SC,
                                 seller_charges_vat=True, rate=Decimal('5'))

    assert parts['vat_adjustment'] == Decimal('0.00'), "the 5% band must not strip VAT"
    assert parts['discount_amount'] == Decimal('4.10'), "5% of the GROSS 82 is 4.10"
    assert parts['total'] == Decimal('77.90')
    assert (parts['total'] + parts['discount_amount']
            + parts['vat_adjustment']) == parts['gross']


def test_the_five_percent_band_retains_the_vat_in_the_summary(vat_business):
    """VAT-Exempt Sales stays 0 and the VAT block is populated — a senior buying groceries
    is still charged VAT, just 5% cheaper. If exempt ever reads non-zero here, the band was
    wrongly treated like the 20% one."""
    product = make_product(vat_business, selling_price='82')
    sale = make_sale(vat_business, [(product, 1)], discount_percent=5)
    sale.discount_type = Sale.DISCOUNT_SC
    sale.save(update_fields=['discount_type'])

    summary = sale.vat_summary()

    assert summary['exempt'] == Decimal('0.00'), "the 5% band does not exempt VAT"
    assert summary['vat'] > Decimal('0'), "VAT is retained in the 5% band"


def test_the_five_percent_band_flows_through_the_summary(client, owner):
    """End to end: the cart sends discount_percent=5 with type=sc, and the summary applies
    5% off the gross (not 20%, not VAT-exempt). ₱100 basket → ₱95 due at a non-VAT seller."""
    from django.urls import reverse

    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='100')
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '60',
                                         'selling_price': '100'}}
    session.save()

    response = client.get(
        reverse('view-session-summary', kwargs={'business_slug': biz.slug}),
        {'discount_type': 'sc', 'discount_percent': '5',
         'discount_id_no': '12-3456789', 'discount_name': 'Juan Cruz'},
    )

    assert response.status_code == 200
    assert response.context['discount_percent'] == Decimal('5'), "the 5% band didn't stick"
    assert response.context['discount_amount'] == Decimal('5.00')
    assert response.context['net_total'] == Decimal('95.00')


def test_the_change_readout_base_is_the_discounted_total(client, owner):
    """Regression (2026-07-21): the summary's SALE_TOTAL — the base the cash-change readout
    subtracts the tender from — must be the NET total after discount, not the gross subtotal.
    A ₱100 sale at 5% off is ₱95 due, so ₱200 tendered is ₱105 change; reading the ₱100
    subtotal showed ₱100 change and shortchanged the customer ₱5 on screen."""
    from django.urls import reverse

    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='100')
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '60',
                                         'selling_price': '100'}}
    session.save()

    html = client.get(
        reverse('view-session-summary', kwargs={'business_slug': biz.slug}),
        {'discount_type': 'sc', 'discount_percent': '5'},
    ).content.decode()

    assert 'SALE_TOTAL = 95' in html, "change is measured off the gross subtotal, not the net"
    assert 'SALE_TOTAL = 100' not in html


def test_a_hand_typed_illegal_band_snaps_to_the_default(client, owner):
    """A poked URL asking for SC 3% must not go through — it falls back to the type's
    default 20% band, the same way an invented TYPE is refused."""
    from django.urls import reverse

    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='100')
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '60',
                                         'selling_price': '100'}}
    session.save()

    response = client.get(
        reverse('view-session-summary', kwargs={'business_slug': biz.slug}),
        {'discount_type': 'sc', 'discount_percent': '3'},
    )

    assert response.context['discount_percent'] == Decimal('20'), "an illegal 3% was accepted"


def test_edit_returns_to_the_exact_band(client, owner):
    """Picking SC 5%, confirming, then Edit must come back to SC 5% — not SC 20%. The cart
    reads discount_rate off the state to re-select the right dropdown row."""
    from django.urls import reverse

    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='100')
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '60',
                                         'selling_price': '100'}}
    session.save()

    client.get(reverse('view-session-summary', kwargs={'business_slug': biz.slug}),
               {'discount_type': 'sc', 'discount_percent': '5'})

    state = client.get(reverse('cart-state', kwargs={'business_slug': biz.slug})).json()

    assert state['discount_type'] == 'sc'
    assert Decimal(state['discount_rate']) == Decimal('5'), "Edit would reopen at 20%, not 5%"


# ── the wiring: session → stored Sale ───────────────────────────────────────

def test_a_statutory_type_works_even_when_ordinary_discounts_are_off(client, owner):
    """IMPORTANT: The gate that must NOT apply. `enable_sale_discount` is the owner's preference
    about OPTIONAL discounts. SC and PWD are statutory — a business cannot decline them,
    so a shop with discounts switched off must still serve a senior correctly.

    Driven through the summary URL the cart's Confirm button actually navigates to.
    """
    from django.urls import reverse

    biz, _plan = make_business(owner, plan='pro')
    assert biz.enable_sale_discount is False, "fixture assumption: discounts start off"
    product = make_product(biz, selling_price='50')
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '30',
                                         'selling_price': '50'}}
    session.save()

    response = client.get(
        reverse('view-session-summary', kwargs={'business_slug': biz.slug}),
        {'discount_type': 'sc', 'discount_id_no': '12-3456789',
         'discount_name': 'Juan Cruz', 'discount_percent': '0'},
    )

    assert response.status_code == 200
    assert response.context['discount_percent'] == Decimal('20'), (
        "enable_sale_discount blocked a statutory rate it has no business gating"
    )
    assert response.context['discount_amount'] == Decimal('10.00')
    assert response.context['net_total'] == Decimal('40.00')
    assert response.context['discount_id_no'] == '12-3456789'


def test_an_invented_discount_type_is_refused(client, owner):
    """The type arrives in a query string, so a hand-edited URL must not be able to mint
    a discount category the law doesn't have."""
    from django.urls import reverse

    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='50')
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '30',
                                         'selling_price': '50'}}
    session.save()

    response = client.get(
        reverse('view-session-summary', kwargs={'business_slug': biz.slug}),
        {'discount_type': 'vip_friend_of_owner', 'discount_percent': '90'},
    )

    assert response.context['discount_type'] == ''
    assert response.context['net_total'] == Decimal('50.00'), "a made-up type discounted"


def test_switching_back_to_regular_drops_the_statutory_rate(client, owner):
    """IMPORTANT: User-reported 2026-07-20: pick PWD, change back to Regular, and the 20% stayed.

    The cart only sent `discount_type` when a statutory type was selected, so switching
    back sent NOTHING — and "absent" is not the same as "cleared". The session kept the
    old type and the server happily re-applied 20% to a regular customer.

    The cart now always sends the key, empty when regular. This test drives the empty
    value, which is exactly what the fixed client sends.
    """
    from django.urls import reverse

    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='50')
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '30',
                                         'selling_price': '50'}}
    session['sale_discount_type'] = 'pwd'
    session['sale_discount_id_no'] = '12-3456789'
    session['sale_discount_name'] = 'Juan Cruz'
    session.save()

    response = client.get(
        reverse('view-session-summary', kwargs={'business_slug': biz.slug}),
        {'discount_type': '', 'discount_percent': '0'},
    )

    assert response.context['discount_percent'] == Decimal('0'), "PWD rate survived"
    assert response.context['net_total'] == Decimal('50.00')
    assert response.context['discount_type'] == ''
    # the ID must go with it — it belongs to a customer who has left
    assert response.context['discount_id_no'] == ''
    assert 'sale_discount_type' not in client.session


def test_edit_returns_to_a_cart_that_still_knows_the_customer(client, owner):
    """IMPORTANT: Reported 2026-07-20: pick Senior Citizen, Confirm, then click Edit — the cart came
    back showing "Regular customer" with 20% sitting in the MANUAL discount box.

    Two faults compounding. The cart never restored the type on mount, and the summary view
    wrote the statutory rate into `sale_discount_percent`, the manual slot. So the screen
    claimed a regular customer at 20% while the session still held a senior.

    This drives the JSON the cart re-reads on mount.
    """
    from django.urls import reverse

    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='50')
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '30',
                                         'selling_price': '50'}}
    session.save()

    client.get(reverse('view-session-summary', kwargs={'business_slug': biz.slug}),
               {'discount_type': 'sc', 'discount_id_no': '12-3456789',
                'discount_name': 'Juan Cruz', 'discount_percent': '20'})

    state = client.get(reverse('cart-state', kwargs={'business_slug': biz.slug})).json()

    assert state['discount_type'] == 'sc', "the cart would show 'Regular customer'"
    assert state['discount_id_no'] == '12-3456789'
    assert state['discount_name'] == 'Juan Cruz'
    # and the statutory rate must NOT have leaked into the manual box
    assert Decimal(state['discount_percent']) == Decimal('0'), (
        "20% leaked into the manual discount slot — Edit would show it typed in"
    )


def test_the_id_field_length_matches_end_to_end(client, owner):
    """The input's maxLength, the session truncation and the column width must agree.

    If the browser accepts more than the model stores, a long ID is silently cut and a
    WRONG number is printed on a BIR invoice with nothing on screen to say so. This pins
    the server half; the JSX carries maxLength={60} to match.
    """
    from django.urls import reverse
    from Sales.models import Sale as SaleModel

    assert SaleModel._meta.get_field('discount_id_no').max_length == 60
    assert SaleModel._meta.get_field('discount_name').max_length == 255

    biz, _plan = make_business(owner, plan='pro')
    product = make_product(biz, selling_price='50')
    client.force_login(owner)

    session = client.session
    session['sale'] = {str(product.id): {'quantity': 1, 'cost_price': '30',
                                         'selling_price': '50'}}
    session.save()

    client.get(reverse('view-session-summary', kwargs={'business_slug': biz.slug}),
               {'discount_type': 'sc', 'discount_id_no': 'X' * 200,
                'discount_name': 'Y' * 400, 'discount_percent': '20'})

    # truncated rather than 500ing on a too-long value — but never SILENTLY longer than
    # the column, which would raise at save() time deep inside checkout
    assert len(client.session['sale_discount_id_no']) == 60
    assert len(client.session['sale_discount_name']) == 255


def test_clearing_the_cart_forgets_the_customer(client, owner):
    """IMPORTANT: A leak worth guarding: the ID and rate are per-CUSTOMER, not per-session. If a
    senior's details survived into the next sale, the following shopper would silently
    get 20% off under someone else's OSCA number."""
    from django.urls import reverse

    biz, _plan = make_business(owner, plan='pro')
    client.force_login(owner)

    session = client.session
    session['sale_discount_type'] = 'sc'
    session['sale_discount_id_no'] = '12-3456789'
    session['sale_discount_name'] = 'Juan Cruz'
    session.save()

    client.get(reverse('clear-sale', kwargs={'business_slug': biz.slug}))

    assert 'sale_discount_type' not in client.session
    assert 'sale_discount_id_no' not in client.session
    assert 'sale_discount_name' not in client.session


# ── it has to REACH THE SCREEN, not just the database ───────────────────────

@pytest.fixture
def posted_sc_sale(vat_business, client):
    """A completed SC sale at a VAT-registered seller, with the invoice enabled.

    ₱50 sticker → ₱5.36 VAT off → ₱8.93 discount → ₱35.71. Three figures, and every
    screen has to show all three or the arithmetic reads as broken.
    """
    vat_business.is_bir_active = True
    vat_business.tin = '123-456-789-00000'
    vat_business.save()

    product = make_product(vat_business, selling_price='50')
    sale = make_sale(vat_business, [(product, 1)])
    sale.discount_type = Sale.DISCOUNT_SC
    sale.discount_percent = Decimal('20')
    sale.vat_adjustment = Decimal('5.36')
    sale.discount_amount = Decimal('8.93')
    sale.total_revenue = Decimal('35.71')
    sale.discount_id_no = '12-3456789'
    sale.discount_name = 'Juan Cruz'
    sale.save()
    return vat_business, sale


def test_the_detail_page_shows_the_vat_line_so_the_column_adds_up(client, owner,
                                                                  posted_sc_sale):
    """IMPORTANT: Reported 2026-07-20: the screen read Subtotal 50.00, Discount −8.93, Total 35.71
    and the ₱5.36 was nowhere. Anyone checking the maths sees an error, and an examiner
    sees an unexplained deduction."""
    from django.urls import reverse

    biz, sale = posted_sc_sale
    client.force_login(owner)

    html = client.get(reverse('sale-detail', kwargs={
        'business_slug': biz.slug, 'sale_id': sale.id})).content.decode()

    assert 'VAT exempt' in html, "the VAT adjustment is invisible — the column won't add up"
    assert '5.36' in html
    assert 'SC (' in html, "a bare 'Discount (20%)' doesn't say WHY"
    assert '12-3456789' in html, "no ID means no audit trail for the deduction"
    assert 'Juan Cruz' in html


def test_the_official_invoice_carries_the_id_and_a_signature_line(client, owner,
                                                                  posted_sc_sale):
    """RMO p.5(n) requires the ID, the name, the TIN and a SIGNATURE. The signature is a
    printed rule the customer signs — the one required element the system cannot capture,
    so if it isn't on the paper the invoice is non-compliant."""
    from django.urls import reverse

    biz, sale = posted_sc_sale
    client.force_login(owner)

    html = client.get(reverse('sale-receipt', kwargs={
        'business_slug': biz.slug, 'sale_id': sale.id})).content.decode()

    assert 'SC (' in html, "the SC discount line must name the customer type"
    assert '12-3456789' in html
    assert 'Signature:' in html, "p.5(n) requires a signature line on the invoice"
    assert 'Less VAT (exempt)' in html


def test_a_regular_sale_prints_no_statutory_block(client, owner, vat_business):
    """The ID and signature rows appear only when a statutory discount applies —
    an ordinary sale shouldn't ask a customer to sign for nothing."""
    from django.urls import reverse

    vat_business.is_bir_active = True
    vat_business.save(update_fields=['is_bir_active'])
    product = make_product(vat_business, selling_price='50')
    sale = make_sale(vat_business, [(product, 1)], discount_percent=10)
    client.force_login(owner)

    html = client.get(reverse('sale-receipt', kwargs={
        'business_slug': vat_business.slug, 'sale_id': sale.id})).content.decode()

    assert 'Signature:' not in html
    assert 'Less VAT (exempt)' not in html, "no exemption, so nothing to deduct"
    assert 'Discount (10%)' in html


def test_is_statutory_discount_distinguishes_the_two_kinds(non_vat_business):
    """The receipt prints an ID and signature block for statutory discounts only, and
    the Z reading counts them under their own category instead of 'Other'."""
    product = make_product(non_vat_business, selling_price='50')

    ordinary = make_sale(non_vat_business, [(product, 1)], discount_percent=20)
    statutory = make_sale(non_vat_business, [(product, 1)], discount_percent=20)
    statutory.discount_type = Sale.DISCOUNT_PWD
    statutory.save(update_fields=['discount_type'])

    assert ordinary.is_statutory_discount is False
    assert statutory.is_statutory_discount is True
