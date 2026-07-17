"""Helpers for views that serve both an htmx modal and a full page."""
from urllib.parse import urlparse

from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse


def back_url(request):
    """The page the USER is on — which is not request.path inside a modal.

    A view rendering a modal is answering a background fetch: request.path is the
    modal's own URL, while the browser is still sitting on the list that opened it.
    So a `?next={{ request.path }}` baked into a modal sends the user to a dedicated
    page they never visited — right on a full page, wrong in the modal that replaced it.

    htmx puts the real address bar in HX-Current-URL, so use that when present and
    fall back to request.path for the full-page render (where they match anyway).

    Returns a path, never an absolute URL: urlparse drops scheme+netloc, so a spoofed
    header like "//evil.com/x" comes back as "/x" and can't become an open redirect
    (a bare startswith('/') check would have let that through).
    """
    current = request.headers.get('HX-Current-URL')
    if not current:
        return request.path
    parsed = urlparse(current)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def redirect_after_form(request, url_name, *, query='', **kwargs):
    """Redirect that survives an htmx modal submit.

    The create/update forms live in an htmx modal AND on a full page, served by the
    same view. A plain redirect works for the full page, but when the form is
    submitted from the modal (hx-post → #confirmBody) htmx would transparently
    follow the 302 and swap the destination PAGE into the modal. So for htmx we send
    HX-Redirect, which tells htmx to do a real browser navigation instead (messages
    then show on the destination as normal).

    Use this on EVERY redirect exit of such a view, GET ones included — one plain
    `redirect()` left behind is enough to render a whole page inside the modal.

    `query` appends a query string to the destination (e.g. to keep the list's
    filters through a save).
    """
    url = reverse(url_name, kwargs=kwargs)
    if query:
        url = f"{url}?{query}"
    if request.headers.get('HX-Request'):
        resp = HttpResponse(status=204)
        resp['HX-Redirect'] = url
        return resp
    return redirect(url)
