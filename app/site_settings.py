"""Admin-editable site settings stored in the database (key/value)."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from .config import settings as env
from .models import SiteSetting

DEFAULTS: dict[str, str] = {
    "site_title": "Our Giving Tree",
    "intro_text": (
        "This season we're helping a few families in our community. Each ornament on the tree "
        "is a gift a family has asked for. Pick an ornament, claim it with your email, and drop "
        "the gift off before the deadline. Thank you for sharing the joy!"
    ),
    "deadline": "",  # YYYY-MM-DD
    "dropoff_instructions": "Please drop off unwrapped gifts with the ornament number attached.",
    "contact_email": "",
    "admin_notify_email": env.ADMIN_NOTIFY_EMAIL,
    # Comma separated list of "days before the deadline" to send reminders.
    "reminder_days": "7,2",
    # Send a friendly check-in this many days after a claim (0 = off).
    "followup_days": "0",
    # If on, claims are held until the visitor clicks a link in their email.
    "require_confirmation": "0",
    "confirm_window_hours": "24",
    "max_claim_qty": "5",
    # "1" = group the public list by family (alias + description shown).
    # "0" = one combined list; family names/descriptions never appear publicly or in giver emails.
    "show_families": "1",
    "claims_open": "1",
    "closed_message": "Claiming is closed for this season. Thank you to everyone who gave!",
}


def get_all(db: Session) -> dict[str, str]:
    values = dict(DEFAULTS)
    for row in db.query(SiteSetting).all():
        values[row.key] = row.value
    return values


def save(db: Session, updates: dict[str, str]) -> None:
    for key, value in updates.items():
        if key not in DEFAULTS:
            continue
        row = db.get(SiteSetting, key)
        if row is None:
            db.add(SiteSetting(key=key, value=value))
        else:
            row.value = value
    db.commit()


def as_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def as_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def show_families(values: dict[str, str]) -> bool:
    return as_bool(values.get("show_families", "1"))


def deadline(values: dict[str, str]) -> date | None:
    raw = (values.get("deadline") or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def reminder_days(values: dict[str, str]) -> list[int]:
    days = set()
    for part in (values.get("reminder_days") or "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            days.add(int(part))
    return sorted(days, reverse=True)
