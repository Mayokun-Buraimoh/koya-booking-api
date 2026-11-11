"""Thin views: read the request, call a service, shape the response."""
import json
import logging
from functools import wraps

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import services
from .errors import ApiError, BadRequest

logger = logging.getLogger(__name__)


def json_response(status, payload, headers=None):
    resp = JsonResponse(payload, status=status, safe=False)
    for k, v in (headers or {}).items():
        resp[k] = v
    return resp


def api(methods):
    """Method check plus uniform {"error": ...} responses for every failure."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(request, *args, **kwargs):
            if request.method not in methods:
                return json_response(405, {"error": "method not allowed"}, {"Allow": ", ".join(methods)})
            try:
                return fn(request, *args, **kwargs)
            except ApiError as e:
                return json_response(e.status, {"error": e.message}, e.headers)
            except Exception:
                logger.exception("unhandled error")
                return json_response(500, {"error": "internal server error"})
        # The API is protected by X-API-Key, not cookies, so CSRF does not apply to it.
        return csrf_exempt(wrapper)
    return decorator


def read_json(request):
    try:
        return json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BadRequest("request body must be valid JSON")


# ---------- API ----------

@api(["GET"])
def health(request):
    return json_response(200, {"status": "ok"})


@api(["POST"])
def reset(request):
    services.reset(read_json(request))
    return json_response(200, {"ok": True})


@api(["GET"])
def slots(request):
    return json_response(200, {"slots": services.list_slots(request.GET.get("provider") or None)})


@api(["GET", "POST"])
def bookings(request):
    if request.method == "POST":
        key = (request.headers.get("Idempotency-Key") or "").strip() or None
        status, payload = services.create_booking(read_json(request), key)
        return json_response(status, payload)
    result = services.list_bookings(
        request.GET.get("email"), request.GET.get("limit"), request.GET.get("cursor")
    )
    return json_response(200, result)


@api(["GET", "DELETE"])
def booking_detail(request, booking_id):
    if request.method == "DELETE":
        return json_response(200, services.cancel_booking(booking_id))
    return json_response(200, services.get_booking(booking_id))


# ---------- errors outside the API views ----------

def not_found(request, exception=None, **kwargs):
    return json_response(404, {"error": "not found"})


def server_error(request, **kwargs):
    return json_response(500, {"error": "internal server error"})


# ---------- API docs (Swagger UI) ----------

SWAGGER_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Clinic Booking API docs</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css">
<style>body{margin:0}.swagger-ui .topbar{display:none}</style></head>
<body><div id="ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({url: "/openapi.json", dom_id: "#ui", docExpansion: "list",
  defaultModelsExpandDepth: 0, deepLinking: true, persistAuthorization: true, tryItOutEnabled: true});</script>
</body></html>"""


@api(["GET"])
def docs(request):
    return HttpResponse(SWAGGER_PAGE, content_type="text/html; charset=utf-8")


@api(["GET"])
def openapi_spec(request):
    path = settings.BASE_DIR / "bookings" / "openapi.json"
    return HttpResponse(path.read_bytes(), content_type="application/json")


# ---------- the frontend (served by this same app) ----------

def _read_web(name):
    return (settings.WEB_DIR / name).read_bytes()


@api(["GET"])
def web_page(request):
    """index.html with the real API key injected (patients never type it)."""
    html = _read_web("index.html").decode("utf-8")
    config = json.dumps({"apiKey": settings.API_KEY, "baseUrl": ""}).replace("<", "\\u003c")
    marker = 'window.APP_CONFIG = { apiKey: "dev-key", baseUrl: "" };'
    html = html.replace(marker, "window.APP_CONFIG = " + config + ";")
    return HttpResponse(html, content_type="text/html; charset=utf-8")


@api(["GET"])
def web_asset(request, name):
    types = {"styles.css": "text/css", "app.js": "application/javascript"}
    return HttpResponse(_read_web(name), content_type=types[name] + "; charset=utf-8")
