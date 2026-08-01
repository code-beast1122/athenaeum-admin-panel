from flask import Blueprint, flash, redirect, render_template, request, url_for

from security import protect_blueprint
from services import forms
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

bp = Blueprint("exams", __name__, url_prefix="/exams")
protect_blueprint(bp)

OPTIONS = ["A", "B", "C", "D"]


def _teachers():
    rows = fetch_all("profiles", select="id, full_name, role", order_by="full_name")
    return [r for r in rows if (r.get("role") or "").lower() in {"teacher", "admin"}] or rows


def _recalculate_marks(exam_id):
    questions = fetch_all("exam_questions", select="marks", filters={"exam_id": exam_id})
    total = sum(q.get("marks") or 0 for q in questions)
    update_row("exams", exam_id, {"total_marks": total})
    return total


@bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    course_id = request.args.get("course_id", "all")
    published = request.args.get("published", "all")

    filters = {"course_id": course_id}
    if published in {"true", "false"}:
        filters["is_published"] = published == "true"

    try:
        rows, total, pages = query_table(
            "exams",
            select="*, courses(id, title)",
            search=search,
            search_fields=("title",),
            filters=filters,
            order_by="created_at",
            page=page,
        )
        counts = {}
        for r in fetch_all("exam_results", select="exam_id"):
            counts[r["exam_id"]] = counts.get(r["exam_id"], 0) + 1
        question_counts = {}
        for q in fetch_all("exam_questions", select="exam_id"):
            question_counts[q["exam_id"]] = question_counts.get(q["exam_id"], 0) + 1
        for row in rows:
            row["attempts"] = counts.get(row["id"], 0)
            row["question_count"] = question_counts.get(row["id"], 0)
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages = [], 0, 1

    return render_template(
        "exams.html",
        exams=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        course_id=course_id,
        published=published,
        courses=fetch_all("courses", select="id, title", order_by="title"),
        teachers=_teachers(),
    )


@bp.route("/<exam_id>")
def detail(exam_id):
    try:
        exam = fetch_one("exams", exam_id, select="*, courses(id, title)")
        if not exam:
            flash("Exam not found.", "danger")
            return redirect(url_for("exams.index"))

        questions = fetch_all(
            "exam_questions", filters={"exam_id": exam_id}, order_by="order_index"
        )
        results, _, _ = query_table(
            "exam_results",
            filters={"exam_id": exam_id},
            order_by="completed_at",
            limit=200,
        )
        attach_profiles(results, "student_id", "student")
        avg = (
            round(sum(float(r.get("percentage") or 0) for r in results) / len(results), 1)
            if results
            else 0
        )
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("exams.index"))

    return render_template(
        "exam_detail.html",
        exam=exam,
        questions=questions,
        results=results,
        average=avg,
        pass_count=len([r for r in results if float(r.get("percentage") or 0) >= 50]),
        options=OPTIONS,
        courses=fetch_all("courses", select="id, title", order_by="title"),
        teachers=_teachers(),
        students=fetch_all("profiles", select="id, full_name", order_by="full_name"),
    )


@bp.route("/create", methods=["POST"])
def create():
    title = forms.text(request.form, "title")
    if not title:
        flash("An exam title is required.", "danger")
        return redirect(url_for("exams.index"))
    try:
        row = insert_row(
            "exams",
            {
                "title": title,
                "course_id": forms.uuid_or_none(request.form, "course_id"),
                "teacher_id": forms.uuid_or_none(request.form, "teacher_id"),
                "duration_minutes": forms.integer(request.form, "duration_minutes", 30),
                "is_published": forms.boolean(request.form, "is_published"),
                "total_marks": 0,
            },
        )
        flash("Exam created - now add questions.", "success")
        return redirect(url_for("exams.detail", exam_id=row["id"]))
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("exams.index"))


@bp.route("/<exam_id>/update", methods=["POST"])
def update(exam_id):
    try:
        update_row(
            "exams",
            exam_id,
            {
                "title": forms.text(request.form, "title"),
                "course_id": forms.uuid_or_none(request.form, "course_id"),
                "teacher_id": forms.uuid_or_none(request.form, "teacher_id"),
                "duration_minutes": forms.integer(request.form, "duration_minutes", 30),
                "is_published": forms.boolean(request.form, "is_published"),
            },
        )
        flash("Exam updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("exams.detail", exam_id=exam_id))


@bp.route("/<exam_id>/publish", methods=["POST"])
def toggle_publish(exam_id):
    publish = forms.boolean(request.form, "is_published")
    try:
        update_row("exams", exam_id, {"is_published": publish})
        flash("Exam published." if publish else "Exam unpublished.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("exams.index"))


@bp.route("/<exam_id>/delete", methods=["POST"])
def delete(exam_id):
    try:
        delete_where("exam_questions", "exam_id", exam_id)
        delete_where("exam_results", "exam_id", exam_id)
        delete_row("exams", exam_id)
        flash("Exam deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("exams.detail", exam_id=exam_id))
    return redirect(url_for("exams.index"))


# --- Questions ---------------------------------------------------------------


@bp.route("/<exam_id>/questions", methods=["POST"])
def create_question(exam_id):
    existing = fetch_all("exam_questions", select="order_index", filters={"exam_id": exam_id})
    next_index = max([q.get("order_index") or 0 for q in existing], default=0) + 1
    payload = {
        "exam_id": exam_id,
        "question_text": forms.text(request.form, "question_text"),
        "option_a": forms.text(request.form, "option_a"),
        "option_b": forms.text(request.form, "option_b"),
        "option_c": forms.text(request.form, "option_c"),
        "option_d": forms.text(request.form, "option_d"),
        "correct_option": forms.text(request.form, "correct_option", "A"),
        "marks": forms.integer(request.form, "marks", 1),
        "order_index": forms.integer(request.form, "order_index", next_index),
    }
    missing = [k for k in ("question_text", "option_a", "option_b", "option_c", "option_d") if not payload[k]]
    if missing:
        flash("The question text and all four options are required.", "danger")
        return redirect(url_for("exams.detail", exam_id=exam_id))
    try:
        insert_row("exam_questions", payload)
        _recalculate_marks(exam_id)
        flash("Question added.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("exams.detail", exam_id=exam_id))


@bp.route("/<exam_id>/questions/<question_id>/update", methods=["POST"])
def update_question(exam_id, question_id):
    try:
        update_row(
            "exam_questions",
            question_id,
            {
                "question_text": forms.text(request.form, "question_text"),
                "option_a": forms.text(request.form, "option_a"),
                "option_b": forms.text(request.form, "option_b"),
                "option_c": forms.text(request.form, "option_c"),
                "option_d": forms.text(request.form, "option_d"),
                "correct_option": forms.text(request.form, "correct_option", "A"),
                "marks": forms.integer(request.form, "marks", 1),
                "order_index": forms.integer(request.form, "order_index", 1),
            },
        )
        _recalculate_marks(exam_id)
        flash("Question updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("exams.detail", exam_id=exam_id))


@bp.route("/<exam_id>/questions/<question_id>/delete", methods=["POST"])
def delete_question(exam_id, question_id):
    try:
        delete_row("exam_questions", question_id)
        _recalculate_marks(exam_id)
        flash("Question deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("exams.detail", exam_id=exam_id))


@bp.route("/<exam_id>/results", methods=["POST"])
def create_result(exam_id):
    """Record an attempt taken on paper or lost to a crash."""
    student_id = forms.uuid_or_none(request.form, "student_id")
    if not student_id:
        flash("Pick the student this attempt belongs to.", "danger")
        return redirect(url_for("exams.detail", exam_id=exam_id))

    score = forms.integer(request.form, "score", 0) or 0
    exam = fetch_one("exams", exam_id, select="total_marks")
    total = forms.integer(request.form, "total_marks", (exam or {}).get("total_marks") or 0) or 0
    try:
        insert_row(
            "exam_results",
            {
                "exam_id": exam_id,
                "student_id": student_id,
                "score": score,
                "total_marks": total,
                "percentage": round(score / total * 100, 2) if total else 0,
                "time_taken_minutes": forms.integer(request.form, "time_taken_minutes", 0),
                "xp_awarded": forms.integer(request.form, "xp_awarded", 0),
            },
        )
        flash("Attempt recorded.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("exams.detail", exam_id=exam_id))


@bp.route("/<exam_id>/results/<result_id>/update", methods=["POST"])
def update_result(exam_id, result_id):
    """Regrade an attempt. Percentage is derived so it can never disagree."""
    score = forms.integer(request.form, "score", 0) or 0
    total = forms.integer(request.form, "total_marks", 0) or 0
    try:
        update_row(
            "exam_results",
            result_id,
            {
                "score": score,
                "total_marks": total,
                "percentage": round(score / total * 100, 2) if total else 0,
                "time_taken_minutes": forms.integer(request.form, "time_taken_minutes", 0),
            },
        )
        flash("Result regraded.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("exams.detail", exam_id=exam_id))


@bp.route("/<exam_id>/results/<result_id>/delete", methods=["POST"])
def delete_result(exam_id, result_id):
    try:
        delete_row("exam_results", result_id)
        flash("Attempt cleared - the student can retake the exam.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("exams.detail", exam_id=exam_id))
