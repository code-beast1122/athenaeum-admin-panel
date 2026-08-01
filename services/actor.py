"""Who the portal is acting as when it writes user-owned rows.

Community posts and comments have a NOT NULL `user_id` pointing at
`auth.users`. In local mode there is no signed-in account, so fall back to an
existing admin profile rather than writing NULL and failing the insert.
"""

from security import current_admin
from services.db import fetch_all

_cached_admin_id = None


def _first_admin_profile():
    global _cached_admin_id
    if _cached_admin_id:
        return _cached_admin_id
    for profile in fetch_all("profiles", select="id, role, full_name"):
        if (profile.get("role") or "").lower() == "admin":
            _cached_admin_id = profile["id"]
            return _cached_admin_id
    return None


def acting_admin():
    """Return `(user_id, display_name, avatar_url)` for authoring content.

    `user_id` is None only when the project has no admin profile at all, in
    which case the caller must refuse rather than write a null.
    """
    admin = current_admin() or {}
    name = admin.get("name") or "Athenaeum Admin"
    if admin.get("id"):
        return admin["id"], name, admin.get("avatar_url")

    fallback = _first_admin_profile()
    if not fallback:
        return None, name, None

    profile = next(
        (p for p in fetch_all("profiles", select="id, full_name, avatar_url") if p["id"] == fallback),
        {},
    )
    return fallback, profile.get("full_name") or name, profile.get("avatar_url")
