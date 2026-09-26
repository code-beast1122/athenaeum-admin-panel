import csv
import io

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from extensions import supabase
from security import protect_blueprint
from services import forms
from services.access import PAID, PAYMENT_STATUSES
from services.db import (
    DbError,
    attach_profiles,
    delete_row,
    fetch_all,
    insert_row,
    query_table,
    update_row,
)

bp = Blueprint("enrollments", __name__, url_prefix="/enrollments")
protect_blueprint(bp)



def _student_ids_matching(term):
    """`enrollments.student_id` points at auth.users, so PostgREST cannot embed
    the profile. Resolve names to ids first, then filter on those ids."""
    rows = (
        supabase.table("profiles")
        .select("id")
        .ilike("full_name", f"*{term}*")
        .limit(500)
        .execute()
        .data
        or []
    )
    return [r["id"] for r in rows]


def _load(page=1, search="", course_id="all", status="all", limit=None):
    in_filters = None
    if search:
        ids = _student_ids_matching(search)
        in_filters = {"student_id": ids or ["00000000-0000-0000-0000-000000000000"]}

    rows, total, pages = query_table(
        "enrollments",
        select="*, courses(id, title, price, category)",
        filters={"course_id": course_id, "payment_status": status},
        in_filters=in_filters,
        order_by="enrolled_at",
        page=page,
        limit=limit,
    )
    attach_profiles(rows, "student_id", "student")
    return rows, total, pages


@bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    course_id = request.args.get("course_id", "all")
    status = request.args.get("status", "all")

    try:
        rows, total, pages = _load(page, search, course_id, status)
        revenue = sum(
            (r.get("courses") or {}).get("price") or 0
            for r in rows
            if (r.get("payment_status") or "").lower() == "paid"
        )
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages, revenue = [], 0, 1, 0

    return render_template(
        "enrollments.html",
        enrollments=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        course_id=course_id,
        status=status,
        revenue=revenue,
        statuses=PAYMENT_STATUSES,
        courses=fetch_all("courses", select="id, title", order_by="title"),
        students=fetch_all("profiles", select="id, full_name", order_by="full_name"),
    )


@bp.route("/create", methods=["POST"])
def create():
    student_id = forms.uuid_or_none(request.form, "student_id")
    course_id = forms.uuid_or_none(request.form, "course_id")
    if not student_id or not course_id:
        flash("Both a student and a course are required.", "danger")
        return redirect(url_for("enrollments.index"))
    try:
        insert_row(
            "enrollments",
            {
                "student_id": student_id,
                "course_id": course_id,
                "payment_status": forms.text(request.form, "payment_status", PAID),
                "transaction_id": forms.text(request.form, "transaction_id", allow_empty=True),
            },
        )
        flash("Enrollment created.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("enrollments.index"))


@bp.route("/<enrollment_id>/update", methods=["POST"])
def update(enrollment_id):
    try:
        update_row(
            "enrollments",
            enrollment_id,
            {
                "payment_status": forms.text(request.form, "payment_status", PAID),
                "transaction_id": forms.text(request.form, "transaction_id", allow_empty=True),
            },
        )
        flash("Enrollment updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("enrollments.index"))


@bp.route("/<enrollment_id>/delete", methods=["POST"])
def delete(enrollment_id):
    try:
        delete_row("enrollments", enrollment_id)
        flash("Enrollment removed.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("enrollments.index"))


@bp.route("/export")
def export():
    try:
        rows, _, _ = _load(
            search=request.args.get("q", "").strip(),
            course_id=request.args.get("course_id", "all"),
            status=request.args.get("status", "all"),
            limit=5000,
        )
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("enrollments.index"))

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Student", "Course", "Price", "Payment status", "Transaction", "Enrolled at"])
    for row in rows:
        writer.writerow(
            [
                (row.get("student") or {}).get("full_name", ""),
                (row.get("courses") or {}).get("title", ""),
                (row.get("courses") or {}).get("price", ""),
                row.get("payment_status", ""),
                row.get("transaction_id", ""),
                row.get("enrolled_at", ""),
            ]
        )
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=enrollments.csv"},
    )
