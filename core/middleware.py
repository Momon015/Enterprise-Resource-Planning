from django.conf import settings
from django.urls import reverse
from django.http import HttpResponse


class HtmxLoginRedirectMiddleware:
    """If an htmx request gets redirected to the login page, force a full-page
    redirect (HX-Redirect) instead of letting htmx swap the login page into a modal."""
    def __init__(self, get_response):
        self.get_response = get_response
        try:
            self.login_path = reverse(settings.LOGIN_URL)
        except Exception:
            self.login_path = settings.LOGIN_URL

    def __call__(self, request):
        response = self.get_response(request)
        if (request.headers.get('HX-Request')
                and response.status_code == 302
                and response.get('Location', '').startswith(self.login_path)):
            full = HttpResponse(status=204)
            full['HX-Redirect'] = response['Location']
            return full
        return response
    
class ReturnToMiddleware:
    """
    Records the last 'navigational' page (list/detail) in the session so forms
    can return there on Cancel / ✕ / after save — preserving filters & scroll context.
    Stores get_full_path() (own server path → no open-redirect to validate).
    """
    SKIP = ('create', 'update', 'edit', 'add', 'delete', 'archive',
            'void', 'login', 'logout', 'register', 'payment')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._record(request, response)
        except Exception:
            pass
        return response

    def _record(self, request, response):
        if request.method != 'GET':
            return
        if request.headers.get('HX-Request') == 'true':
            return
        # Only track real navigational HTML pages — never JSON/API/AJAX endpoints
        # (e.g. the PSGC cascade fetches) or file downloads. This is what stops
        # Cancel from jumping to the last /psgc/… fetch URL.
        if 'text/html' not in (response.get('Content-Type') or ''):
            return
        match = getattr(request, 'resolver_match', None)
        if not match:
            return
        name = match.url_name or ''
        if any(s in name for s in self.SKIP):
            return
        request.session['return_to'] = request.get_full_path()



