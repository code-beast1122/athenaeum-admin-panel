import httpx
from flask import Blueprint, flash, redirect, render_template, request, url_for

from config import Config
from services.db import fetch_one
from security import csrf_token, is_authenticated, login_admin, logout_admin, verify_csrf

bp = Blueprint("auth", __name__)


def _password_grant(email, password):
    """Verify credentials against GoTrue directly.

    Calling `supabase.auth.sign_in_with_password()` on the shared client would
    swap its Authorization header for the signed-in user's JWT and silently
    downgrade every later query from service-role to that user's RLS scope.
    """
    url = f"{Config.SUPABASE_URL.rstrip('/')}/auth/v1/token?grant_type=password"
    headers = {
        "apikey": Config.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {Config.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(
            url,
            headers=headers,
            json={"email": email, "password": password},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return None, f"Could not reach the authentication server ({exc.__class__.__name__})."

    if resp.status_code != 200:
        try:
            body = resp.json()
            message = body.get("error_description") or body.get("msg") or body.get("message")
        except ValueError:
            message = None
        return None, message or "Invalid email or password."

    return resp.json(), None


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if is_authenticated():
            return redirect(url_for("dashboard.index"))
        return render_template("login.html", csrf_token=csrf_token())

    verify_csrf()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""

    if not email or not password:
        flash("Email and password are required.", "danger")
        return redirect(url_for("auth.login"))

    token, error = _password_grant(email, password)
    if error:
        flash(error, "danger")
        return redirect(url_for("auth.login"))

    user = token.get("user") or {}
    user_id = user.get("id")
    profile = fetch_one("profiles", user_id) if user_id else None
    role = (profile or {}).get("role") or ""

    if role.lower() != "admin" and email not in Config.ADMIN_EMAILS:
        flash("This account does not have administrator access.", "danger")
        return redirect(url_for("auth.login"))

    login_admin(profile or {"id": user_id, "full_name": email, "role": "admin"}, email)
    flash("Signed in successfully.", "success")

    next_url = request.form.get("next") or request.args.get("next")
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(url_for("dashboard.index"))


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    if request.method == "POST":
        verify_csrf()
    logout_admin()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))
