"""The Sales list must render for a staff member who has no extra permissions.

This page was 500ing for exactly that user for as long as the receivables panel has
existed, and the suite stayed green the whole time — because `sale_list` was only ever
requested as the OWNER (test_htmx_regions, test_away_void_link) and `make_staff` was
only ever pointed at other pages (activity scoping, sell guard, void window). Both
halves were covered; the intersection was not, and the bug lived in the intersection.

The failure: `pending_count` was assigned only inside `if can_view_receivables:` but
read unconditionally when building the context, so a staff member without
`can_handle_receivables` — the DEFAULT for newly added staff — got an
UnboundLocalError instead of the page.

So this is not really a test about drafts. It is a test that the receivables panel
stays OPTIONAL. Everything that block introduces has to be safe to skip, and the
cheapest way to keep proving that is to load the page as someone who skips it.
"""
import pytest
from django.urls import reverse

from tests.factories import make_staff


@pytest.fixture
def staff_without_receivables(business):
    """Plain staff — no can_handle_* flags granted, which is how staff start out."""
    user, _employee = make_staff(business)
    return user


def test_sale_list_renders_for_staff_without_receivables(client, business,
                                                         staff_without_receivables):
    client.force_login(staff_without_receivables)

    response = client.get(reverse('sale-list', kwargs={'business_slug': business.slug}))

    assert response.status_code == 200


def test_the_skipped_panel_leaves_usable_values_behind(client, business,
                                                       staff_without_receivables):
    """Guards the whole class of bug, not just `pending_count`.

    When the panel is skipped its variables must still be present and falsy — a
    template that renders `{{ recv_any_count }}` shouldn't care who is looking. Add a
    new name inside that block without initialising it out here and this fails.
    """
    client.force_login(staff_without_receivables)

    response = client.get(reverse('sale-list', kwargs={'business_slug': business.slug}))
    context = response.context

    assert context['can_view_receivables'] is False, "fixture granted a flag it shouldn't"
    assert context['pending_count'] == 0
    assert context['recv_page_obj'] is None
    assert context['recv_any_count'] == 0
