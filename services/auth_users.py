"""Helpers over the Supabase auth admin API.

`profiles` has no email column, so anything that needs an address has to go
through GoTrue. That endpoint is paged, hence the loops below.
"""

from extensions import supabase

PAGE_SIZE = 200
MAX_PAGES = 20


def iter_users():
    for page in range(1, MAX_PAGES + 1):
        batch = supabase.auth.admin.list_users(page=page, per_page=PAGE_SIZE)
        for user in batch:
            yield user
        if len(batch) < PAGE_SIZE:
            return


def email_map():
    """id -> email for every auth user. Returns {} if the call fails."""
    try:
        return {u.id: u.email for u in iter_users()}
    except Exception:  # noqa: BLE001 - email is decoration, never block a page
        return {}


def find_by_email(email):
    target = (email or "").strip().lower()
    for user in iter_users():
        if (user.email or "").lower() == target:
            return user
    return None
