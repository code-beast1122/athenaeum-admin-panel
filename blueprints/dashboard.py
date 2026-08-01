from collections import Counter, OrderedDict
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template

from security import protect_blueprint
from services.db import DbError, attach_profiles, count_table, fetch_all, query_table

bp = Blueprint("dashboard", __name__)
protect_blueprint(bp)


def _parse_day(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def _day_series(rows, field, days=14):
    today = datetime.now(timezone.utc).date()
    buckets = OrderedDict(
        ((today - timedelta(days=offset)).isoformat(), 0) for offset in range(days - 1, -1, -1)
    )
    for row in rows:
        day = _parse_day(row.get(field))
        if day and day.isoformat() in buckets:
            buckets[day.isoformat()] += 1
    return buckets


@bp.route("/")
def index():
    stats = {}
    warning = None
    enrollments = []
    trial_requests = []
    upcoming_classes = []

    try:
        stats["total_users"] = count_table("profiles")
        stats["students"] = count_table("profiles", ilike_filters={"role": "student"})
        stats["teachers"] = count_table("profiles", ilike_filters={"role": "teacher"})
        stats["active_courses"] = count_table("courses", filters={"is_published": True})
        stats["draft_courses"] = count_table("courses", filters={"is_published": False})
        stats["enrollments"] = count_table("enrollments")
        stats["pending_payments"] = count_table("enrollments", filters={"payment_status": "pending"})
        stats["pending_posts"] = count_table("community_posts", filters={"is_approved": False})
        stats["pending_trials"] = count_table("free_trial_requests", filters={"status": "pending"})
        stats["live_classes"] = count_table("live_classes", filters={"status": "scheduled"})
        stats["unused_codes"] = count_table("teacher_access_codes", filters={"is_used": False})

        ai_rows = fetch_all("ai_usage", select="questions_used, quizzes_used, usage_date")
        stats["ai_interactions"] = sum(
            (r.get("questions_used") or 0) + (r.get("quizzes_used") or 0) for r in ai_rows
        )

        # Revenue: paid enrollments valued at their course's current price.
        paid = [
            r
            for r in fetch_all("enrollments", select="course_id, payment_status, enrolled_at")
            if (r.get("payment_status") or "").lower() in {"paid", "active"}
        ]
        prices = {c["id"]: (c.get("price") or 0) for c in fetch_all("courses", select="id, price")}
        stats["revenue"] = sum(prices.get(r.get("course_id"), 0) or 0 for r in paid)
        stats["paid_enrollments"] = len(paid)

        enrollments, _, _ = query_table(
            "enrollments",
            select="*, courses(id, title, price)",
            order_by="enrolled_at",
            limit=6,
        )
        attach_profiles(enrollments, "student_id", "student")

        trial_requests, _, _ = query_table(
            "free_trial_requests", filters={"status": "pending"}, limit=5
        )

        upcoming_classes, _, _ = query_table(
            "live_classes",
            select="*, courses(title)",
            order_by="start_time",
            desc=False,
            limit=5,
        )
    except DbError as exc:
        warning = str(exc)
        stats = {k: stats.get(k, 0) for k in
                 ["total_users", "students", "teachers", "active_courses", "draft_courses",
                  "enrollments", "pending_payments", "pending_posts", "pending_trials",
                  "live_classes", "unused_codes", "ai_interactions", "revenue",
                  "paid_enrollments"]}

    return render_template(
        "index.html",
        stats=stats,
        recent_enrollments=enrollments,
        trial_requests=trial_requests,
        upcoming_classes=upcoming_classes,
        warning=warning,
    )


@bp.route("/api/charts")
def charts():
    """Chart data for the dashboard, rendered client side by Chart.js."""
    try:
        enrollments = fetch_all("enrollments", select="enrolled_at")
        signups = fetch_all("profiles", select="created_at")
        ai_rows = fetch_all("ai_usage", select="usage_date, questions_used, quizzes_used")
        profiles = fetch_all("profiles", select="role, plan_type, status")
        courses = fetch_all("courses", select="category, is_published")
    except DbError as exc:
        return jsonify({"error": str(exc)}), 502

    enrollment_series = _day_series(enrollments, "enrolled_at", days=14)
    signup_series = _day_series(signups, "created_at", days=14)

    today = datetime.now(timezone.utc).date()
    ai_days = OrderedDict(
        ((today - timedelta(days=o)).isoformat(), {"questions": 0, "quizzes": 0})
        for o in range(6, -1, -1)
    )
    for row in ai_rows:
        day = _parse_day(row.get("usage_date"))
        key = day.isoformat() if day else None
        if key in ai_days:
            ai_days[key]["questions"] += row.get("questions_used") or 0
            ai_days[key]["quizzes"] += row.get("quizzes_used") or 0

    roles = Counter((p.get("role") or "unknown").strip().lower() for p in profiles)
    plans = Counter((p.get("plan_type") or "unknown").strip().lower() for p in profiles)
    categories = Counter((c.get("category") or "uncategorised") for c in courses)

    return jsonify(
        {
            "labels": list(enrollment_series.keys()),
            "enrollments": list(enrollment_series.values()),
            "signups": list(signup_series.values()),
            "ai": {
                "labels": list(ai_days.keys()),
                "questions": [v["questions"] for v in ai_days.values()],
                "quizzes": [v["quizzes"] for v in ai_days.values()],
            },
            "roles": {"labels": list(roles.keys()), "values": list(roles.values())},
            "plans": {"labels": list(plans.keys()), "values": list(plans.values())},
            "categories": {
                "labels": [c.replace("_", " ").title() for c in categories.keys()],
                "values": list(categories.values()),
            },
        }
    )
