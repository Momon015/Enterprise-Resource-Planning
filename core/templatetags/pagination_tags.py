from django import template
from django.core.paginator import Paginator

register = template.Library()


def _url_for(request, param, value):
    """Current URL with ONLY the page key changed — every other query param
    (filters, search, the other panel's page) is carried through untouched."""
    query = request.GET.copy()
    query[param] = value
    return '?' + query.urlencode()


@register.inclusion_tag('partials/_pagination.html', takes_context=True)
def pagination(context, page_obj, param='page', on_each_side=3, on_ends=1):
    """
    The ONE pagination control. Use it as:

        {% load pagination_tags %}
        {% pagination page_obj %}

    …and for a panel that paginates alongside the main list on the same page:

        {% pagination recv_page_obj param='recv_page' %}

    ── WHY THIS EXISTS ──────────────────────────────────────────────────────
    Every list template used to hand-build its own pagination links, re-attaching
    each filter BY NAME:

        ?page=2{% if q %}&search={{ q }}{% endif %}{% if cat %}&category={{ cat }}{% endif %}…

    sale_list re-attached EIGHT. So every template had to KNOW its own view's filter
    list, and the day someone adds a filter and forgets the pagination block, page 2
    silently drops it — unfiltered results, no error, no clue. A latent bug sitting
    in 30 templates.

    Copying request.GET wholesale means no template ever needs to know its filters.
    Add filters freely; pagination keeps working, untouched.

    This also FIXES a live bug: sale_list's links dropped `recv_page`, so paging the
    sales list bounced the embedded receivables panel back to page 1. Carrying the
    whole query string keeps the two paginators independent.

    ── on_each_side ────────────────────────────────────────────────────────
    How many page numbers flank the current one. 3 gives "1 2 3 4 … 10" at the
    start and "1 … 12 13 14 15 16 17 18 … 30" in the middle — enough to JUMP a few
    pages instead of clicking next-next-next, which was the whole complaint.

    Don't push it past 3 without checking a phone: 4 produces a 15-box bar. (The
    bar wraps now — `.pagination` gained flex-wrap — but a two-line pagination
    control looks broken.)

    A leading "…" that hides only pages 2-3 is NOT worth adding: the ellipsis is
    as wide as a page number, so you'd trade two clickable pages for nothing.
    Django already knows this and only elides once there's a real gap to collapse.

    Renders nothing when there's one page or fewer — callers need no {% if %} guard.
    """
    if page_obj is None or page_obj.paginator.num_pages <= 1:
        return {'show': False}

    request = context['request']
    ellipsis = Paginator.ELLIPSIS

    pages = []
    for p in page_obj.paginator.get_elided_page_range(
        page_obj.number, on_each_side=on_each_side, on_ends=on_ends
    ):
        if p == ellipsis:
            pages.append({'ellipsis': True, 'label': ellipsis})
        else:
            pages.append({
                'ellipsis': False,
                'label': p,
                'url': _url_for(request, param, p),
                'active': p == page_obj.number,
            })

    return {
        'show': True,
        'pages': pages,
        'has_previous': page_obj.has_previous(),
        'has_next': page_obj.has_next(),
        'previous_url': _url_for(request, param, page_obj.previous_page_number())
                        if page_obj.has_previous() else None,
        'next_url': _url_for(request, param, page_obj.next_page_number())
                    if page_obj.has_next() else None,
    }
