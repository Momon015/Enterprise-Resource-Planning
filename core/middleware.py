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
            self._record(request)
        except Exception:
            pass  
        return response

    def _record(self, request):
        if request.method != 'GET':
            print(f"[RT] skip (not GET): {request.path}")
            return
        if request.headers.get('HX-Request') == 'true':
            print(f"[RT] skip (htmx): {request.path}")
            return
        match = getattr(request, 'resolver_match', None)
        if not match:
            print(f"[RT] skip (no match): {request.path}")
            return
        name = match.url_name or ''
        if any(s in name for s in self.SKIP):
            print(f"[RT] skip (name='{name}'): {request.path}")
            return
        request.session['return_to'] = request.get_full_path()
        print(f"[RT] RECORDED return_to = {request.get_full_path()}  (name='{name}')")


