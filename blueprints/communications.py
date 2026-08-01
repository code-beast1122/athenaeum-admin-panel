from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import supabase
from security import protect_blueprint
from services import forms
from services.db import (
    DbError,
    delete_row,
    fetch_all,
    insert_row,
    profiles_map,
    query_table,
    update_row,
)

bp = Blueprint("communications", __name__)
protect_blueprint(bp)

TARGETS = ["all", "paid", "trial", "parents", "teachers"]
TYPES = ["info", "success", "warning", "urgent"]


def _recipients(target):
    """Resolve an announcement target into profile ids."""
    profiles = fetch_all("profiles", select="id, role, plan_type")
    target = (target or "all").lower()
    if target == "all":
        return [p["id"] for p in profiles]
    if target in {"paid", "trial"}:
        return [p["id"] for p in profiles if (p.get("plan_type") or "").lower() == target]
    if target == "teachers":
        return [p["id"] for p in profiles if (p.get("role") or "").lower() == "teacher"]
    if target == "parents":
        return [p["id"] for p in profiles if (p.get("role") or "").lower() == "parent"]
    return [p["id"] for p in profiles if (p.get("role") or "").lower() == target.rstrip("s")]


# --- Announcements -----------------------------------------------------------


@bp.route("/announcements")
def announcements():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    target = request.args.get("target", "all_targets")

    filters = {}
    if target not in {"all_targets", "", None}:
        filters["target"] = target

    try:
        rows, total, pages = query_table(
            "announcements",
            select="*, courses(id, title)",
            search=search,
            search_fields=("title", "message"),
            filters=filters,
            order_by="created_at",
            page=page,
        )
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages = [], 0, 1

    return render_template(
        "announcements.html",
        announcements=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        target=target,
        targets=TARGETS,
        types=TYPES,
        courses=fetch_all("courses", select="id, title", order_by="title"),
    )


@bp.route("/announcements/create", methods=["POST"])
def create_announcement():
    title = forms.text(request.form, "title")
    message = forms.text(request.form, "message")
    if not title or not message:
        flash("Title and message are required.", "danger")
        return redirect(url_for("communications.announcements"))

    payload = {
        "title": title,
        "message": message,
        "target": forms.text(request.form, "target", "all"),
        "type": forms.text(request.form, "type", "info"),
        "course_id": forms.uuid_or_none(request.form, "course_id"),
        "is_active": forms.boolean(request.form, "is_active"),
        "is_pinned": forms.boolean(request.form, "is_pinned"),
        "expires_at": forms.timestamp(request.form, "expires_at"),
    }
    try:
        insert_row("announcements", payload)
        flash("Announcement created.", "success")
        if forms.boolean(request.form, "also_notify"):
            _broadcast(title, message, payload["type"], payload["target"])
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("communications.announcements"))


@bp.route("/announcements/<announcement_id>/update", methods=["POST"])
def update_announcement(announcement_id):
    try:
        update_row(
            "announcements",
            announcement_id,
            {
                "title": forms.text(request.form, "title"),
                "message": forms.text(request.form, "message"),
                "target": forms.text(request.form, "target", "all"),
                "type": forms.text(request.form, "type", "info"),
                "course_id": forms.uuid_or_none(request.form, "course_id"),
                "is_active": forms.boolean(request.form, "is_active"),
                "is_pinned": forms.boolean(request.form, "is_pinned"),
                "expires_at": forms.timestamp(request.form, "expires_at"),
            },
        )
        flash("Announcement updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("communications.announcements"))


@bp.route("/announcements/<announcement_id>/toggle", methods=["POST"])
def toggle_announcement(announcement_id):
    active = forms.boolean(request.form, "is_active")
    try:
        update_row("announcements", announcement_id, {"is_active": active})
        flash("Announcement activated." if active else "Announcement archived.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("communications.announcements"))


@bp.route("/announcements/<announcement_id>/delete", methods=["POST"])
def delete_announcement(announcement_id):
    try:
        delete_row("announcements", announcement_id)
        flash("Announcement deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("communications.announcements"))


# --- Notifications -----------------------------------------------------------


def _broadcast(title, message, type_, target):
    ids = _recipients(target)
    if not ids:
        flash("No users matched that audience - nothing was sent.", "warning")
        return 0
    rows = [
        {"user_id": uid, "title": title, "message": message, "type": type_}
        for uid in ids
    ]
    # Chunked so a large audience does not blow the request size limit.
    for start in range(0, len(rows), 200):
        supabase.table("notifications").insert(rows[start : start + 200]).execute()
    flash(f"Notification delivered to {len(rows)} user(s).", "success")
    return len(rows)


@bp.route("/notifications")
def notifications():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    read = request.args.get("read", "all")

    filters = {}
    if read in {"true", "false"}:
        filters["is_read"] = read == "true"

    try:
        rows, total, pages = query_table(
            "notifications",
            search=search,
            search_fields=("title", "message"),
            filters=filters,
            order_by="created_at",
            page=page,
        )
        lookup = profiles_map([r.get("user_id") for r in rows])
        for row in rows:
            row["user"] = lookup.get(row.get("user_id")) or {}
        unread = query_table("notifications", filters={"is_read": False}, limit=1)[1]
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages, unread = [], 0, 1, 0

    return render_template(
        "notifications.html",
        notifications=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        read=read,
        unread=unread,
        types=TYPES,
        targets=TARGETS,
    )


@bp.route("/notifications/broadcast", methods=["POST"])
def broadcast():
    title = forms.text(request.form, "title")
    message = forms.text(request.form, "message")
    if not title or not message:
        flash("Title and message are required.", "danger")
        return redirect(url_for("communications.notifications"))
    try:
        _broadcast(
            title,
            message,
            forms.text(request.form, "type", "info"),
            forms.text(request.form, "target", "all"),
        )
    except Exception as exc:  # noqa: BLE001
        flash(f"Broadcast failed: {exc}", "danger")
    return redirect(url_for("communications.notifications"))


@bp.route("/notifications/<notification_id>/read", methods=["POST"])
def toggle_read(notification_id):
    is_read = forms.boolean(request.form, "is_read")
    try:
        update_row("notifications", notification_id, {"is_read": is_read})
        flash("Marked as read." if is_read else "Marked as unread.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("communications.notifications"))


@bp.route("/notifications/<notification_id>/delete", methods=["POST"])
def delete_notification(notification_id):
    try:
        delete_row("notifications", notification_id)
        flash("Notification deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("communications.notifications"))


@bp.route("/notifications/purge-read", methods=["POST"])
def purge_read():
    try:
        supabase.table("notifications").delete().eq("is_read", True).execute()
        flash("Read notifications cleared.", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not clear notifications: {exc}", "danger")
    return redirect(url_for("communications.notifications"))
