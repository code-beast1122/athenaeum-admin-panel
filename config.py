import os

from dotenv import load_dotenv

load_dotenv()


def _env(*names, default=None):
    """Read the first environment variable that is set among `names`."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return default


class Config:
    SECRET_KEY = _env("SECRET_KEY", default="dev-only-change-me")

    SUPABASE_URL = _env("SUPABASE_URL", "BASE_URL")
    SUPABASE_SERVICE_KEY = _env("SUPABASE_SERVICE_KEY", "SERVICE_KEY")

    # The portal has exactly one operator. Credentials live here, not in
    # Supabase Auth - signing in must not touch the accounts the student app
    # uses.
    ADMIN_EMAIL = (_env("ADMIN_EMAIL", default="athenaeum.institute@gmail.com") or "").lower()
    ADMIN_PASSWORD = _env("ADMIN_PASSWORD", default="")

    # Vercel sets VERCEL=1 in every deployment.
    IS_SERVERLESS = bool(_env("VERCEL", "VERCEL_ENV", "AWS_LAMBDA_FUNCTION_NAME"))

    # Locally this is a single-admin tool, so the login screen is off by
    # default. A deployment has a public URL, so it signs in as usual.
    REQUIRE_LOGIN = IS_SERVERLESS or (
        _env("REQUIRE_LOGIN", default="false") or ""
    ).lower() in {"1", "true", "yes", "on"}

    PER_PAGE = int(_env("PER_PAGE", default="25"))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = IS_SERVERLESS  # HTTPS-only cookie once deployed
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12  # 12 hours

    @classmethod
    def validate(cls):
        missing = []
        if not cls.SUPABASE_URL:
            missing.append("SUPABASE_URL (or BASE_URL)")
        if not cls.SUPABASE_SERVICE_KEY:
            missing.append("SUPABASE_SERVICE_KEY")
        if missing:
            raise RuntimeError(
                "Missing required environment variables: " + ", ".join(missing)
            )
        # Each serverless instance signs cookies with this key, so without a
        # shared value logins stop working the moment a second instance spins
        # up. Fail loudly at boot rather than mysteriously at sign-in.
        if cls.IS_SERVERLESS and cls.SECRET_KEY == "dev-only-change-me":
            raise RuntimeError(
                "SECRET_KEY must be set in the deployment environment or "
                "sessions will not persist. Generate one with: "
                'python -c "import secrets; print(secrets.token_hex(32))"'
            )
        # An empty password would let anyone through the login form.
        if cls.REQUIRE_LOGIN and not cls.ADMIN_PASSWORD:
            raise RuntimeError(
                "ADMIN_PASSWORD must be set when the login screen is enabled."
            )
