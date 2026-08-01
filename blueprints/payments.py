"""Payment approvals.

There is no separate payments table: a payment *is* an `enrollments` row, where
`payment_status` moves pending -> paid and `transaction_id` holds the reference
the student submitted. This blueprint is the review queue for that transition.
"""

import csv
import io
from collections import defaultdict
from datetime import datetime, timezone

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from security import protect_blueprint
from services import forms
from services.db import (
    DbError,
    attach_profiles,
    delete_row,
    fetch_all,
    fetch_one,
    insert_row,
    query_table,
    update_row,
)

bp = Blueprint("payments", __name__, url_prefix="/payments")
protect_blueprint(bp)

PENDING = "pending"
APPROVED = "paid"
PAID_STATES = {"paid", "active"}


def _course_prices():
    return {c["id"]: (c.get("price") or 0) for c in fetch_all("courses", select="id, price")}


def _notify(user_id, title, message, type_="info"):
    """Best effort - a failed notification must not undo the approval."""
    if not user_id:
        return
    try:
        insert_row(
            "notifications",
            {"user_id": user_id, "title": title, "message": message, "type": type_},
        )
    except DbError:
        pass


def _enrollment_with_course(enrollment_id):
    return fetch_one(
        "enrollments", enrollment_id, select="*, courses(id, title, price)"
    )


@bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    status = request.args.get("status", PENDING)
    course_id = request.args.get("course_id", "all")

    prices = _course_prices()
    try:
        in_filters = None
        if search:
            matches = fetch_all("profiles", select="id, full_name")
            ids = [
                p["id"]
                for p in matches
                if search.lower() in (p.get("full_name") or "").lower()
            ]
            in_filters = {"student_id": ids or ["00000000-0000-0000-0000-000000000000"]}

        rows, total, pages = query_table(
            "enrollments",
            select="*, courses(id, title, price, category)",
            filters={"payment_status": status, "course_id": course_id},
            in_filters=in_filters,
            order_by="enrolled_at",
            page=page,
        )
        attach_profiles(rows, "student_id", "student")

        all_rows = fetch_all(
            "enrollments", select="payment_status, course_id, enrolled_at"
        )
        summary = defaultdict(float)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        for row in all_rows:
            state = (row.get("payment_status") or "").lower()
            price = prices.get(row.get("course_id"), 0) or 0
            if state == PENDING:
                summary["pending_count"] += 1
                summary["pending_value"] += price
            elif state in PAID_STATES:
                summary["approved_count"] += 1
                summary["approved_value"] += price
                if str(row.get("enrolled_at") or "").startswith(month):
                    summary["month_value"] += price
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages, summary = [], 0, 1, defaultdict(float)

    return render_template(
        "payments.html",
        payments=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        status=status,
        course_id=course_id,
        summary=summary,
        statuses=[PENDING, "paid", "active", "trial", "free"],
        courses=fetch_all("courses", select="id, title", order_by="title"),
    )


@bp.route("/<enrollment_id>/approve", methods=["POST"])
def approve(enrollment_id):
    try:
        row = _enrollment_with_course(enrollment_id)
        if not row:
            flash("That enrollment no longer exists.", "danger")
            return redirect(url_for("payments.index"))

        payload = {"payment_status": APPROVED}
        reference = forms.text(request.form, "transaction_id")
        if reference:
            payload["transaction_id"] = reference
        update_row("enrollments", enrollment_id, payload)

        course_title = (row.get("courses") or {}).get("title", "your course")
        # A verified payment also lifts the student off the trial plan.
        if forms.boolean(request.form, "upgrade_plan"):
            update_row(
                "profiles",
                row.get("student_id"),
                {"plan_type": "paid", "updated_at": forms.now_iso()},
            )
        _notify(
            row.get("student_id"),
            "Payment approved",
            f'Your payment for "{course_title}" has been verified. Enjoy the course!',
            "success",
        )
        flash(f'Payment approved for "{course_title}".', "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("payments.index"))


@bp.route("/<enrollment_id>/reject", methods=["POST"])
def reject(enrollment_id):
    """Rejecting removes the enrollment, so the student loses course access."""
    try:
        row = _enrollment_with_course(enrollment_id)
        if not row:
            flash("That enrollment no longer exists.", "danger")
            return redirect(url_for("payments.index"))

        course_title = (row.get("courses") or {}).get("title", "the course")
        reason = forms.text(request.form, "reason", "")
        delete_row("enrollments", enrollment_id)
        _notify(
            row.get("student_id"),
            "Payment could not be verified",
            f'We could not verify your payment for "{course_title}".'
            + (f" {reason}" if reason else " Please contact support."),
            "warning",
        )
        flash(f'Payment rejected and enrollment removed for "{course_title}".', "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("payments.index"))


@bp.route("/<enrollment_id>/revert", methods=["POST"])
def revert(enrollment_id):
    """Undo an approval - puts the row back in the queue."""
    try:
        update_row("enrollments", enrollment_id, {"payment_status": PENDING})
        flash("Moved back to pending.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("payments.index"))


@bp.route("/<enrollment_id>/reference", methods=["POST"])
def set_reference(enrollment_id):
    try:
        update_row(
            "enrollments",
            enrollment_id,
            {"transaction_id": forms.text(request.form, "transaction_id", allow_empty=True)},
        )
        flash("Transaction reference saved.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("payments.index"))


@bp.route("/approve-all", methods=["POST"])
def approve_all():
    course_id = forms.uuid_or_none(request.form, "course_id")
    filters = {"payment_status": PENDING}
    if course_id:
        filters["course_id"] = course_id
    try:
        rows = fetch_all("enrollments", select="id, student_id, course_id", filters=filters)
        titles = {c["id"]: c["title"] for c in fetch_all("courses", select="id, title")}
        for row in rows:
            update_row("enrollments", row["id"], {"payment_status": APPROVED})
            _notify(
                row.get("student_id"),
                "Payment approved",
                f'Your payment for "{titles.get(row.get("course_id"), "your course")}" '
                "has been verified.",
                "success",
            )
        flash(f"{len(rows)} payment(s) approved.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("payments.index"))


@bp.route("/carts")
def carts():
    """Abandoned carts: a course sat in the basket but was never enrolled."""
    try:
        rows, total, pages = query_table(
            "cart_items",
            select="*, courses(id, title, price)",
            order_by="added_at",
            page=request.args.get("page", 1, type=int),
        )
        attach_profiles(rows, "user_id", "student")

        enrolled = {
            (e.get("student_id"), e.get("course_id"))
            for e in fetch_all("enrollments", select="student_id, course_id")
        }
        for row in rows:
            row["already_enrolled"] = (row.get("user_id"), row.get("course_id")) in enrolled

        value = sum(
            (r.get("courses") or {}).get("price") or 0
            for r in rows
            if not r["already_enrolled"]
        )
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages, value = [], 0, 1, 0

    return render_template(
        "carts.html", items=rows, total=total, pages=pages,
        page=request.args.get("page", 1, type=int), value=value,
    )


@bp.route("/carts/<item_id>/convert", methods=["POST"])
def convert_cart(item_id):
    """Turn a basket item into a pending enrollment so it enters the queue."""
    try:
        item = fetch_one("cart_items", item_id, select="*, courses(title)")
        if not item:
            flash("That cart item no longer exists.", "danger")
            return redirect(url_for("payments.carts"))

        already = [
            e
            for e in fetch_all(
                "enrollments",
                select="id",
                filters={"student_id": item.get("user_id"), "course_id": item.get("course_id")},
            )
        ]
        if already:
            flash("That student is already enrolled in the course.", "warning")
        else:
            insert_row(
                "enrollments",
                {
                    "student_id": item.get("user_id"),
                    "course_id": item.get("course_id"),
                    "payment_status": PENDING,
                },
            )
            _notify(
                item.get("user_id"),
                "Enrollment started",
                f'We have started your enrollment for "{(item.get("courses") or {}).get("title", "your course")}". '
                "Send your payment reference to finish.",
                "info",
            )
            flash("Cart item converted to a pending enrollment.", "success")
        delete_row("cart_items", item_id)
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("payments.carts"))


@bp.route("/carts/<item_id>/delete", methods=["POST"])
def delete_cart(item_id):
    try:
        delete_row("cart_items", item_id)
        flash("Cart item removed.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("payments.carts"))


@bp.route("/carts/purge-enrolled", methods=["POST"])
def purge_enrolled_carts():
    """Drop basket items for courses the student has since enrolled in."""
    try:
        enrolled = {
            (e.get("student_id"), e.get("course_id"))
            for e in fetch_all("enrollments", select="student_id, course_id")
        }
        removed = 0
        for item in fetch_all("cart_items", select="id, user_id, course_id"):
            if (item.get("user_id"), item.get("course_id")) in enrolled:
                delete_row("cart_items", item["id"])
                removed += 1
        flash(f"{removed} stale cart item(s) removed.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("payments.carts"))


@bp.route("/export")
def export():
    prices = _course_prices()
    try:
        rows, _, _ = query_table(
            "enrollments",
            select="*, courses(title, price)",
            filters={"payment_status": request.args.get("status", "all")},
            order_by="enrolled_at",
            limit=5000,
        )
        attach_profiles(rows, "student_id", "student")
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("payments.index"))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Student", "Course", "Amount", "Status", "Reference", "Date"])
    for row in rows:
        writer.writerow(
            [
                (row.get("student") or {}).get("full_name", ""),
                (row.get("courses") or {}).get("title", ""),
                prices.get(row.get("course_id"), 0),
                row.get("payment_status", ""),
                row.get("transaction_id", ""),
                row.get("enrolled_at", ""),
            ]
        )
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=payments.csv"},
    )
