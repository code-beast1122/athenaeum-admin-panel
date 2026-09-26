"""Course access rules.

An enrollment is either `pending` (payment awaiting review) or `paid`. A paid
enrollment gives one month of access: the database stamps `expires_at` when the
row becomes paid (trigger `set_enrollment_expiry`). There is no free tier and
no account-wide plan.
"""

from datetime import datetime, timezone

PENDING = "pending"
PAID = "paid"
PAYMENT_STATUSES = [PENDING, PAID]


def _parse(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def access_state(row):
    """'active', 'expired' or 'pending' for one enrollment row."""
    status = (row.get("payment_status") or "").lower()
    if status != PAID:
        return PENDING
    expires = _parse(row.get("expires_at"))
    if expires is None or expires > datetime.now(timezone.utc):
        return "active"
    return "expired"


def is_active(row):
    return access_state(row) == "active"
