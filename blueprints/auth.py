import hmac

from flask import Blueprint, flash, redirect, render_template, request, url_for

from config import Config
from security import csrf_token, is_authenticated, login_admin, logout_admin, verify_csrf

bp = Blueprint("auth", __name__)


def _credentials_match(email, password):
    """Compare against the configured operator credentials.

    Deliberately does not touch Supabase Auth: the portal is a private tool and
    signing into it must be independent of the accounts the student app uses.
    `compare_digest` keeps the check constant-time.
    """
    email_ok = hmac.compare_digest(email.strip().lower(), Config.ADMIN_EMAIL)
    password_ok = bool(Config.ADMIN_PASSWORD) and hmac.compare_digest(
        password, Config.ADMIN_PASSWORD
    )
    return email_ok and password_ok


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if is_authenticated():
            return redirect(url_for("dashboard.index"))
        return render_template(
            "login.html", csrf_token=csrf_token(), admin_email=Config.ADMIN_EMAIL
        )

    verify_csrf()
    email = request.form.get("email") or Config.ADMIN_EMAIL
    password = request.form.get("password") or ""

    if not _credentials_match(email, password):
        flash("Incorrect password.", "danger")
        return redirect(url_for("auth.login"))

    login_admin(Config.ADMIN_EMAIL)
    flash("Signed in.", "success")

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
