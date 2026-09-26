from collections import defaultdict

from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import supabase
from security import protect_blueprint
from services import forms
from services.auth_users import email_map
from services.db import (
    DbError,
    fetch_all,
    fetch_one,
    insert_row,
    query_table,
    update_row,
)

bp = Blueprint("teachers", __name__, url_prefix="/teachers")
protect_blueprint(bp)


def _course_stats():
    """course_id -> enrolled student count."""
    counts = defaultdict(int)
    for row in fetch_all("enrollments", select="course_id"):
        counts[row.get("course_id")] += 1
    return counts


@bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    status = request.args.get("status", "all")

    try:
        rows, total, pages = query_table(
            "profiles",
            search=search,
            search_fields=("full_name", "phone"),
            ilike_filters={"role": "teacher"},
            filters={"status": status},
            order_by="created_at",
            page=page,
        )

        emails = email_map()
        courses = fetch_all("courses", select="id, title, instructor_id, is_published")
        classes = fetch_all("live_classes", select="teacher_id, status")
        exams = fetch_all("exams", select="teacher_id")
        enrolled = _course_stats()

        for teacher in rows:
            owned = [c for c in courses if c.get("instructor_id") == teacher["id"]]
            teacher["email"] = emails.get(teacher["id"], "")
            teacher["course_count"] = len(owned)
            teacher["published_count"] = len([c for c in owned if c.get("is_published")])
            teacher["student_count"] = sum(enrolled.get(c["id"], 0) for c in owned)
            teacher["class_count"] = len([c for c in classes if c.get("teacher_id") == teacher["id"]])
            teacher["exam_count"] = len([e for e in exams if e.get("teacher_id") == teacher["id"]])

        unassigned = [c for c in courses if not c.get("instructor_id")]
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages, unassigned = [], 0, 1, []

    candidates = [
        p
        for p in fetch_all("profiles", select="id, full_name, role", order_by="full_name")
        if (p.get("role") or "").lower() != "teacher"
    ]

    return render_template(
        "teachers.html",
        teachers=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        status=status,
        unassigned=unassigned,
        candidates=candidates,
        statuses=["active", "blocked", "suspended"],
    )


@bp.route("/<teacher_id>")
def detail(teacher_id):
    try:
        teacher = fetch_one("profiles", teacher_id)
        if not teacher:
            flash("Teacher not found.", "danger")
            return redirect(url_for("teachers.index"))
        teacher["email"] = email_map().get(teacher_id, "")

        courses = fetch_all("courses", select="*", filters={"instructor_id": teacher_id})
        enrolled = _course_stats()
        for course in courses:
            course["enrollment_count"] = enrolled.get(course["id"], 0)

        classes = fetch_all(
            "live_classes",
            select="*, courses(title)",
            filters={"teacher_id": teacher_id},
            order_by="start_time",
            desc=True,
        )
        exams = fetch_all(
            "exams",
            select="*, courses(title)",
            filters={"teacher_id": teacher_id},
            order_by="created_at",
            desc=True,
        )
        materials = fetch_all(
            "course_materials",
            select="*, courses(title)",
            filters={"teacher_id": teacher_id},
            order_by="created_at",
            desc=True,
        )
        codes = fetch_all(
            "teacher_access_codes", select="*, courses(title)", filters={"used_by": teacher_id}
        )
        unassigned = [
            c
            for c in fetch_all("courses", select="id, title, instructor_id", order_by="title")
            if not c.get("instructor_id")
        ]
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("teachers.index"))

    return render_template(
        "teacher_detail.html",
        teacher=teacher,
        courses=courses,
        classes=classes,
        exams=exams,
        materials=materials,
        codes=codes,
        unassigned=unassigned,
        student_total=sum(c["enrollment_count"] for c in courses),
        statuses=["active", "blocked", "suspended"],
    )


@bp.route("/create", methods=["POST"])
def create():
    email = forms.text(request.form, "email")
    password = forms.text(request.form, "password")
    full_name = forms.text(request.form, "full_name")
    if not email or not password:
        flash("Email and password are required.", "danger")
        return redirect(url_for("teachers.index"))

    try:
        created = supabase.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": full_name or email},
            }
        )
        teacher_id = created.user.id
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not create the account: {exc}", "danger")
        return redirect(url_for("teachers.index"))

    profile = {
        "id": teacher_id,
        "full_name": full_name or email.split("@")[0],
        "role": "teacher",
        "status": "active",
        "phone": forms.text(request.form, "phone"),
    }
    try:
        if fetch_one("profiles", teacher_id):
            update_row("profiles", teacher_id, forms.compact(profile))
        else:
            insert_row("profiles", forms.compact(profile))

        course_id = forms.uuid_or_none(request.form, "course_id")
        if course_id:
            update_row("courses", course_id, {"instructor_id": teacher_id})
        flash(f"Teacher {email} created.", "success")
    except DbError as exc:
        flash(f"Account created but the profile failed: {exc}", "warning")

    return redirect(url_for("teachers.detail", teacher_id=teacher_id))


@bp.route("/promote", methods=["POST"])
def promote():
    """Turn an existing student/parent account into a teacher."""
    user_id = forms.uuid_or_none(request.form, "user_id")
    if not user_id:
        flash("Pick an account to promote.", "danger")
        return redirect(url_for("teachers.index"))
    try:
        update_row("profiles", user_id, {"role": "teacher", "updated_at": forms.now_iso()})
        flash("Account promoted to teacher.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("teachers.detail", teacher_id=user_id))


@bp.route("/<teacher_id>/role", methods=["POST"])
def set_role(teacher_id):
    role = forms.text(request.form, "role", "student")
    try:
        update_row("profiles", teacher_id, {"role": role, "updated_at": forms.now_iso()})
        flash(f"Role changed to {role}.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("teachers.index") if role != "teacher"
                    else url_for("teachers.detail", teacher_id=teacher_id))


@bp.route("/<teacher_id>/assign", methods=["POST"])
def assign_course(teacher_id):
    course_id = forms.uuid_or_none(request.form, "course_id")
    if not course_id:
        flash("Pick a course to assign.", "danger")
        return redirect(url_for("teachers.detail", teacher_id=teacher_id))
    try:
        update_row("courses", course_id, {"instructor_id": teacher_id})
        flash("Course assigned.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("teachers.detail", teacher_id=teacher_id))


@bp.route("/<teacher_id>/unassign/<course_id>", methods=["POST"])
def unassign_course(teacher_id, course_id):
    try:
        update_row("courses", course_id, {"instructor_id": None})
        flash("Course unassigned.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("teachers.detail", teacher_id=teacher_id))


@bp.route("/<teacher_id>/status", methods=["POST"])
def set_status(teacher_id):
    status = forms.text(request.form, "status", "active")
    try:
        update_row("profiles", teacher_id, {"status": status, "updated_at": forms.now_iso()})
        if status in {"blocked", "suspended"}:
            supabase.auth.admin.update_user_by_id(teacher_id, {"ban_duration": "876000h"})
        else:
            supabase.auth.admin.update_user_by_id(teacher_id, {"ban_duration": "none"})
        flash(f"Teacher marked as {status}.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    except Exception as exc:  # noqa: BLE001
        flash(f"Profile updated, but the auth ban could not be applied: {exc}", "warning")
    return redirect(request.referrer or url_for("teachers.index"))


@bp.route("/<teacher_id>/notify", methods=["POST"])
def notify(teacher_id):
    try:
        insert_row(
            "notifications",
            {
                "user_id": teacher_id,
                "title": forms.text(request.form, "title", "Message from Athenaeum"),
                "message": forms.text(request.form, "message", ""),
                "type": forms.text(request.form, "type", "info"),
            },
        )
        flash("Notification sent.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("teachers.detail", teacher_id=teacher_id))
