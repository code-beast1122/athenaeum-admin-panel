from flask import Blueprint, flash, redirect, render_template, request, url_for

from security import protect_blueprint
from services import forms
from services.db import (
    DbError,
    delete_row,
    fetch_all,
    insert_row,
    query_table,
    update_row,
)

bp = Blueprint("live_classes", __name__, url_prefix="/live-classes")
protect_blueprint(bp)

STATUSES = ["scheduled", "live", "ended"]


def _meeting_link(form, field="meeting_link"):
    """Tidy up a pasted meeting link.

    Teachers paste these by hand, so accept `meet.google.com/abc-defg-hij`
    without a scheme and store a URL the browser can actually open. Blank is
    allowed - a class can be scheduled before the link exists.
    """
    link = (form.get(field) or "").strip()
    if not link:
        return None
    if not link.startswith(("http://", "https://")):
        link = "https://" + link.lstrip("/")
    return link


@bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "all")
    course_id = request.args.get("course_id", "all")

    try:
        rows, total, pages = query_table(
            "live_classes",
            select="*, courses(id, title)",
            search=search,
            search_fields=("title", "subject", "teacher_name"),
            filters={"status": status, "course_id": course_id},
            order_by="start_time",
            page=page,
        )
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages = [], 0, 1

    teachers = [
        r
        for r in fetch_all("profiles", select="id, full_name, role", order_by="full_name")
        if (r.get("role") or "").lower() in {"teacher", "admin"}
    ]

    return render_template(
        "live_classes.html",
        classes=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        status=status,
        course_id=course_id,
        statuses=STATUSES,
        courses=fetch_all("courses", select="id, title", order_by="title"),
        teachers=teachers,
    )


@bp.route("/create", methods=["POST"])
def create():
    course_id = forms.uuid_or_none(request.form, "course_id")
    payload = {
        "course_id": course_id,
        "title": forms.text(request.form, "title"),
        "subject": forms.text(request.form, "subject", "General"),
        "teacher_name": forms.text(request.form, "teacher_name", "Athenaeum"),
        "teacher_id": forms.uuid_or_none(request.form, "teacher_id"),
        "description": forms.text(request.form, "description", allow_empty=True),
        "start_time": forms.timestamp(request.form, "start_time"),
        "end_time": forms.timestamp(request.form, "end_time"),
        "meeting_link": _meeting_link(request.form),
        "status": forms.text(request.form, "status", "scheduled"),
    }
    if not payload["title"] or not payload["start_time"] or not payload["end_time"]:
        flash("Title, start time and end time are required.", "danger")
        return redirect(url_for("live_classes.index"))
    try:
        insert_row("live_classes", payload)
        flash("Live class scheduled.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("live_classes.index"))


@bp.route("/<class_id>/update", methods=["POST"])
def update(class_id):
    payload = {
        "course_id": forms.uuid_or_none(request.form, "course_id"),
        "title": forms.text(request.form, "title"),
        "subject": forms.text(request.form, "subject"),
        "teacher_name": forms.text(request.form, "teacher_name"),
        "teacher_id": forms.uuid_or_none(request.form, "teacher_id"),
        "description": forms.text(request.form, "description", allow_empty=True),
        "start_time": forms.timestamp(request.form, "start_time"),
        "end_time": forms.timestamp(request.form, "end_time"),
        "status": forms.text(request.form, "status"),
    }
    try:
        payload = forms.compact(payload)
        # Set after compacting: clearing a wrong link means writing NULL, and
        # compact() drops Nones.
        if "meeting_link" in request.form:
            payload["meeting_link"] = _meeting_link(request.form)
        update_row("live_classes", class_id, payload)
        flash("Live class updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("live_classes.index"))


@bp.route("/<class_id>/link", methods=["POST"])
def set_link(class_id):
    """Attach or replace the meeting link from the list view."""
    link = _meeting_link(request.form)
    try:
        update_row("live_classes", class_id, {"meeting_link": link})
        flash("Meeting link saved." if link else "Meeting link removed.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("live_classes.index"))


@bp.route("/<class_id>/status", methods=["POST"])
def set_status(class_id):
    status = forms.text(request.form, "status", "scheduled")
    try:
        update_row("live_classes", class_id, {"status": status})
        flash(f"Class marked as {status}.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("live_classes.index"))


@bp.route("/<class_id>/delete", methods=["POST"])
def delete(class_id):
    try:
        delete_row("live_classes", class_id)
        flash("Live class deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("live_classes.index"))
