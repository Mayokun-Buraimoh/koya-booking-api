from django.contrib import admin
from django.urls import path, re_path

from bookings import views

admin.site.site_header = "Clinic Booking"
admin.site.index_title = "Overview"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health", views.health),
    path("docs", views.docs),
    path("openapi.json", views.openapi_spec),
    path("reset", views.reset),
    path("slots", views.slots),
    path("bookings", views.bookings),
    path("bookings/<str:booking_id>", views.booking_detail),
    # The frontend page (served without an API key; see middleware).
    path("", views.web_page),
    path("styles.css", views.web_asset, {"name": "styles.css"}),
    path("app.js", views.web_asset, {"name": "app.js"}),
    # Anything else: JSON 404.
    re_path(r"^.*$", views.not_found),
]
