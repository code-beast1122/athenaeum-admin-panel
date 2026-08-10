from flask import Blueprint, flash, redirect, render_template, request, url_for

from extensions import supabase
from security import protect_blueprint
from services import forms
from services.auth_users import email_map
from services.db import (
    DbError,
    delete_row,
    delete_where,
    fetch_all,
    fetch_one,
    insert_row,
    profiles_map,
    query_table,
    update_row,
)
from services.progress import student_progress

bp = Blueprint("users", __name__, url_prefix="/users")
protect_blueprint(bp)

ROLES = ["student", "teacher", "admin", "parent"]
PLANS = ["trial", "paid", "free", "expired"]
STATUSES = ["active", "blocked", "suspended", "inactive"]
# profiles.plan_type describes the account; enrollments.payment_status is per course
PAYMENT_STATUSES = ["free", "paid", "pending", "active", "trial"]


def _auth_emails():
    return email_map()


@bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    role = request.args.get("role", "all")
    status = request.args.get("status", "all")
    plan = request.args.get("plan", "all")

    try:
        rows, total, pages = query_table(
            "profiles",
            search=search,
            search_fields=("full_name", "phone"),
            ilike_filters={"role": role},
            filters={"status": status, "plan_type": plan},
            order_by="created_at",
            page=page,
        )
        emails = _auth_emails()
        for row in rows:
            row["email"] = emails.get(row["id"], "")

        # Each enrollment carries its own payment status, so the list needs them
        # to offer per-course plan changes without opening every profile.
        by_student = {}
        if rows:
            enrolled = (
                supabase.table("enrollments")
                .select("*, courses(id, title)")
                .in_("student_id", [r["id"] for r in rows])
                .execute()
                .data
                or []
            )
            for item in enrolled:
                by_student.setdefault(item.get("student_id"), []).append(item)
        for row in rows:
            row["enrollments"] = sorted(
                by_student.get(row["id"], []),
                key=lambda e: ((e.get("courses") or {}).get("title") or ""),
            )
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages = [], 0, 1

    return render_template(
        "users.html",
        users=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        role=role,
        status=status,
        plan=plan,
        roles=ROLES,
        plans=PLANS,
        statuses=STATUSES,
        payment_statuses=PAYMENT_STATUSES,
    )


@bp.route("/<user_id>")
def detail(user_id):
    try:
        user = fetch_one("profiles", user_id)
        if not user:
            flash("User not found.", "danger")
            return redirect(url_for("users.index"))

        user["email"] = _auth_emails().get(user_id, "")

        enrollments, _, _ = query_table(
            "enrollments",
            select="*, courses(id, title, price)",
            filters={"student_id": user_id},
            order_by="enrolled_at",
            limit=100,
        )
        results, _, _ = query_table(
            "exam_results",
            select="*, exams(title)",
            filters={"student_id": user_id},
            order_by="completed_at",
            limit=50,
        )
        ai_rows = fetch_all(
            "ai_usage", filters={"user_id": user_id}, order_by="usage_date", desc=True
        )
        notifications, _, _ = query_table(
            "notifications", filters={"user_id": user_id}, order_by="created_at", limit=20
        )

        progress = student_progress(user_id, [e.get("course_id") for e in enrollments])

        # Two mechanisms exist for family links: profiles.parent_id and the
        # parent_child_links table. Show both so neither hides a relationship.
        children = fetch_all("profiles", filters={"parent_id": user_id})
        links = fetch_all("parent_child_links", filters={"parent_id": user_id})
        parent_of_links = fetch_all("parent_child_links", filters={"student_id": user_id})
        linked_ids = [l.get("student_id") for l in links] + [
            l.get("parent_id") for l in parent_of_links
        ]
        link_profiles = profiles_map(linked_ids)
        for link in links:
            link["student"] = link_profiles.get(link.get("student_id")) or {}
        for link in parent_of_links:
            link["parent"] = link_profiles.get(link.get("parent_id")) or {}

        parent = fetch_one("profiles", user["parent_id"]) if user.get("parent_id") else None
        cart, _, _ = query_table(
            "cart_items",
            select="*, courses(id, title, price)",
            filters={"user_id": user_id},
            order_by="added_at",
            limit=50,
        )
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("users.index"))

    return render_template(
        "user_detail.html",
        user=user,
        enrollments=enrollments,
        results=results,
        ai_rows=ai_rows,
        ai_total=sum((r.get("questions_used") or 0) + (r.get("quizzes_used") or 0) for r in ai_rows),
        notifications=notifications,
        children=children,
        links=links,
        parent_of_links=parent_of_links,
        parent=parent,
        progress=progress,
        cart=cart,
        roles=ROLES,
        plans=PLANS,
        statuses=STATUSES,
        payment_statuses=PAYMENT_STATUSES,
        courses=fetch_all("courses", select="id, title", order_by="title"),
        students=[
            p
            for p in fetch_all("profiles", select="id, full_name, role", order_by="full_name")
            if (p.get("role") or "").lower() == "student" and p["id"] != user_id
        ],
    )


@bp.route("/create", methods=["POST"])
def create():
    email = forms.text(request.form, "email")
    password = forms.text(request.form, "password")
    if not email or not password:
        flash("Email and password are required to create a user.", "danger")
        return redirect(url_for("users.index"))

    try:
        created = supabase.auth.admin.create_user(
            {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"full_name": forms.text(request.form, "full_name") or email},
            }
        )
        user_id = created.user.id
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not create the auth user: {exc}", "danger")
        return redirect(url_for("users.index"))

    profile = {
        "id": user_id,
        "full_name": forms.text(request.form, "full_name") or email.split("@")[0],
        "role": forms.text(request.form, "role", "student"),
        "plan_type": forms.text(request.form, "plan_type", "trial"),
        "status": forms.text(request.form, "status", "active"),
        "phone": forms.text(request.form, "phone"),
    }
    try:
        # A signup trigger may have created the profile row already.
        if fetch_one("profiles", user_id):
            update_row("profiles", user_id, forms.compact(profile))
        else:
            insert_row("profiles", forms.compact(profile))
        flash(f"User {email} created.", "success")
    except DbError as exc:
        flash(f"Auth user created but the profile failed: {exc}", "warning")

    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/update", methods=["POST"])
def update(user_id):
    payload = {
        "full_name": forms.text(request.form, "full_name"),
        "role": forms.text(request.form, "role"),
        "plan_type": forms.text(request.form, "plan_type"),
        "status": forms.text(request.form, "status"),
        "phone": forms.text(request.form, "phone", allow_empty=True),
        "avatar_url": forms.text(request.form, "avatar_url", allow_empty=True),
        "xp": forms.integer(request.form, "xp"),
        "streak_days": forms.integer(request.form, "streak_days"),
        "parent_id": forms.uuid_or_none(request.form, "parent_id"),
        "updated_at": forms.now_iso(),
    }
    try:
        update_row("profiles", user_id, {k: v for k, v in payload.items() if v is not None})
        flash("User updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/plan", methods=["POST"])
def set_plan(user_id):
    """Change the profile-wide plan only.

    This is separate from a course's `payment_status`: the plan describes the
    account, each enrollment is paid for on its own.
    """
    plan = forms.text(request.form, "plan_type", "trial")
    try:
        update_row(
            "profiles", user_id, {"plan_type": plan, "updated_at": forms.now_iso()}
        )
        flash(f"Global plan set to {plan}.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("users.index"))


@bp.route("/<user_id>/enrollments/<enrollment_id>/plan", methods=["POST"])
def set_enrollment_plan(user_id, enrollment_id):
    """Change one course's payment status for this student."""
    status = forms.text(request.form, "payment_status", "free")
    try:
        update_row("enrollments", enrollment_id, {"payment_status": status})
        flash(f"Course plan set to {status}.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/status", methods=["POST"])
def set_status(user_id):
    status = forms.text(request.form, "status", "active")
    try:
        update_row("profiles", user_id, {"status": status, "updated_at": forms.now_iso()})
        # Mirror the block on the auth side so the account cannot sign in.
        if status in {"blocked", "suspended"}:
            supabase.auth.admin.update_user_by_id(user_id, {"ban_duration": "876000h"})
        else:
            supabase.auth.admin.update_user_by_id(user_id, {"ban_duration": "none"})
        flash(f"User marked as {status}.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    except Exception as exc:  # noqa: BLE001
        flash(f"Profile updated, but the auth ban could not be applied: {exc}", "warning")
    return redirect(request.referrer or url_for("users.index"))


@bp.route("/<user_id>/password", methods=["POST"])
def reset_password(user_id):
    password = forms.text(request.form, "password")
    if not password or len(password) < 6:
        flash("Password must be at least 6 characters.", "danger")
        return redirect(url_for("users.detail", user_id=user_id))
    try:
        supabase.auth.admin.update_user_by_id(user_id, {"password": password})
        flash("Password updated.", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"Could not update the password: {exc}", "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/xp", methods=["POST"])
def award_xp(user_id):
    amount = forms.integer(request.form, "amount", 0) or 0
    try:
        user = fetch_one("profiles", user_id, select="xp")
        update_row(
            "profiles",
            user_id,
            {"xp": max(0, (user.get("xp") or 0) + amount), "updated_at": forms.now_iso()},
        )
        flash(f"{amount:+d} XP applied.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/enroll", methods=["POST"])
def enroll(user_id):
    course_id = forms.uuid_or_none(request.form, "course_id")
    if not course_id:
        flash("Pick a course to enrol the student in.", "danger")
        return redirect(url_for("users.detail", user_id=user_id))
    try:
        insert_row(
            "enrollments",
            {
                "student_id": user_id,
                "course_id": course_id,
                "payment_status": forms.text(request.form, "payment_status", "free"),
            },
        )
        flash("Student enrolled.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/notify", methods=["POST"])
def notify(user_id):
    try:
        insert_row(
            "notifications",
            {
                "user_id": user_id,
                "title": forms.text(request.form, "title", "Message from Athenaeum"),
                "message": forms.text(request.form, "message", ""),
                "type": forms.text(request.form, "type", "info"),
            },
        )
        flash("Notification sent.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/progress/reset", methods=["POST"])
def reset_progress(user_id):
    """Clear lesson completions so the student can work through a course again."""
    course_id = forms.uuid_or_none(request.form, "course_id")
    try:
        rows = fetch_all("user_progress", select="id, lesson_id", filters={"student_id": user_id})
        if course_id:
            from services.progress import lesson_index

            lesson_course, _, _ = lesson_index()
            rows = [r for r in rows if lesson_course.get(r.get("lesson_id")) == course_id]
        for row in rows:
            delete_row("user_progress", row["id"])
        flash(f"{len(rows)} lesson completion(s) cleared.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/progress/complete", methods=["POST"])
def complete_course(user_id):
    """Mark every lesson in a course complete - for students blocked by a glitch."""
    course_id = forms.uuid_or_none(request.form, "course_id")
    if not course_id:
        flash("Pick a course.", "danger")
        return redirect(url_for("users.detail", user_id=user_id))
    try:
        from services.progress import lesson_index

        _, course_lessons, _ = lesson_index()
        lesson_ids = course_lessons.get(course_id, [])
        existing = {
            r["lesson_id"]: r
            for r in fetch_all("user_progress", select="*", filters={"student_id": user_id})
        }
        touched = 0
        for lesson_id in lesson_ids:
            row = existing.get(lesson_id)
            if row and row.get("completed"):
                continue
            if row:
                update_row(
                    "user_progress",
                    row["id"],
                    {"completed": True, "completed_at": forms.now_iso()},
                )
            else:
                insert_row(
                    "user_progress",
                    {
                        "student_id": user_id,
                        "lesson_id": lesson_id,
                        "completed": True,
                        "completed_at": forms.now_iso(),
                    },
                )
            touched += 1
        if not lesson_ids:
            flash("That course has no lessons yet.", "warning")
        else:
            flash(f"{touched} lesson(s) marked complete.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/cart/add", methods=["POST"])
def add_cart_item(user_id):
    course_id = forms.uuid_or_none(request.form, "course_id")
    if not course_id:
        flash("Pick a course.", "danger")
        return redirect(url_for("users.detail", user_id=user_id))
    try:
        existing = fetch_all(
            "cart_items", select="id", filters={"user_id": user_id, "course_id": course_id}
        )
        if existing:
            flash("That course is already in the cart.", "warning")
        else:
            insert_row("cart_items", {"user_id": user_id, "course_id": course_id})
            flash("Course added to the cart.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/family/link", methods=["POST"])
def link_child(user_id):
    """Link a student to this parent, writing both link mechanisms."""
    student_id = forms.uuid_or_none(request.form, "student_id")
    if not student_id:
        flash("Pick a student to link.", "danger")
        return redirect(url_for("users.detail", user_id=user_id))
    if student_id == user_id:
        flash("An account cannot be its own parent.", "danger")
        return redirect(url_for("users.detail", user_id=user_id))
    try:
        existing = fetch_all("parent_child_links", filters={"parent_id": user_id})
        if any(l.get("student_id") == student_id for l in existing):
            flash("That student is already linked.", "warning")
        else:
            insert_row(
                "parent_child_links",
                {
                    "parent_id": user_id,
                    "student_id": student_id,
                    "is_verified": forms.boolean(request.form, "is_verified"),
                },
            )
        update_row("profiles", student_id, {"parent_id": user_id, "updated_at": forms.now_iso()})
        flash("Student linked to this parent.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/family/<link_id>/verify", methods=["POST"])
def verify_link(user_id, link_id):
    verified = forms.boolean(request.form, "is_verified")
    try:
        update_row("parent_child_links", link_id, {"is_verified": verified})
        flash("Link verified." if verified else "Link marked unverified.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/family/<link_id>/unlink", methods=["POST"])
def unlink_child(user_id, link_id):
    try:
        link = fetch_one("parent_child_links", link_id)
        delete_row("parent_child_links", link_id)
        if link and link.get("student_id"):
            child = fetch_one("profiles", link["student_id"], select="parent_id")
            if child and child.get("parent_id") == link.get("parent_id"):
                update_row("profiles", link["student_id"], {"parent_id": None})
        flash("Link removed.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/ai-usage", methods=["POST"])
def set_ai_usage(user_id):
    """Adjust or clear today's AI quota counters."""
    row_id = forms.text(request.form, "row_id")
    try:
        if forms.boolean(request.form, "clear"):
            if row_id:
                delete_row("ai_usage", row_id)
            else:
                delete_where("ai_usage", "user_id", user_id)
            flash("AI usage cleared - the quota resets immediately.", "success")
        else:
            payload = {
                "questions_used": forms.integer(request.form, "questions_used", 0),
                "quizzes_used": forms.integer(request.form, "quizzes_used", 0),
                "updated_at": forms.now_iso(),
            }
            if row_id:
                update_row("ai_usage", row_id, payload)
            else:
                payload["user_id"] = user_id
                insert_row("ai_usage", payload)
            flash("AI usage updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


@bp.route("/<user_id>/cart/<item_id>/delete", methods=["POST"])
def delete_cart_item(user_id, item_id):
    try:
        delete_row("cart_items", item_id)
        flash("Cart item removed.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.detail", user_id=user_id))


# Rows belonging to the user, removed with them.
OWNED_ROWS = (
    ("enrollments", "student_id"),
    ("exam_results", "student_id"),
    ("user_progress", "student_id"),
    ("notifications", "user_id"),
    ("ai_usage", "user_id"),
    ("cart_items", "user_id"),
    ("post_likes", "user_id"),
    ("post_comments", "user_id"),
    ("community_posts", "user_id"),
    ("free_trial_requests", "user_id"),
    ("parent_child_links", "parent_id"),
    ("parent_child_links", "student_id"),
)

# Content that outlives the user: the reference is cleared, the row stays.
DETACHED_ROWS = (
    ("courses", "instructor_id", {"instructor_id": None}),
    ("exams", "teacher_id", {"teacher_id": None}),
    ("live_classes", "teacher_id", {"teacher_id": None}),
    ("course_materials", "teacher_id", {"teacher_id": None}),
    ("teacher_access_codes", "used_by", {"used_by": None, "is_used": False, "used_at": None}),
    ("profiles", "parent_id", {"parent_id": None}),
)


@bp.route("/<user_id>/delete", methods=["POST"])
def delete(user_id):
    """Delete a user.

    `profiles.id` is a foreign key onto `auth.users`, and half a dozen tables
    point back at the user, so everything referencing them has to be cleared
    first - otherwise Postgres rejects the delete and the account survives.
    Courses, exams and live classes are kept and simply detached.
    """
    detached = []
    try:
        for table_name, field, payload in DETACHED_ROWS:
            try:
                resp = supabase.table(table_name).update(payload).eq(field, user_id).execute()
                if resp.data:
                    detached.append(f"{len(resp.data)} {table_name.replace('_', ' ')}")
            except Exception:  # noqa: BLE001 - table may not exist in this project
                pass

        for table_name, field in OWNED_ROWS:
            try:
                supabase.table(table_name).delete().eq(field, user_id).execute()
            except Exception:  # noqa: BLE001
                pass

        delete_row("profiles", user_id)
        supabase.auth.admin.delete_user(user_id)
        message = "User deleted."
        if detached:
            message += " Kept and unassigned: " + ", ".join(detached) + "."
        flash(message, "success")
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("users.detail", user_id=user_id))
    except Exception as exc:  # noqa: BLE001
        flash(f"Profile removed but the auth user remains: {exc}", "warning")
    return redirect(url_for("users.index"))
