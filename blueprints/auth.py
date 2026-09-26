import hmac
import time

from flask import Blueprint, flash, redirect, render_template, request, url_for

from config import Config
from security import csrf_token, is_authenticated, login_admin, logout_admin, verify_csrf

bp = Blueprint("auth", __name__)

# Brute-force brake: after MAX_FAILURES wrong passwords from one address, refuse
# further attempts for LOCKOUT_SECONDS. Per instance (serverless instances do not
# share memory), which still makes guessing a strong password impractical.
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
_failures = {}


def _client_ip():
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() or request.remote_addr or "unknown")


def _locked_out(ip):
    count, since = _failures.get(ip, (0, 0.0))
    if count >= MAX_FAILURES and time.time() - since < LOCKOUT_SECONDS:
        return True
    if time.time() - since >= LOCKOUT_SECONDS:
        _failures.pop(ip, None)
    return False


def _record_failure(ip):
    count, since = _failures.get(ip, (0, time.time()))
    _failures[ip] = (count + 1, since if count else time.time())


def _safe_next(url):
    """Only same-site paths. Browsers treat '/\\evil.com' like '//evil.com'."""
    if not url or not url.startswith("/") or url.startswith("//") or "\\" in url:
        return None
    return url


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
    ip = _client_ip()
    if _locked_out(ip):
        flash("Too many failed attempts. Try again in 15 minutes.", "danger")
        return redirect(url_for("auth.login"))

    email = request.form.get("email") or Config.ADMIN_EMAIL
    password = request.form.get("password") or ""

    if not _credentials_match(email, password):
        _record_failure(ip)
        time.sleep(1)  # slows scripted guessing
        flash("Incorrect password.", "danger")
        return redirect(url_for("auth.login"))

    _failures.pop(ip, None)

    login_admin(Config.ADMIN_EMAIL)
    flash("Signed in.", "success")

    next_url = _safe_next(request.form.get("next") or request.args.get("next"))
    if next_url:
        return redirect(next_url)
    return redirect(url_for("dashboard.index"))


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    if request.method == "POST":
        verify_csrf()
    logout_admin()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))
