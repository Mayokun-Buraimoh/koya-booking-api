"""Input cleaning. Pure functions, no database."""
import re
from datetime import timezone as dt_timezone

from django.utils.dateparse import parse_datetime

from .errors import BadRequest

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalise_email(value):
    """Trimmed, lower-cased email if it is a valid-looking string, otherwise None."""
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if value and EMAIL_RE.match(value) else None


def validate_new_booking(body):
    """Spec level 2. Returns (slot_id, name, email) cleaned, or raises BadRequest."""
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object")

    cleaned = {}
    for field in ("slot_id", "name", "email"):
        value = body.get(field)
        if not isinstance(value, str) or not value.strip():
            raise BadRequest(f"{field} is required")
        cleaned[field] = value.strip()

    email = normalise_email(cleaned["email"])
    if email is None:
        raise BadRequest("email is not valid")
    return cleaned["slot_id"], cleaned["name"], email


def parse_limit(raw):
    """`limit` query param: whole number 1..50, default 10. Anything else is a 400."""
    if raw is None:
        return 10
    if not re.fullmatch(r"[0-9]+", raw) or not (1 <= int(raw) <= 50):
        raise BadRequest("limit must be a whole number between 1 and 50")
    return int(raw)


def parse_reset_slots(body):
    """Validate the /reset payload. Returns a list of dicts ready to build Slot rows."""
    if not isinstance(body, dict) or not isinstance(body.get("slots"), list):
        raise BadRequest("body must be an object with a 'slots' array")
    out, seen = [], set()
    for i, item in enumerate(body["slots"]):
        if not isinstance(item, dict):
            raise BadRequest(f"slots[{i}] must be an object")
        row = {}
        for field in ("id", "provider"):
            v = item.get(field)
            if not isinstance(v, str) or not v.strip():
                raise BadRequest(f"slots[{i}].{field} is required")
            row[field] = v.strip() if field == "id" else v
        for field in ("start", "end"):
            v = item.get(field)
            dt = parse_datetime(v) if isinstance(v, str) else None
            if dt is None:
                raise BadRequest(f"slots[{i}].{field} must be an ISO date-time")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_timezone.utc)
            row[field] = dt
        if row["id"] in seen:
            raise BadRequest(f"duplicate slot id '{row['id']}'")
        seen.add(row["id"])
        out.append(row)
    return out
