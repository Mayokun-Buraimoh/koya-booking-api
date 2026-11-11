"""Admin pages (default Django admin, styled by Jazzmin).

Design rule: the admin must not be a back door around the API's rules. Bookings are
view-only and can only be cancelled through an action; attempts and idempotency records
are view-only (deleting them is allowed, e.g. to clear someone's rate limit).
"""
from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils.html import format_html

from .models import Booking, BookingAttempt, IdempotencyRecord, Slot


def badge(text, colour):
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 10px;border-radius:999px;font-size:.85em">{}</span>',
        colour, text,
    )


class ViewOnlyAdmin(admin.ModelAdmin):
    """No adding, no editing. Listing, searching and viewing only."""
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Slot)
class SlotAdmin(admin.ModelAdmin):
    list_display = ("id", "provider", "start", "end", "availability")
    list_filter = ("provider", "start")
    search_fields = ("id", "provider")
    ordering = ("start",)
    date_hierarchy = "start"
    list_per_page = 50

    def get_queryset(self, request):
        confirmed = Booking.objects.filter(slot=OuterRef("pk"), status=Booking.CONFIRMED)
        return super().get_queryset(request).annotate(_taken=Exists(confirmed))

    @admin.display(description="Availability", ordering="_taken")
    def availability(self, obj):
        return badge("Booked", "#dc3545") if obj._taken else badge("Available", "#28a745")


@admin.register(Booking)
class BookingAdmin(ViewOnlyAdmin):
    list_display = ("public_id", "status_badge", "name", "email", "provider", "slot_time", "created_at")
    list_filter = ("status", "slot__provider", "created_at")
    search_fields = ("public_id", "name", "email", "slot__id")
    ordering = ("-seq",)
    date_hierarchy = "created_at"
    list_select_related = ("slot",)
    list_per_page = 50
    actions = ["cancel_selected"]
    fieldsets = (
        ("Booking", {"fields": ("public_id", "status", "created_at")}),
        ("Patient", {"fields": ("name", "email")}),
        ("Slot", {"fields": ("slot",)}),
    )

    def has_delete_permission(self, request, obj=None):
        return False  # cancel instead, so history is kept

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        return badge(obj.status, "#28a745" if obj.status == Booking.CONFIRMED else "#6c757d")

    @admin.display(description="Provider", ordering="slot__provider")
    def provider(self, obj):
        return obj.slot.provider

    @admin.display(description="Slot time", ordering="slot__start")
    def slot_time(self, obj):
        return obj.slot.start.strftime("%d %b %Y, %H:%M UTC")

    @admin.action(description="Cancel selected bookings (frees their slots)")
    def cancel_selected(self, request, queryset):
        with transaction.atomic():
            count = queryset.filter(status=Booking.CONFIRMED).update(status=Booking.CANCELLED)
        skipped = queryset.count() - count
        self.message_user(request, f"Cancelled {count} booking(s).", messages.SUCCESS)
        if skipped:
            self.message_user(request, f"{skipped} were already cancelled.", messages.WARNING)


@admin.register(BookingAttempt)
class BookingAttemptAdmin(ViewOnlyAdmin):
    """The rate-limit log. Delete a person's rows to lift their limit."""
    list_display = ("email", "created_at")
    search_fields = ("email",)
    ordering = ("-created_at",)
    list_per_page = 100


@admin.register(IdempotencyRecord)
class IdempotencyRecordAdmin(ViewOnlyAdmin):
    list_display = ("key", "status_code", "short_fingerprint", "created_at")
    search_fields = ("key",)
    list_filter = ("status_code",)
    ordering = ("-created_at",)
    list_per_page = 100

    @admin.display(description="Fingerprint")
    def short_fingerprint(self, obj):
        return obj.fingerprint[:12] + "…"
