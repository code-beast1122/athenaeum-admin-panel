import csv
import io
from collections import defaultdict

from flask import Blueprint, Response, flash, render_template, request

from security import protect_blueprint
from services.db import DbError, fetch_all

bp = Blueprint("reports", __name__, url_prefix="/reports")
protect_blueprint(bp)

# Every table in the schema is exportable; the column list keeps CSVs readable
# and doubles as an allowlist, since the table name comes from the URL.
EXPORTABLE = {
    "profiles": "id, full_name, role, status, xp, streak_days, phone, parent_id, created_at",
    "courses": "id, title, category, subject, price, instructor_id, is_published, created_at",
    "modules": "id, course_id, title, order_index",
    "lessons": "id, module_id, title, video_url, duration_minutes, order_index, is_free_preview",
    "enrollments": "id, student_id, course_id, payment_status, transaction_id, enrolled_at, paid_at, expires_at",
    "user_progress": "id, student_id, lesson_id, completed, completed_at, xp_awarded",
    "exams": "id, title, course_id, teacher_id, duration_minutes, total_marks, is_published, created_at",
    "exam_questions": "id, exam_id, question_text, correct_option, marks, order_index",
    "exam_results": "id, student_id, exam_id, score, total_marks, percentage, time_taken_minutes, completed_at",
    "live_classes": "id, title, subject, teacher_name, teacher_id, course_id, start_time, end_time, status, meeting_link",
    "announcements": "id, title, message, target, type, is_active, is_pinned, expires_at, created_at",
    "notifications": "id, user_id, title, message, type, is_read, created_at",
    "teacher_access_codes": "id, code, course_id, is_used, used_by, used_at, created_at",
    "community_posts": "id, author_name, author_role, category, is_approved, is_pinned, likes_count, comments_count, created_at",
    "post_comments": "id, post_id, author_name, author_role, content, created_at",
    "post_likes": "id, post_id, user_id, created_at",
    "course_materials": "id, course_id, teacher_name, title, material_type, file_url, link_url, created_at",
    "parent_child_links": "id, parent_id, student_id, is_verified, linked_at",
    "cart_items": "id, user_id, course_id, added_at",
    "ai_usage": "id, user_id, usage_date, questions_used, quizzes_used",
}


@bp.route("/")
def index():
    try:
        courses = fetch_all("courses", select="id, title, price, category, is_published")
        enrollments = fetch_all("enrollments", select="course_id, payment_status, student_id")
        profiles = fetch_all("profiles", select="id, full_name, xp, streak_days, role")
        ai_rows = fetch_all("ai_usage", select="user_id, questions_used, quizzes_used")
        results = fetch_all("exam_results", select="exam_id, percentage, student_id")
    except DbError as exc:
        flash(str(exc), "danger")
        courses, enrollments, profiles, ai_rows, results = [], [], [], [], []

    course_lookup = {c["id"]: c for c in courses}
    per_course = defaultdict(lambda: {"count": 0, "revenue": 0.0})
    for row in enrollments:
        course = course_lookup.get(row.get("course_id"))
        if not course:
            continue
        bucket = per_course[course["id"]]
        bucket["count"] += 1
        if (row.get("payment_status") or "").lower() == "paid":
            bucket["revenue"] += float(course.get("price") or 0)

    top_courses = sorted(
        (
            {
                "title": course_lookup[cid]["title"],
                "category": course_lookup[cid].get("category"),
                "price": course_lookup[cid].get("price") or 0,
                "count": data["count"],
                "revenue": data["revenue"],
            }
            for cid, data in per_course.items()
        ),
        key=lambda c: (c["revenue"], c["count"]),
        reverse=True,
    )[:10]

    leaderboard = sorted(
        (p for p in profiles if (p.get("role") or "").lower() == "student"),
        key=lambda p: (p.get("xp") or 0),
        reverse=True,
    )[:10]

    ai_by_user = defaultdict(int)
    for row in ai_rows:
        ai_by_user[row.get("user_id")] += (row.get("questions_used") or 0) + (
            row.get("quizzes_used") or 0
        )
    name_lookup = {p["id"]: p.get("full_name") or "Unknown" for p in profiles}
    top_ai = sorted(
        ({"name": name_lookup.get(uid, "Unknown"), "total": total} for uid, total in ai_by_user.items()),
        key=lambda r: r["total"],
        reverse=True,
    )[:10]

    scores = [float(r.get("percentage") or 0) for r in results]
    summary = {
        "total_revenue": sum(c["revenue"] for c in top_courses),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "attempts": len(results),
        "published": len([c for c in courses if c.get("is_published")]),
        "ai_total": sum(ai_by_user.values()),
    }

    return render_template(
        "reports.html",
        top_courses=top_courses,
        leaderboard=leaderboard,
        top_ai=top_ai,
        summary=summary,
        exportable=sorted(EXPORTABLE),
    )


@bp.route("/export/<table_name>")
def export(table_name):
    columns = EXPORTABLE.get(table_name)
    if not columns:
        return Response("Unknown table", status=404, mimetype="text/plain")

    try:
        rows = fetch_all(table_name, select=columns, limit=10000)
    except DbError as exc:
        return Response(f"Export failed: {exc}", status=502, mimetype="text/plain")

    fields = [c.strip() for c in columns.split(",")]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row.get(f, "") for f in fields})

    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={table_name}.csv"},
    )
