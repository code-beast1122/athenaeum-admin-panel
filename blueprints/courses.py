from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import supabase
from security import protect_blueprint
from services import forms
from services.progress import course_completion
from services.db import (
    DbError,
    attach_profiles,
    delete_row,
    delete_where,
    fetch_all,
    fetch_one,
    insert_row,
    query_table,
    update_row,
)

bp = Blueprint("courses", __name__, url_prefix="/courses")
protect_blueprint(bp)

CATEGORIES = ["o_levels", "a_levels", "matric", "inter", "skill", "academic", "tech"]
MATERIAL_TYPES = ["pdf", "announcement", "link", "image"]


def _instructors():
    rows = fetch_all("profiles", select="id, full_name, role", order_by="full_name")
    return [r for r in rows if (r.get("role") or "").lower() in {"teacher", "admin"}] or rows


def _course_payload(form):
    return {
        "title": forms.text(form, "title"),
        "description": forms.text(form, "description", allow_empty=True),
        "category": forms.text(form, "category"),
        "subject": forms.text(form, "subject", allow_empty=True),
        "thumbnail_url": forms.text(form, "thumbnail_url", allow_empty=True),
        "price": forms.number(form, "price", 0),
        "is_free_preview": forms.boolean(form, "is_free_preview"),
        "is_published": forms.boolean(form, "is_published"),
        "instructor_id": forms.uuid_or_none(form, "instructor_id"),
        "what_you_will_learn": forms.string_list(form, "what_you_will_learn"),
    }


@bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "all")
    published = request.args.get("published", "all")

    filters = {"category": category}
    if published in {"true", "false"}:
        filters["is_published"] = published == "true"

    try:
        rows, total, pages = query_table(
            "courses",
            select="*, profiles(id, full_name)",
            search=search,
            search_fields=("title", "subject", "description"),
            filters=filters,
            order_by="created_at",
            page=page,
        )
        enrollment_counts = {}
        for row in fetch_all("enrollments", select="course_id"):
            key = row.get("course_id")
            enrollment_counts[key] = enrollment_counts.get(key, 0) + 1
        for row in rows:
            row["enrollment_count"] = enrollment_counts.get(row["id"], 0)
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages = [], 0, 1

    return render_template(
        "courses.html",
        courses=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        category=category,
        published=published,
        categories=CATEGORIES,
        instructors=_instructors(),
    )


@bp.route("/<course_id>")
def detail(course_id):
    try:
        course = fetch_one("courses", course_id, select="*, profiles(id, full_name)")
        if not course:
            flash("Course not found.", "danger")
            return redirect(url_for("courses.index"))

        modules = fetch_all(
            "modules", filters={"course_id": course_id}, order_by="order_index"
        )
        module_ids = [m["id"] for m in modules]
        lessons = []
        if module_ids:
            lessons = (
                supabase.table("lessons")
                .select("*")
                .in_("module_id", module_ids)
                .order("order_index")
                .execute()
                .data
                or []
            )
        for module in modules:
            module["lessons"] = [l for l in lessons if l["module_id"] == module["id"]]

        enrollments, _, _ = query_table(
            "enrollments",
            filters={"course_id": course_id},
            order_by="enrolled_at",
            limit=200,
        )
        attach_profiles(enrollments, "student_id", "student")

        completion = course_completion(
            course_id, [e.get("student_id") for e in enrollments if e.get("student_id")]
        )
        for row in enrollments:
            row["progress"] = completion.get(row.get("student_id")) or {
                "completed": 0,
                "total": len(lessons),
                "percent": 0,
            }

        exams = fetch_all("exams", filters={"course_id": course_id}, order_by="created_at", desc=True)
        materials = fetch_all(
            "course_materials", filters={"course_id": course_id}, order_by="created_at", desc=True
        )
        classes = fetch_all(
            "live_classes", filters={"course_id": course_id}, order_by="start_time", desc=True
        )
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("courses.index"))

    return render_template(
        "course_detail.html",
        course=course,
        modules=modules,
        enrollments=enrollments,
        exams=exams,
        materials=materials,
        classes=classes,
        categories=CATEGORIES,
        instructors=_instructors(),
        material_types=MATERIAL_TYPES,
        lesson_count=len(lessons),
    )


@bp.route("/create", methods=["POST"])
def create():
    payload = _course_payload(request.form)
    if not payload["title"]:
        flash("A course title is required.", "danger")
        return redirect(url_for("courses.index"))
    try:
        row = insert_row("courses", payload)
        flash("Course created.", "success")
        return redirect(url_for("courses.detail", course_id=row["id"]))
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("courses.index"))


@bp.route("/<course_id>/update", methods=["POST"])
def update(course_id):
    try:
        update_row("courses", course_id, _course_payload(request.form))
        flash("Course updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("courses.detail", course_id=course_id))


@bp.route("/<course_id>/publish", methods=["POST"])
def toggle_publish(course_id):
    publish = forms.boolean(request.form, "is_published")
    try:
        update_row("courses", course_id, {"is_published": publish})
        flash("Course published." if publish else "Course moved to draft.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("courses.index"))


@bp.route("/<course_id>/delete", methods=["POST"])
def delete(course_id):
    try:
        modules = fetch_all("modules", select="id", filters={"course_id": course_id})
        for module in modules:
            delete_where("lessons", "module_id", module["id"])
        delete_where("modules", "course_id", course_id)
        for table_name in ("enrollments", "cart_items", "course_materials", "teacher_access_codes"):
            try:
                delete_where(table_name, "course_id", course_id)
            except DbError:
                pass
        delete_row("courses", course_id)
        flash("Course and its content deleted.", "success")
    except DbError as exc:
        flash(f"{exc} - detach exams, live classes or announcements first.", "danger")
        return redirect(url_for("courses.detail", course_id=course_id))
    return redirect(url_for("courses.index"))


# --- Modules -----------------------------------------------------------------


@bp.route("/<course_id>/modules", methods=["POST"])
def create_module(course_id):
    existing = fetch_all("modules", select="order_index", filters={"course_id": course_id})
    next_index = max([m.get("order_index") or 0 for m in existing], default=0) + 1
    try:
        insert_row(
            "modules",
            {
                "course_id": course_id,
                "title": forms.text(request.form, "title", "Untitled module"),
                "order_index": forms.integer(request.form, "order_index", next_index),
            },
        )
        flash("Module added.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("courses.detail", course_id=course_id))


@bp.route("/<course_id>/modules/<module_id>/update", methods=["POST"])
def update_module(course_id, module_id):
    try:
        update_row(
            "modules",
            module_id,
            forms.compact(
                {
                    "title": forms.text(request.form, "title"),
                    "order_index": forms.integer(request.form, "order_index"),
                }
            ),
        )
        flash("Module updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("courses.detail", course_id=course_id))


@bp.route("/<course_id>/modules/<module_id>/delete", methods=["POST"])
def delete_module(course_id, module_id):
    try:
        delete_where("lessons", "module_id", module_id)
        delete_row("modules", module_id)
        flash("Module deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("courses.detail", course_id=course_id))


# --- Lessons -----------------------------------------------------------------


@bp.route("/<course_id>/modules/<module_id>/lessons", methods=["POST"])
def create_lesson(course_id, module_id):
    existing = fetch_all("lessons", select="order_index", filters={"module_id": module_id})
    next_index = max([l.get("order_index") or 0 for l in existing], default=0) + 1
    try:
        insert_row(
            "lessons",
            {
                "module_id": module_id,
                "title": forms.text(request.form, "title", "Untitled lesson"),
                "video_url": forms.text(request.form, "video_url", allow_empty=True),
                "duration_minutes": forms.integer(request.form, "duration_minutes", 0),
                "order_index": forms.integer(request.form, "order_index", next_index),
                "is_free_preview": forms.boolean(request.form, "is_free_preview"),
            },
        )
        flash("Lesson added.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("courses.detail", course_id=course_id))


@bp.route("/<course_id>/lessons/<lesson_id>/update", methods=["POST"])
def update_lesson(course_id, lesson_id):
    try:
        update_row(
            "lessons",
            lesson_id,
            {
                "title": forms.text(request.form, "title"),
                "video_url": forms.text(request.form, "video_url", allow_empty=True),
                "duration_minutes": forms.integer(request.form, "duration_minutes", 0),
                "order_index": forms.integer(request.form, "order_index", 1),
                "is_free_preview": forms.boolean(request.form, "is_free_preview"),
            },
        )
        flash("Lesson updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("courses.detail", course_id=course_id))


@bp.route("/<course_id>/lessons/<lesson_id>/delete", methods=["POST"])
def delete_lesson(course_id, lesson_id):
    try:
        delete_where("user_progress", "lesson_id", lesson_id)
        delete_row("lessons", lesson_id)
        flash("Lesson deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("courses.detail", course_id=course_id))


# --- Materials ---------------------------------------------------------------


@bp.route("/<course_id>/materials", methods=["POST"])
def create_material(course_id):
    try:
        insert_row(
            "course_materials",
            {
                "course_id": course_id,
                "teacher_id": forms.uuid_or_none(request.form, "teacher_id"),
                "teacher_name": forms.text(request.form, "teacher_name", "Admin"),
                "title": forms.text(request.form, "title", "Untitled"),
                "description": forms.text(request.form, "description", allow_empty=True),
                "material_type": forms.text(request.form, "material_type", "link"),
                "file_url": forms.text(request.form, "file_url", allow_empty=True),
                "link_url": forms.text(request.form, "link_url", allow_empty=True),
                "is_pinned": forms.boolean(request.form, "is_pinned"),
            },
        )
        flash("Material added.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("courses.detail", course_id=course_id))


@bp.route("/<course_id>/materials/<material_id>/delete", methods=["POST"])
def delete_material(course_id, material_id):
    try:
        delete_row("course_materials", material_id)
        flash("Material deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("courses.detail", course_id=course_id))
