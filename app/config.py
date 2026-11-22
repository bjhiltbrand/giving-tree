"""Runtime configuration, read from environment variables (see .env.example)."""
import os
import secrets


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    # Public URL of the site as visitors see it (used to build links in emails).
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

    # Used to sign session cookies and claim tokens. MUST be set in production.
    SECRET_KEY: str = os.getenv("SECRET_KEY") or secrets.token_urlsafe(48)
    SECRET_KEY_IS_EPHEMERAL: bool = not bool(os.getenv("SECRET_KEY"))

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:////data/giving_tree.db")

    # Bootstrap admin account (created on first start if no admins exist).
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")
    # Address that receives "someone claimed an ornament" notifications (optional).
    ADMIN_NOTIFY_EMAIL: str = os.getenv("ADMIN_NOTIFY_EMAIL", "")

    # SMTP. If SMTP_HOST is empty, emails are written to the log instead of sent.
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = _int("SMTP_PORT", 587)
    SMTP_USERNAME: str = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    # "starttls" (port 587), "ssl" (port 465) or "none" (e.g. local relay / Mailpit)
    SMTP_SECURITY: str = os.getenv("SMTP_SECURITY", "starttls").lower()
    MAIL_FROM: str = os.getenv("MAIL_FROM", "Giving Tree <giving-tree@localhost>")
    MAIL_REPLY_TO: str = os.getenv("MAIL_REPLY_TO", "")

    # Set to true when the reverse proxy terminates TLS (marks cookies Secure).
    SECURE_COOKIES: bool = _bool("SECURE_COOKIES", BASE_URL.startswith("https://"))

    # How often the background job runs (minutes).
    SCHEDULER_INTERVAL_MINUTES: int = _int("SCHEDULER_INTERVAL_MINUTES", 15)

    # Basic abuse protection on the public claim form.
    CLAIMS_PER_IP_PER_HOUR: int = _int("CLAIMS_PER_IP_PER_HOUR", 30)

    TIMEZONE: str = os.getenv("TZ", "America/Chicago")


settings = Settings()
