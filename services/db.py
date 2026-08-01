"""Thin helpers over the Supabase/PostgREST client.

Every list page in the portal needs the same four things: search, filtering,
ordering and pagination. `query_table` bundles them so the blueprints stay short.
"""

from math import ceil

from postgrest.exceptions import APIError

from config import Config
from extensions import supabase


class DbError(Exception):
    """Raised with a human-readable message when PostgREST rejects a call."""


def _wrap(exc):
    if isinstance(exc, APIError):
        detail = exc.message or ""
        if exc.details:
            detail = f"{detail} ({exc.details})"
        if exc.hint:
            detail = f"{detail} Hint: {exc.hint}"
        return DbError(detail or "Database rejected the request.")
    return DbError(str(exc))


def table(name):
    return supabase.table(name)


def query_table(
    name,
    select="*",
    search=None,
    search_fields=(),
    filters=None,
    ilike_filters=None,
    in_filters=None,
    order_by="created_at",
    desc=True,
    page=1,
    per_page=None,
    limit=None,
):
    """Return `(rows, total, pages)` for a paginated list view.

    `filters` are exact matches, `ilike_filters` are case-insensitive matches
    (needed because `profiles.role` holds both `student` and `Student`).
    """
    per_page = per_page or Config.PER_PAGE
    page = max(1, int(page or 1))

    q = supabase.table(name).select(select, count="exact")

    for field, value in (filters or {}).items():
        if value not in (None, "", "all"):
            q = q.eq(field, value)

    for field, value in (ilike_filters or {}).items():
        if value not in (None, "", "all"):
            q = q.ilike(field, value)

    for field, values in (in_filters or {}).items():
        q = q.in_(field, list(values))

    if search and search_fields:
        # PostgREST `or` filter: field.ilike.*term*,other.ilike.*term*
        term = search.replace(",", " ").replace("(", "").replace(")", "").strip()
        if term:
            clauses = ",".join(f"{f}.ilike.*{term}*" for f in search_fields)
            q = q.or_(clauses)

    if order_by:
        q = q.order(order_by, desc=desc)

    if limit:
        q = q.limit(limit)
    else:
        start = (page - 1) * per_page
        q = q.range(start, start + per_page - 1)

    try:
        resp = q.execute()
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a flash
        raise _wrap(exc) from exc

    total = resp.count if resp.count is not None else len(resp.data)
    pages = max(1, ceil(total / per_page)) if not limit else 1
    return resp.data or [], total, pages


def count_table(name, filters=None, ilike_filters=None):
    q = supabase.table(name).select("id", count="exact")
    for field, value in (filters or {}).items():
        if value not in (None, "", "all"):
            q = q.eq(field, value)
    for field, value in (ilike_filters or {}).items():
        if value not in (None, "", "all"):
            q = q.ilike(field, value)
    try:
        resp = q.limit(1).execute()
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    return resp.count or 0


def fetch_all(name, select="*", filters=None, order_by=None, desc=False, limit=1000):
    q = supabase.table(name).select(select)
    for field, value in (filters or {}).items():
        if value not in (None, ""):
            q = q.eq(field, value)
    if order_by:
        q = q.order(order_by, desc=desc)
    try:
        return q.limit(limit).execute().data or []
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


def fetch_one(name, row_id, select="*", id_field="id"):
    try:
        resp = (
            supabase.table(name).select(select).eq(id_field, row_id).limit(1).execute()
        )
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    return resp.data[0] if resp.data else None


def insert_row(name, payload):
    try:
        resp = supabase.table(name).insert(payload).execute()
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    return resp.data[0] if resp.data else None


def update_row(name, row_id, payload, id_field="id"):
    if not payload:
        return None
    try:
        resp = supabase.table(name).update(payload).eq(id_field, row_id).execute()
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    return resp.data[0] if resp.data else None


def delete_row(name, row_id, id_field="id"):
    try:
        supabase.table(name).delete().eq(id_field, row_id).execute()
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


def delete_where(name, field, value):
    try:
        supabase.table(name).delete().eq(field, value).execute()
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc


def profiles_map(ids, select="id, full_name, avatar_url, role"):
    """Look up profiles by id. Enrollments reference `auth.users`, so PostgREST
    cannot embed `profiles` on them - we join in Python instead."""
    ids = [i for i in {i for i in ids if i}]
    if not ids:
        return {}
    try:
        resp = supabase.table("profiles").select(select).in_("id", ids).execute()
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc) from exc
    return {row["id"]: row for row in resp.data or []}


def attach_profiles(rows, key="student_id", as_field="student"):
    lookup = profiles_map([r.get(key) for r in rows])
    for row in rows:
        row[as_field] = lookup.get(row.get(key)) or {}
    return rows
