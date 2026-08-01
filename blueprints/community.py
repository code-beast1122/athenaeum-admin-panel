from flask import Blueprint, flash, redirect, render_template, request, url_for

from security import protect_blueprint
from services import forms
from services.actor import acting_admin
from services.db import (
    DbError,
    delete_row,
    delete_where,
    fetch_all,
    fetch_one,
    insert_row,
    query_table,
    update_row,
)

bp = Blueprint("community", __name__, url_prefix="/community")
protect_blueprint(bp)

CATEGORIES = [
    "general",
    "class_notes",
    "announcement",
    "past_paper",
    "motivational",
    "doubt",
    "result",
    "exam_schedule",
    "academy_update",
]


@bp.route("/")
def index():
    page = request.args.get("page", 1, type=int)
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "all")
    approved = request.args.get("approved", "all")

    filters = {"category": category}
    if approved in {"true", "false"}:
        filters["is_approved"] = approved == "true"

    try:
        rows, total, pages = query_table(
            "community_posts",
            search=search,
            search_fields=("content", "author_name"),
            filters=filters,
            order_by="created_at",
            page=page,
        )
        pending = query_table(
            "community_posts", filters={"is_approved": False}, limit=1
        )[1]
    except DbError as exc:
        flash(str(exc), "danger")
        rows, total, pages, pending = [], 0, 1, 0

    return render_template(
        "community.html",
        posts=rows,
        total=total,
        pages=pages,
        page=page,
        search=search,
        category=category,
        approved=approved,
        pending=pending,
        categories=CATEGORIES,
    )


@bp.route("/<post_id>")
def detail(post_id):
    try:
        post = fetch_one("community_posts", post_id)
        if not post:
            flash("Post not found.", "danger")
            return redirect(url_for("community.index"))
        comments = fetch_all(
            "post_comments", filters={"post_id": post_id}, order_by="created_at"
        )
        likes = fetch_all("post_likes", select="id", filters={"post_id": post_id})
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("community.index"))

    return render_template(
        "post_detail.html",
        post=post,
        comments=comments,
        likes=len(likes),
        categories=CATEGORIES,
    )


@bp.route("/create", methods=["POST"])
def create():
    content = forms.text(request.form, "content")
    if not content:
        flash("Post content is required.", "danger")
        return redirect(url_for("community.index"))

    author_id, author_name, author_avatar = acting_admin()
    if not author_id:
        flash(
            "No admin profile exists to post as. Run "
            "`flask --app app promote-admin <email>` first.",
            "danger",
        )
        return redirect(url_for("community.index"))
    try:
        insert_row(
            "community_posts",
            {
                "user_id": author_id,
                "author_name": author_name,
                "author_role": "admin",
                "author_avatar": author_avatar,
                "content": content,
                "image_url": forms.text(request.form, "image_url", allow_empty=True),
                "category": forms.text(request.form, "category", "announcement"),
                "is_approved": True,
                "is_pinned": forms.boolean(request.form, "is_pinned"),
            },
        )
        flash("Post published.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("community.index"))


@bp.route("/<post_id>/update", methods=["POST"])
def update(post_id):
    try:
        update_row(
            "community_posts",
            post_id,
            {
                "content": forms.text(request.form, "content"),
                "category": forms.text(request.form, "category"),
                "image_url": forms.text(request.form, "image_url", allow_empty=True),
                "is_pinned": forms.boolean(request.form, "is_pinned"),
                "updated_at": forms.now_iso(),
            },
        )
        flash("Post updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("community.detail", post_id=post_id))


@bp.route("/<post_id>/approve", methods=["POST"])
def approve(post_id):
    approved = forms.boolean(request.form, "is_approved")
    try:
        update_row(
            "community_posts",
            post_id,
            {"is_approved": approved, "updated_at": forms.now_iso()},
        )
        flash("Post approved." if approved else "Post hidden from the feed.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("community.index"))


@bp.route("/<post_id>/pin", methods=["POST"])
def pin(post_id):
    pinned = forms.boolean(request.form, "is_pinned")
    try:
        update_row("community_posts", post_id, {"is_pinned": pinned})
        flash("Post pinned." if pinned else "Post unpinned.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(request.referrer or url_for("community.index"))


@bp.route("/<post_id>/delete", methods=["POST"])
def delete(post_id):
    try:
        delete_where("post_comments", "post_id", post_id)
        delete_where("post_likes", "post_id", post_id)
        delete_row("community_posts", post_id)
        flash("Post deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("community.detail", post_id=post_id))
    return redirect(url_for("community.index"))


@bp.route("/<post_id>/comments", methods=["POST"])
def create_comment(post_id):
    """Reply to a post as the academy."""
    content = forms.text(request.form, "content")
    if not content:
        flash("Write something first.", "danger")
        return redirect(url_for("community.detail", post_id=post_id))

    author_id, author_name, _ = acting_admin()
    if not author_id:
        flash(
            "No admin profile exists to comment as. Run "
            "`flask --app app promote-admin <email>` first.",
            "danger",
        )
        return redirect(url_for("community.detail", post_id=post_id))
    try:
        insert_row(
            "post_comments",
            {
                "post_id": post_id,
                "user_id": author_id,
                "author_name": author_name,
                "author_role": "admin",
                "content": content,
            },
        )
        # `community_posts.comments_count` is maintained by a database trigger -
        # incrementing it here as well would double-count.
        flash("Reply posted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("community.detail", post_id=post_id))


@bp.route("/<post_id>/likes/clear", methods=["POST"])
def clear_likes(post_id):
    """Wipe likes on a post (used when a like count has been gamed)."""
    try:
        delete_where("post_likes", "post_id", post_id)
        # The trigger decrements per row; this also repairs a drifted counter.
        update_row("community_posts", post_id, {"likes_count": 0})
        flash("Likes cleared.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("community.detail", post_id=post_id))


@bp.route("/<post_id>/comments/<comment_id>/update", methods=["POST"])
def update_comment(post_id, comment_id):
    """Edit a comment - normally your own reply, or to redact something."""
    content = forms.text(request.form, "content")
    if not content:
        flash("A comment cannot be empty - delete it instead.", "danger")
        return redirect(url_for("community.detail", post_id=post_id))
    try:
        update_row("post_comments", comment_id, {"content": content})
        flash("Comment updated.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("community.detail", post_id=post_id))


@bp.route("/<post_id>/comments/<comment_id>/delete", methods=["POST"])
def delete_comment(post_id, comment_id):
    try:
        delete_row("post_comments", comment_id)  # trigger decrements comments_count
        flash("Comment deleted.", "success")
    except DbError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("community.detail", post_id=post_id))
