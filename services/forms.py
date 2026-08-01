"""Form value coercion.

HTML forms only ever submit strings; PostgREST wants real types and treats an
empty string as an invalid uuid/number rather than NULL. These helpers convert
one payload dict per request so the blueprints never touch `request.form`
directly for typed columns.
"""

from datetime import datetime, timezone

TRUE_VALUES = {"1", "true", "on", "yes", "y"}


def text(form, field, default=None, allow_empty=False):
    value = (form.get(field) or "").strip()
    if not value and not allow_empty:
        return default
    return value


def boolean(form, field):
    return (form.get(field) or "").strip().lower() in TRUE_VALUES


def integer(form, field, default=None):
    raw = (form.get(field) or "").strip()
    if raw == "":
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def number(form, field, default=None):
    raw = (form.get(field) or "").strip()
    if raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def uuid_or_none(form, field):
    raw = (form.get(field) or "").strip()
    return raw or None


def timestamp(form, field):
    """Convert a `datetime-local` input to an ISO-8601 UTC timestamp."""
    raw = (form.get(field) or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return (
                datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).isoformat()
            )
        except ValueError:
            continue
    return raw


def string_list(form, field, separator="\n"):
    raw = (form.get(field) or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(separator) if part.strip()]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def compact(payload):
    """Drop keys whose value is None so partial updates leave columns alone."""
    return {k: v for k, v in payload.items() if v is not None}
