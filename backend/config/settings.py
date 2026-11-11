"""Settings. Everything environment-specific comes from env vars (or backend/.env locally)."""
import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent      # backend/
WEB_DIR = BASE_DIR.parent / "web"                      # the frontend files


def _load_dotenv(path):
    """Tiny .env reader so local work needs no extra dependency. Real env vars win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DEBUG = _bool("DEBUG", False)

SECRET_KEY = os.environ.get("SECRET_KEY") or ("dev-insecure-key" if DEBUG else None)
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY is not set")

API_KEY = os.environ.get("API_KEY", "")
if not API_KEY:
    raise ImproperlyConfigured("API_KEY is not set")

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "*").split(",") if h.strip()]

INSTALLED_APPS = [
    "jazzmin",                      # must come before django.contrib.admin
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "bookings",
]

MIDDLEWARE = [
    "whitenoise.middleware.WhiteNoiseMiddleware",          # serves admin CSS/JS
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",           # admin login only; API views are exempt
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "bookings.middleware.ApiKeyMiddleware",                # skips /admin/ and /static/
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    }
]

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Admin login needs this on HTTPS hosts, e.g. https://your-app.onrender.com
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# ---------- Admin look and feel (Jazzmin) ----------
JAZZMIN_SETTINGS = {
    "site_title": "Clinic Booking Admin",
    "site_header": "Clinic Booking",
    "site_brand": "Clinic Booking",
    "welcome_sign": "Clinic booking administration",
    "copyright": "Clinic Booking",
    "search_model": ["bookings.Booking"],
    "topmenu_links": [
        {"name": "Dashboard", "url": "admin:index"},
        {"name": "Booking page", "url": "/", "new_window": True},
        {"name": "API spec", "url": "https://koyaacademy.com/assessment-api", "new_window": True},
    ],
    "order_with_respect_to": [
        "bookings.booking", "bookings.slot", "bookings.bookingattempt", "bookings.idempotencyrecord", "auth",
    ],
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.group": "fas fa-users",
        "bookings.booking": "fas fa-calendar-check",
        "bookings.slot": "fas fa-clock",
        "bookings.bookingattempt": "fas fa-shield-alt",
        "bookings.idempotencyrecord": "fas fa-fingerprint",
    },
    "default_icon_parents": "fas fa-chevron-circle-right",
    "default_icon_children": "fas fa-circle",
    "related_modal_active": False,
    "show_ui_builder": False,
    "changeform_format": "horizontal_tabs",
}
JAZZMIN_UI_TWEAKS = {
    "theme": "flatly",
    "dark_mode_theme": "darkly",
    "navbar_small_text": False,
    "sidebar_nav_small_text": False,
    "sidebar_nav_flat_style": True,
}

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
handler404 = "bookings.views.not_found"
handler500 = "bookings.views.server_error"

# Neon (Postgres). Set DATABASE_URL in .env locally or in Render's environment.
_db_url = os.environ.get("DATABASE_URL", "").strip()
if _db_url:
    DATABASES = {
        "default": dj_database_url.parse(_db_url, conn_max_age=60, conn_health_checks=True, ssl_require=True)
    }
else:
    # Placeholder so `manage.py check` / `makemigrations` work before Neon is configured.
    DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "NAME": "unconfigured"}}
# Safe with Neon's pooled (pgbouncer) connection strings.
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
APPEND_SLASH = False          # a redirect would turn a POST into a failure
USE_TZ = True
TIME_ZONE = "UTC"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
