"""Session auth + CSRF for the admin portal.

The app talks to Supabase with the service-role key, which bypasses row level
security entirely, so every route outside the login screen must be gated.
"""

import hmac
import secrets
from functools import wraps

from flask import (
    abort,
    flash,
    redirect,
    request,
    session,
    url_for,
)

from config import Config

SESSION_KEY = "admin_user"
CSRF_KEY = "_csrf_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Stand-in identity used when REQUIRE_LOGIN is off (the local single-admin case).
LOCAL_ADMIN = {
    "id": None,
    "email": "",
    "name": "Admin",
    "role": "admin",
    "avatar_url": None,
}


def login_admin(profile, email):
    session.permanent = True
    session[SESSION_KEY] = {
        "id": profile.get("id"),
        "email": email,
        "name": profile.get("full_name") or email.split("@")[0],
        "role": profile.get("role") or "admin",
        "avatar_url": profile.get("avatar_url"),
    }


def logout_admin():
    session.pop(SESSION_KEY, None)
    session.pop(CSRF_KEY, None)


def current_admin():
    return session.get(SESSION_KEY) or (None if Config.REQUIRE_LOGIN else LOCAL_ADMIN)


def is_authenticated():
    if not Config.REQUIRE_LOGIN:
        return True
    return bool(session.get(SESSION_KEY))


def csrf_token():
    token = session.get(CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_KEY] = token
    return token


def verify_csrf():
    if request.method in SAFE_METHODS:
        return
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
    expected = session.get(CSRF_KEY) or ""
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="Invalid or expired CSRF token. Please retry.")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_authenticated():
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.full_path))
        return view(*args, **kwargs)

    return wrapped


def protect_blueprint(bp):
    """Require a session and a valid CSRF token for every route on `bp`."""

    @bp.before_request
    def _guard():
        if not is_authenticated():
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("auth.login", next=request.full_path))
        verify_csrf()
        return None

    return bp
