"""The official invoice header must carry what RMO 24-2023 p.4(a) requires.

Two fields were missing until 2026-07-20: the REGISTERED NAME (the legal entity behind
the trade name — Annex D-2 stacks them as "NICOLE'S SUPERMARKET" over "Operated by:
Facunla Enterprise Inc.") and the machine S/N, which for subscription software is the
software licence number.

These are cheap fields with no logic, so the only thing worth testing is that they
actually reach the printed document — a field nobody renders is the same as no field.
"""
import pytest
from django.urls import reverse

from user.models import BusinessProfile
from tests.factories import make_business, make_product, make_sale


pytestmark = pytest.mark.django_db


@pytest.fixture
def accredited(owner):
    """A business set up the way an ACCREDITED one would be — is_bir_active on, plus
    the BIR-issued identifiers. Without is_bir_active the receipt prints a plain slip
    instead of the official invoice, so the header under test never renders."""
    biz, _plan = make_business(owner, plan='pro')
    biz.is_bir_active = True
    biz.tin = '123-456-789-00000'
    biz.bir_min = '1234567890'
    biz.bir_serial_number = '0987654321-11'
    biz.registered_name = 'Facunla Enterprise Inc.'
    biz.save()
    return biz


def test_the_invoice_header_carries_the_registered_name_and_serial(client, owner, accredited):
    product = make_product(accredited, selling_price='100')
    sale = make_sale(accredited, [(product, 1)])
    client.force_login(owner)

    response = client.get(reverse('sale-receipt', kwargs={
        'business_slug': accredited.slug, 'sale_id': sale.id}))
    html = response.content.decode()

    assert 'Operated by: Facunla Enterprise Inc.' in html
    assert 'S/N: 0987654321-11' in html
    assert 'MIN: 1234567890' in html


def test_the_registered_name_is_omitted_when_it_matches_the_trade_name(client, owner,
                                                                       accredited):
    """A business trading under its own registered name shouldn't print it twice —
    "Operated by" repeating the line above it is noise, not compliance."""
    accredited.registered_name = accredited.business_name
    accredited.save(update_fields=['registered_name'])

    product = make_product(accredited, selling_price='100')
    sale = make_sale(accredited, [(product, 1)])
    client.force_login(owner)

    response = client.get(reverse('sale-receipt', kwargs={
        'business_slug': accredited.slug, 'sale_id': sale.id}))

    assert 'Operated by:' not in response.content.decode()


@pytest.mark.parametrize('as_modal', [True, False], ids=['modal', 'full page'])
@pytest.mark.parametrize('flow', ['edit', 'create'])
def test_the_new_fields_render_on_ALL_FOUR_form_surfaces(client, owner, flow, as_modal):
    """IMPORTANT: A business profile form has FOUR renderings, and I shipped a field to none of
    the ones the owner uses on 2026-07-20 — twice in a row.

    Adding to Meta.fields does nothing here: every template writes its inputs by hand
    (`{{ form.tin }}`). And there is no single template to fix —

        edit   full page : user/business_profile_update.html
        edit   modal     : user/partials/_business_form_modal.html
        create full page : user/business_profile_create.html
        create modal     : user/partials/_business_create_modal.html

    Both views branch on the HX-Request header, and the owner reaches BOTH forms through
    the quickview modal — so the two full-page templates are the ones almost nobody
    opens, and they are exactly what an unparametrised test hits by default.

    Any future field on BusinessProfileForm belongs in this test. Four surfaces, or it
    isn't added.
    """
    client.force_login(owner)
    headers = {'HTTP_HX_REQUEST': 'true'} if as_modal else {}

    if flow == 'edit':
        biz, _plan = make_business(owner, plan='pro')
        url = reverse('business-profile-update', kwargs={
            'business_id': biz.id, 'business_slug': biz.slug})
    else:
        url = reverse('business-profile-create')

    response = client.get(url, **headers)
    html = response.content.decode()

    assert response.status_code == 200
    assert 'name="registered_name"' in html, f"registered_name missing from {flow}/{as_modal}"
    assert 'name="non_vat_type"' in html, f"non_vat_type missing from {flow}/{as_modal}"


def test_non_vat_businesses_default_to_percentage_tax(owner):
    """Under ₱3M on 3% percentage tax is the common case for our clients, so it's the
    default. 'Exempt' is the rare one — RMO p.5(l) scopes it to businesses subject to
    NEITHER VAT nor percentage tax, naming rice, vegetable, fruit, livestock and
    poultry dealers."""
    biz, _plan = make_business(owner)

    assert biz.is_vat_registered is False
    assert biz.non_vat_type == BusinessProfile.NON_VAT_PERCENTAGE


def test_the_three_non_vat_regimes_are_all_expressible(owner):
    """Each prints a different tax block, so the model has to distinguish them:
    percentage-taxable needs no block at all, mixed needs an SSPT vs Exempt split
    (p.5(m)), and fully exempt needs the word EXEMPT shown prominently (p.5(l))."""
    biz, _plan = make_business(owner)

    for value, _label in BusinessProfile.NON_VAT_TYPE_CHOICES:
        biz.non_vat_type = value
        biz.full_clean()          # would raise if the choice weren't valid
        biz.save(update_fields=['non_vat_type'])
        biz.refresh_from_db()
        assert biz.non_vat_type == value
