"""Per-email rate limit (level 5): at most 5 attempts per email per rolling 60 seconds.

The attempt is recorded in its OWN transaction, so it still counts when the booking
that follows fails. A transaction-level advisory lock per email makes two simultaneous
requests unable to both slip under the limit. (Transaction-level locks also work through
Neon's pooled connections, unlike session-level ones.)
"""
import math
from datetime import timedelta

from django.db import connection, transaction
from django.utils import timezone

from .errors import RateLimited
from .models import BookingAttempt

MAX_ATTEMPTS = 5
WINDOW = timedelta(seconds=60)


def advisory_xact_lock(name):
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [name])


def check_and_record(email):
    """Raise RateLimited if `email` already used its 5 attempts, else record this one."""
    with transaction.atomic():
        advisory_xact_lock("rl:" + email)
        now = timezone.now()
        window_start = now - WINDOW

        BookingAttempt.objects.filter(email=email, created_at__lte=window_start).delete()
        times = list(
            BookingAttempt.objects.filter(email=email).order_by("created_at").values_list("created_at", flat=True)
        )
        if len(times) >= MAX_ATTEMPTS:
            wait = math.ceil((times[0] + WINDOW - now).total_seconds())
            wait = max(1, wait)
            raise RateLimited("too many booking attempts, try again later", headers={"Retry-After": str(wait)})
        BookingAttempt.objects.create(email=email, created_at=now)
