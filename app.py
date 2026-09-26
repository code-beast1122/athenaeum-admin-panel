"""Athenaeum admin portal.

Run with:  python app.py       (or: flask --app app run)
"""

from datetime import datetime, timezone

import click
from flask import Flask, flash, redirect, render_template, request, url_for

from config import Config
from security import csrf_token, current_admin, is_authenticated
from services.access import access_state


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if Config.IS_SERVERLESS:
        # Static files are served by this function, so let browsers cache them
        # instead of paying for an invocation per stylesheet request.
        app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24

    from blueprints import auth, communications, courses, dashboard, enrollments
    from blueprints import exams, live_classes, reports, requests_bp, users
    from blueprints import community, payments, teachers

    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(users.bp)
    app.register_blueprint(teachers.bp)
    app.register_blueprint(courses.bp)
    app.register_blueprint(exams.bp)
    app.register_blueprint(enrollments.bp)
    app.register_blueprint(payments.bp)
    app.register_blueprint(live_classes.bp)
    app.register_blueprint(community.bp)
    app.register_blueprint(communications.bp)
    app.register_blueprint(requests_bp.bp)
    app.register_blueprint(reports.bp)

    @app.after_request
    def security_headers(response):
        # The portal is never embedded anywhere: block clickjacking and sniffing.
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if Config.IS_SERVERLESS:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        # Admin pages hold personal data: keep them out of shared caches.
        if not request.path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    register_template_helpers(app)
    register_error_handlers(app)
    register_cli(app)
    return app


def register_template_helpers(app):
    @app.context_processor
    def inject_globals():
        return {
            "csrf_token": csrf_token(),
            "admin": current_admin(),
            "is_authenticated": is_authenticated(),
            "require_login": Config.REQUIRE_LOGIN,
            "now": datetime.now(timezone.utc),
        }

    @app.template_filter("date")
    def format_date(value, fmt="%d %b %Y"):
        if not value:
            return "-"
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime(fmt)
        except ValueError:
            return str(value)[:10]

    @app.template_filter("datetime")
    def format_datetime(value, fmt="%d %b %Y, %H:%M"):
        return format_date(value, fmt)

    @app.template_filter("form_datetime")
    def form_datetime(value):
        """Render a timestamp for a `datetime-local` input."""
        if not value:
            return ""
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime(
                "%Y-%m-%dT%H:%M"
            )
        except ValueError:
            return ""

    @app.template_filter("money")
    def money(value):
        try:
            return f"Rs {float(value or 0):,.0f}"
        except (TypeError, ValueError):
            return "Rs 0"

    @app.template_filter("initials")
    def initials(value):
        parts = [p for p in str(value or "").split() if p]
        if not parts:
            return "?"
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()

    @app.template_filter("access")
    def access(row):
        """active / expired / pending for an enrollment row."""
        return access_state(row or {})

    @app.template_filter("pretty")
    def pretty(value):
        return str(value or "").replace("_", " ").title()


def register_error_handlers(app):
    @app.errorhandler(400)
    def bad_request(error):
        flash(getattr(error, "description", "Bad request."), "danger")
        return redirect(request.referrer or url_for("dashboard.index")), 302

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(500)
    def server_error(error):
        app.logger.exception("Unhandled error")
        return (
            render_template("error.html", code=500, message="Something went wrong."),
            500,
        )


def register_cli(app):
    @app.cli.command("promote-admin")
    @click.argument("email")
    def promote_admin(email):
        """Grant portal access to an existing Supabase user by email."""
        from services.auth_users import find_by_email
        from services.db import fetch_one, insert_row, update_row

        match = find_by_email(email)
        if not match:
            click.echo(f"No auth user found for {email}.")
            return
        if fetch_one("profiles", match.id):
            update_row("profiles", match.id, {"role": "admin"})
        else:
            insert_row("profiles", {"id": match.id, "full_name": email, "role": "admin"})
        click.echo(f"{email} ({match.id}) is now an admin.")

    @app.cli.command("list-admins")
    def list_admins():
        """Show every profile currently allowed into the portal."""
        from services.auth_users import email_map
        from services.db import fetch_all

        emails = email_map()
        for profile in fetch_all("profiles", select="id, full_name, role"):
            if (profile.get("role") or "").lower() == "admin":
                click.echo(f"{emails.get(profile['id'], '?'):40} {profile.get('full_name')}")


app = create_app()


if __name__ == "__main__":
    # Bound to localhost on purpose: this portal holds the service-role key.
    app.run(host="127.0.0.1", port=5000, debug=True)
