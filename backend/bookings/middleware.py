import hmac

from django.conf import settings
from django.http import JsonResponse

# Open without a key: the health check and the frontend page itself.
PUBLIC_PATHS = {"/health", "/", "/styles.css", "/app.js", "/docs", "/openapi.json"}
ADMIN_PREFIXES = ("/admin/", "/static/")


class ApiKeyMiddleware:
    """Level 2: every other request needs the right X-API-Key, else 401."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "GET" and request.path in PUBLIC_PATHS:
            return self.get_response(request)
        # The admin has its own login; /static/ is the admin's CSS and JS.
        if request.path.startswith(ADMIN_PREFIXES):
            return self.get_response(request)
        supplied = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(supplied.encode(), settings.API_KEY.encode()):
            return JsonResponse({"error": "missing or invalid API key"}, status=401)
        return self.get_response(request)
