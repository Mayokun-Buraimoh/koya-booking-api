from django.db import models
from django.db.models import Q


class Slot(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    provider = models.CharField(max_length=200)
    start = models.DateTimeField()
    end = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["start"], name="slot_start_idx")]


class Booking(models.Model):
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"

    # `seq` is the internal auto-increment key. It gives a strict "oldest first" order
    # and powers the paging cursor. `public_id` is the id the API shows.
    seq = models.BigAutoField(primary_key=True)
    public_id = models.CharField(max_length=40, unique=True)
    slot = models.ForeignKey(Slot, on_delete=models.CASCADE, related_name="bookings")
    name = models.CharField(max_length=200)
    email = models.CharField(max_length=254)      # always stored lower-case
    status = models.CharField(max_length=16, default=CONFIRMED)
    created_at = models.DateTimeField()

    class Meta:
        constraints = [
            # The real double-booking guard: at most ONE confirmed booking per slot.
            models.UniqueConstraint(
                fields=["slot"],
                condition=Q(status="confirmed"),
                name="one_confirmed_booking_per_slot",
            ),
        ]
        indexes = [models.Index(fields=["email", "seq"], name="booking_email_seq_idx")]


class BookingAttempt(models.Model):
    """One row per booking attempt (successful or not). Drives the per-email rate limit."""
    email = models.CharField(max_length=254)
    created_at = models.DateTimeField()

    class Meta:
        indexes = [models.Index(fields=["email", "created_at"], name="attempt_email_time_idx")]


class IdempotencyRecord(models.Model):
    key = models.CharField(max_length=255, unique=True)
    fingerprint = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
