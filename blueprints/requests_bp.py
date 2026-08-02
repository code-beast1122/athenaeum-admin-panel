import secrets
import string

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

bp = Blueprint("requests", __name__)
protect_blueprint(bp)

TRIAL_STATUSES = ["pending", "contacted", "scheduled", "converted", "rejected"]
CODE_ALPHABET = string.hexdigits.upper()[:16]  # 0-9A-F


def _generate_code(existing=()):
    """Mint an unused code in the same shape the database generator uses.

    The DB has its own `generate_teacher_code()`, but it authorises through
    `auth.uid()` and the portal signs in with its own password, so it cannot be
    called from here. Matching its `ATH-` + 8 hex format keeps codes uniform
    whichever side issued them; `code` is UNIQUE, so avoid a collision rather
    than let the insert fail.
    """
    for _ in range(20):
        code = "ATH-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        if code not in existing:
            return code
    return code


# --- Free trial requests -----------------------------------------------------


@bp.route("/trial-requests")
def trial_requests():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "all")

    try:
        rows, total, pages = query_table(
            "free_trial_requests",
            search=search,
            search_fields=("full_name", "email", "phone", "course_name"),
            filters={"status": status},
            order_by="created_at",
            page=page,
        )
        pending = query_table("free_trial_requests", filters={"status": "pending"}, limit=1)[1]
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages, pending = [], 0, 1, 0

    return render_template(
        "trial_requests.html",
        requests=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        status=status,
        pending=pending,
        statuses=TRIAL_STATUSES,
    )


@bp.route("/trial-requests/create", methods=["POST"])
def create_trial_request():
    """Log a request that arrived by phone or WhatsApp."""
    payload = {
        "full_name": forms.text(request.form, "full_name"),
        "email": forms.text(request.form, "email"),
        "phone": forms.text(request.form, "phone"),
        "course_name": forms.text(request.form, "course_name"),
        "preferred_time": forms.text(request.form, "preferred_time"),
        "status": forms.text(request.form, "status", "pending"),
        "user_id": forms.uuid_or_none(request.form, "user_id"),
    }
    missing = [k for k in ("full_name", "email", "phone", "course_name", "preferred_time")
               if not payload[k]]
    if missing:
        flash("Name, email, phone, course and preferred time are all required.", "danger")
        return redirect(url_for("requests.trial_requests"))
    try:
        insert_row("free_trial_requests", forms.compact(payload))
        flash("Trial request logged.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("requests.trial_requests"))


@bp.route("/trial-requests/<request_id>/status", methods=["POST"])
def set_trial_status(request_id):
    status = forms.text(request.form, "status", "pending")
    try:
        update_row("free_trial_requests", request_id, {"status": status})
        flash(f"Request marked as {status}.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("requests.trial_requests"))


@bp.route("/trial-requests/<request_id>/delete", methods=["POST"])
def delete_trial_request(request_id):
    try:
        delete_row("free_trial_requests", request_id)
        flash("Request deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("requests.trial_requests"))


# --- Teacher access codes ----------------------------------------------------


@bp.route("/access-codes")
def access_codes():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    used = request.args.get("used", "all")
    course_id = request.args.get("course_id", "all")

    filters = {"course_id": course_id}
    if used in {"true", "false"}:
        filters["is_used"] = used == "true"

    try:
        rows, total, pages = query_table(
            "teacher_access_codes",
            select="*, courses(id, title)",
            search=search,
            search_fields=("code",),
            filters=filters,
            order_by="created_at",
            page=page,
        )
        lookup = profiles_map([r.get("used_by") for r in rows])
        for row in rows:
            row["user"] = lookup.get(row.get("used_by")) or {}
            # A code flagged used but with no owner means the teacher app
            # closed it without recording who redeemed it.
            row["unattributed"] = bool(row.get("is_used")) and not row.get("used_by")
        available = query_table("teacher_access_codes", filters={"is_used": False}, limit=1)[1]
        redeemed = query_table("teacher_access_codes", filters={"is_used": True}, limit=1)[1]
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages, available, redeemed = [], 0, 1, 0, 0

    teachers = [
        p
        for p in fetch_all("profiles", select="id, full_name, role", order_by="full_name")
        if (p.get("role") or "").lower() in {"teacher", "admin"}
    ]

    return render_template(
        "access_codes.html",
        codes=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        used=used,
        course_id=course_id,
        available=available,
        redeemed=redeemed,
        teachers=teachers,
        courses=fetch_all("courses", select="id, title", order_by="title"),
    )


@bp.route("/access-codes/generate", methods=["POST"])
def generate_codes():
    quantity = max(1, min(50, forms.integer(request.form, "quantity", 1) or 1))
    course_id = forms.uuid_or_none(request.form, "course_id")
    created = 0
    try:
        taken = {c["code"] for c in fetch_all("teacher_access_codes", select="code")}
        for _ in range(quantity):
            code = _generate_code(taken)
            taken.add(code)
            insert_row(
                "teacher_access_codes",
                {"code": code, "course_id": course_id, "is_used": False},
            )
            created += 1
        flash(f"{created} access code(s) generated.", "success")
    except DbError as exc:
        flash(f"Generated {created} code(s) before failing: {exc}", "danger")
    return redirect(url_for("requests.access_codes"))


@bp.route("/access-codes/<code_id>/redeem", methods=["POST"])
def redeem_code(code_id):
    """Record who used a code.

    The teacher app hands out the course but does not write back to this table,
    so codes stay `is_used = false` for ever and can be redeemed again. Marking
    them here closes the code and keeps the audit trail.
    """
    teacher_id = forms.uuid_or_none(request.form, "used_by")
    if not teacher_id:
        flash("Pick the teacher who used this code.", "danger")
        return redirect(url_for("requests.access_codes"))
    try:
        update_row(
            "teacher_access_codes",
            code_id,
            {"is_used": True, "used_by": teacher_id, "used_at": forms.now_iso()},
        )
        flash("Code marked as redeemed.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("requests.access_codes"))


@bp.route("/access-codes/<code_id>/reset", methods=["POST"])
def reset_code(code_id):
    try:
        update_row(
            "teacher_access_codes",
            code_id,
            {"is_used": False, "used_by": None, "used_at": None},
        )
        flash("Code reset and available again.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("requests.access_codes"))


@bp.route("/access-codes/<code_id>/delete", methods=["POST"])
def delete_code(code_id):
    try:
        delete_row("teacher_access_codes", code_id)
        flash("Access code deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("requests.access_codes"))


@bp.route("/access-codes/purge-used", methods=["POST"])
def purge_used():
    try:
        supabase.table("teacher_access_codes").delete().eq("is_used", True).execute()
        flash("Used codes cleared.", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not clear codes: {exc}", "danger")
    return redirect(url_for("requests.access_codes"))
