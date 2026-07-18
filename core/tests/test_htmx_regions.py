"""No control inside a live filter region may inherit that region's hx-select.

The invariant: an element with its OWN hx-get/hx-post and its OWN hx-target sends its
response SOMEWHERE ELSE (a modal, a cart badge). It must not also inherit the enclosing
list region's hx-select, or htmx will try to lift the region's id out of that other
response, find nothing, and swap EMPTY.

This shipped on 2026-07-17. Clicking Archive on the product list dimmed the page and
opened an empty modal: the button's own hx-get went to #confirmBody, but it silently
inherited hx-select="#product-results" from the region wrapper, and the modal fragment
has no #product-results in it. main.html's htmx:afterSwap opens the modal whether or not
anything landed in it, so the failure renders as a blank panel. Nothing is logged.

Why it needs a test rather than care: the mistaken belief that caused it is reasonable
and durable — "hx-boost='false' means htmx leaves this subtree alone". It does not.
Boosting and inheritance are different mechanisms, and hx-boost only speaks to the first.
The same wrong reasoning was written into THREE templates in one afternoon, and the two
payment panels (_receivables_panel, _payables_panel) shipped broken because the pages
they live on had no modal open in the review. Every future conversion re-runs this risk,
and the symptom is invisible server-side: the HTML is valid, the view is correct, the
response is 200.

The fix is hx-disinherit on the container. This test doesn't check for that attribute —
it checks for the CONSEQUENCE, so any other correct fix (hx-select="unset" on the child,
htmx's disableInheritance config) also passes.
"""
from html.parser import HTMLParser

import pytest
from django.urls import reverse

from django.utils import timezone

from tests.factories import (make_business, make_employee, make_product, make_purchase,
                             make_sale, make_service, make_stock, make_supplier,
                             make_timecard)

# Every list converted to the hx-boost + hx-select live-region pattern.
# ADD THE NEXT CONVERSION HERE — that is the whole point of this file.
CONVERTED_LISTS = [
    "product-list",
    "category-list",
    "service-list",
    "view-inventory-stock",
    "material-list",
    "supplier-list",
    "employee-list",
    "sale-list",
    "expense-list",
    "expense-waste-list",
    "sales-receivables",
    "purchase-payables",
    "sales-return-list",
    "purchase-return-list",
]

# shift-dashboard (Timecards) is ALSO converted, but it can't ride CONVERTED_LISTS: the page is
# gated to Standard+ (has_timecards) and 404/redirects on the free-plan stocked_business, and it
# needs a real ShiftEmployee row to render its table. It gets its own test below instead —
# test_shift_dashboard_region_has_no_leaks — so this list stays "one free-plan GET per entry".

VOID_TAGS = {"img", "br", "hr", "input", "meta", "link", "source", "area", "base",
             "col", "embed", "wbr", "track", "param"}


class InheritanceWalker(HTMLParser):
    """Resolve hx-select the way htmx itself does.

    Mirrors the vendored htmx 2.0.3 (static/js/htmx.min.js), which is the only
    authority that matters here:

        function H(e,t,n){ const r=te(t,n); const o=te(t,"hx-disinherit");
          if(e!==t){ if(o&&(o==="*"||o.split(" ").indexOf(n)>=0)){return "unset"} }
          return r }
        function re(t,n){ let r=null; i(t,e=>!!(r=H(t,ue(e),n)));
          if(r!=="unset"){return r} }

    i.e. walk ancestors from the element upward and take the first non-null value;
    an ancestor carrying hx-disinherit short-circuits to "unset", which re() maps to
    "no value". An element's OWN attribute is found first and always wins.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []          # [(tag, attrs), ...] — open elements, outermost first
        self.leaks = []
        self.hx_seen = 0         # how many hx-get/hx-post elements we examined

    # -- parser plumbing ---------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in VOID_TAGS:
            self.stack.append((tag, a))
            self._check(tag, a, self_on_stack=True)
        else:
            self._check(tag, a, self_on_stack=False)

    def handle_startendtag(self, tag, attrs):
        self._check(tag, dict(attrs), self_on_stack=False)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    # -- the actual rule ---------------------------------------------------------
    def _check(self, tag, a, *, self_on_stack):
        if "hx-get" not in a and "hx-post" not in a:
            return
        self.hx_seen += 1

        if "hx-select" in a:
            return                              # own value wins; nothing is inherited

        own_target = a.get("hx-target")
        if own_target is None:
            # Inherits target AND select together, so it is deliberately driving the
            # region it sits in (the receivables/payables status <select>, a KPI card).
            # Consistent by construction — not a leak.
            return

        ancestors = self.stack[:-1] if self_on_stack else self.stack
        for anc_tag, anc in reversed(ancestors):
            disinherit = anc.get("hx-disinherit")
            if disinherit and (disinherit == "*" or "hx-select" in disinherit.split()):
                return                          # walk is cut here — safe
            region = anc.get("hx-select")
            if region is None:
                continue
            if region == own_target:
                return                          # same destination; nothing to break
            self.leaks.append(
                f"<{tag} hx-target={own_target}> sends its response to {own_target}, "
                f"but would inherit hx-select={region} from "
                f"<{anc_tag} id={anc.get('id')}> — htmx will look for {region} in that "
                f"response, not find it, and swap EMPTY. "
                f"url={a.get('hx-get') or a.get('hx-post')}"
            )
            return


def walk(html):
    w = InheritanceWalker()
    w.feed(html)
    return w


@pytest.fixture
def stocked_business(owner):
    """A business with enough on it that the converted pages render real controls.

    EVERY ROW HERE IS LOAD-BEARING, none of it is scenery. These pages render their
    riskiest controls only when they have something to list — the payment panels' "Add
    payment" button, a table's per-row edit/archive/add-to-cart. With an empty table the
    page falls back to an empty state and the test sails through having checked nothing.

    This is not hypothetical, it was measured twice:
      - with no purchase, reverting the fix in _payables_panel failed NOTHING;
      - with no service, service_list rendered its empty state and never exercised the
        <tbody> guard at all.
    If you add a page to CONVERTED_LISTS, add whatever it needs to render ROWS.
    """
    biz, _plan = make_business(owner)
    product = make_product(biz, selling_price="100", cost_price="60", stock=25)
    make_service(biz, selling_price="20")   # Product.services -> service_list has a row
    make_sale(biz, [(product, 2)])          # unpaid -> a receivable
    make_purchase(biz, total_cost="500")    # unpaid -> a payable
    make_stock(biz, quantity=25)            # a Material+Stock row -> stock list has a row
    make_supplier(biz)                      # a vendor row -> supplier list has a row
    make_employee(biz)                       # a staff row -> employee list has a row
    return biz


@pytest.mark.parametrize("url_name", CONVERTED_LISTS)
def test_no_control_inherits_its_regions_hx_select(client, owner, stocked_business, url_name):
    client.force_login(owner)
    response = client.get(reverse(url_name, kwargs={"business_slug": stocked_business.slug}))
    assert response.status_code == 200, f"{url_name} -> {response.status_code}"

    result = walk(response.content.decode())
    assert not result.leaks, (
        f"{url_name}: {len(result.leaks)} control(s) would swap an empty response.\n  "
        + "\n  ".join(result.leaks)
    )


def test_empty_state_ctas_are_guarded_too(client, owner):
    """A page's EMPTY state has its own controls, and the row fixture hides them.

    stocked_business gives service_list a row, which is what exercises its <tbody>. But
    that same row means the empty state never renders — and service_list's empty state
    carries its own hx-get button ("Add your first service fee" -> #confirmBody). The two
    states are mutually exclusive, so one fixture cannot cover both, and a no-match search
    doesn't help either: that CTA is {% if not search %}.

    Measured: with only the stocked fixture, reverting the empty state's hx-disinherit
    failed NOTHING.

    This is the worst case to leave broken — it is the FIRST button a brand-new business
    ever presses, and it would open an empty modal.
    """
    bare, _plan = make_business(owner)          # no services at all
    client.force_login(owner)
    response = client.get(reverse("service-list", kwargs={"business_slug": bare.slug}))
    assert response.status_code == 200

    html = response.content.decode()
    assert "Add your first service fee" in html, (
        "the empty-state CTA did not render — this test is no longer checking what it "
        "thinks it is"
    )
    result = walk(html)
    assert not result.leaks, (
        f"service-list empty state: {len(result.leaks)} control(s) would swap empty.\n  "
        + "\n  ".join(result.leaks)
    )


def test_product_list_actually_has_controls_to_check(client, owner, stocked_business):
    """Guard against the test above passing because it found nothing.

    A page whose markup drifts (or whose fixture stops producing rows) would sail
    through the parametrized test with zero hx-get elements examined. product_list is
    the densest case — 4 KPI cards plus per-row add/edit/archive — so if the walker
    sees nothing here, the walker is broken, not the page.
    """
    client.force_login(owner)
    response = client.get(reverse("product-list", kwargs={"business_slug": stocked_business.slug}))
    result = walk(response.content.decode())
    assert result.hx_seen >= 5, (
        f"walker only saw {result.hx_seen} hx-get/hx-post elements on product_list — "
        "it is not looking at what it thinks it is"
    )


def test_walker_catches_the_bug_it_was_written_for():
    """The detector must be able to fail.

    This is the exact shape that shipped: a region declaring hx-select, and a button
    inside it with its own hx-get -> #confirmBody. Without hx-disinherit the button
    inherits the region's hx-select and swaps empty.
    """
    broken = """
      <div id="product-results" hx-boost="true" hx-select="#product-results"
           hx-target="#product-results">
        <tbody hx-boost="false">
          <tr><td>
            <a hx-get="/archive/1/" hx-target="#confirmBody" hx-swap="innerHTML">Archive</a>
          </td></tr>
        </tbody>
      </div>
    """
    assert len(walk(broken).leaks) == 1

    # ...and the fix must clear it. hx-boost="false" alone must NOT be enough —
    # that belief is the whole reason this file exists.
    fixed = broken.replace('<tbody hx-boost="false">',
                           '<tbody hx-boost="false" hx-disinherit="*">')
    assert walk(fixed).leaks == []


def test_a_control_driving_its_own_region_is_not_a_leak():
    """The opposite shape must stay legal, or the test above is unusable.

    A filter <select> with hx-get and NO hx-target inherits target+select together and
    is supposed to: that is how the receivables status filter and the KPI cards drive
    their region. Flagging these would make the real test noise, and noise gets muted.
    """
    fine = """
      <div id="recv-results" hx-select="#recv-results" hx-target="#recv-results">
        <select name="recv_status" hx-get="?recv_status="></select>
      </div>
    """
    assert walk(fine).leaks == []


def test_shift_dashboard_region_has_no_leaks(client, owner):
    """Timecards (shift-dashboard) — the one converted page CONVERTED_LISTS can't hold.

    It is Standard+ gated (has_timecards), so it redirects on the free-plan stocked_business,
    and it needs a real ShiftEmployee row (clock_in set) or it renders its empty state. Built
    here on its own terms: a standard business, one employee, one timecard.

    This page has NO hx-* controls inside its region (rows navigate with onclick=window.location,
    pagination is boosted links), so the walker's leak check is mostly a guard against a future
    edit adding a modal/cart trigger without hx-disinherit. What it positively asserts is that the
    region rendered and a row is in it — i.e. the conversion is actually present and exercised.
    """
    biz, _plan = make_business(owner, plan='standard')
    emp = make_employee(biz)
    make_timecard(biz, clock_in=timezone.now(), employee=emp)

    client.force_login(owner)
    response = client.get(reverse("shift-dashboard", kwargs={"business_slug": biz.slug}))
    assert response.status_code == 200, f"shift-dashboard -> {response.status_code}"

    html = response.content.decode()
    assert 'id="shift-results"' in html, "region wrapper missing — conversion not present"
    assert emp.name in html, "no shift row rendered — the empty state is being tested, not the table"
    assert walk(html).leaks == []
