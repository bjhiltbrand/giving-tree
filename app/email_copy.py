"""Editable wording for the emails sent to givers.

Each email has three editable parts: the subject, an opening message (above the gift details) and a
closing message (below the buttons). The layout, gift details, drop-off info and buttons stay fixed so
an edit can't break an email. Text may contain {placeholders}; unknown ones are left as typed.

Placeholders are filled with plain string substitution (never evaluated as a template), so anything an
admin types is shown as text.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

from markupsafe import Markup, escape

from .templating import fmt_date

PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

COMMON = {
    "name": "Giver's name (or “there” if blank)",
    "site_title": "Your site title",
    "deadline": "Drop-off deadline, e.g. “Thursday, December 10, 2026” (blank if none set)",
    "contact_email": "Public contact email from Settings",
}
ITEM = {
    "item": "The gift's name, e.g. “Winter coat”",
    "quantity": "How many they claimed",
    "family": "The family's public name (or “the family” when families are hidden)",
}

DEFAULTS: dict[str, dict] = {
    "claim_confirmation": {
        "label": "Claim confirmation",
        "when": "Sent right after someone claims an ornament (or once they click the confirmation link, "
                "if confirmation is required). Includes the gift details, drop-off info, a button to view "
                "their ornaments and a button to release this one.",
        "placeholders": {**COMMON, **ITEM},
        "subject": "You claimed “{item}” — thank you!",
        "intro": "Hi {name},\n\nThank you for claiming an ornament from {site_title}! Here's what you picked:",
        "outro": "If plans change and you can't give this gift, no worries — use the release button so "
                 "someone else can pick it up.\n\nNo password needed: just keep this email.",
    },
    "claim_verify": {
        "label": "Please confirm your claim",
        "when": "Only when “Require email confirmation” is on. Sent right after claiming, with the "
                "button that locks the claim in.",
        "placeholders": {**COMMON, **ITEM, "hours": "How many hours they have to confirm"},
        "subject": "Please confirm your ornament: “{item}”",
        "intro": "Hi {name},\n\nWe're holding this ornament for you. Please confirm within {hours} hours to "
                 "lock it in:",
        "outro": "If you don't confirm, the ornament goes back on the tree for someone else. If you didn't "
                 "claim this, just ignore this email.",
    },
    "reminder": {
        "label": "Reminder",
        "when": "Sent on the reminder schedule in Settings to anyone whose gift isn't dropped off yet. "
                "Several ornaments for the same person are bundled into one email.",
        "placeholders": {**COMMON,
                         "deadline_phrase": "“today”, “tomorrow”, “in 5 days”, "
                                            "or “coming up” when no deadline is set",
                         "count": "How many ornaments are listed"},
        "subject": "Friendly reminder: gift drop-off is {deadline_phrase}",
        "intro": "Hi {name},\n\nJust a friendly reminder about what you claimed — gift drop-off is "
                 "{deadline_phrase}.",
        "outro": "Already dropped it off? Let us know with the button above. If plans changed, no worries "
                 "— release it so someone else can pick it up.",
    },
    "fulfilled": {
        "label": "Gift received (thank you)",
        "when": "Sent when an organizer marks a gift as received.",
        "placeholders": {**COMMON, **ITEM},
        "subject": "We received your gift — thank you! \U0001F381",
        "intro": "Hi {name},\n\nYour gift has been received and will be on its way to {family}. \U0001F389",
        "outro": "Thank you for making this season brighter for a family in our community.",
    },
    "released": {
        "label": "Claim released by an organizer",
        "when": "Sent when an organizer releases someone's claim. The reason the organizer typed is shown "
                "below the gift.",
        "placeholders": {**COMMON, **ITEM, "reason": "The reason the organizer typed (may be blank)"},
        "subject": "Update on your ornament “{item}”",
        "intro": "Hi {name},\n\nThis ornament has been released and is no longer assigned to you:",
        "outro": "Thank you for your generosity. You're welcome to pick another ornament from the tree.",
    },
    "hold_expired": {
        "label": "Hold expired",
        "when": "Only when “Require email confirmation” is on. Sent when someone doesn't confirm "
                "in time and the ornament goes back on the tree.",
        "placeholders": {**COMMON, **ITEM},
        "subject": "Your ornament hold expired",
        "intro": "Hi {name},\n\nWe didn't get a confirmation in time, so this ornament went back on the tree:",
        "outro": "You're welcome to pick another ornament from the tree.",
    },
    "manage_link": {
        "label": "“My ornaments” link",
        "when": "Only when “My ornaments” access is set to “Emailed private link”. Sent "
                "when someone asks for their link.",
        "placeholders": COMMON,
        "subject": "Your private link for {site_title}",
        "intro": "Hi {name},\n\nHere's your private link to see and manage the ornaments you've claimed:",
        "outro": "If you didn't ask for this, you can safely ignore this email.",
    },
}
FIELDS = ("subject", "intro", "outro")


def fill(text: str, values: dict) -> str:
    return PLACEHOLDER_RE.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def unknown_placeholders(key: str, *texts: str) -> list[str]:
    allowed = DEFAULTS[key]["placeholders"]
    found = {m.group(1) for t in texts for m in PLACEHOLDER_RE.finditer(t or "")}
    return sorted(found - set(allowed))


def to_html(text: str) -> Markup:
    """Blank line = new paragraph, single newline = line break; everything is HTML-escaped."""
    parts = [p for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    return Markup("").join(
        Markup('<p style="margin:0 0 14px;">{}</p>').format(escape(p.strip()).replace("\n", Markup("<br>")))
        for p in parts
    )


def placeholder_values(ctx: dict) -> dict:
    site = ctx.get("site") or {}
    claimant, item, claim = ctx.get("claimant"), ctx.get("item"), ctx.get("claim")
    days_left = ctx.get("days_left")
    if days_left is None:
        phrase = "coming up"
    elif days_left <= 0:
        phrase = "today"
    elif days_left == 1:
        phrase = "tomorrow"
    else:
        phrase = f"in {days_left} days"
    values = {
        "name": (getattr(claimant, "name", "") or "there"),
        "site_title": site.get("site_title", ""),
        "deadline": fmt_date(ctx.get("deadline")),
        "contact_email": site.get("contact_email", ""),
        "deadline_phrase": phrase,
        "count": len(ctx.get("claims") or []),
        "hours": ctx.get("window_hours", ""),
        "reason": ctx.get("reason", ""),
    }
    if item is not None:
        values["item"] = item.title
        values["family"] = item.family.alias if ctx.get("show_families", True) else "the family"
    if claim is not None:
        values["quantity"] = claim.quantity
    return values


def stored(db, key: str) -> dict:
    """The current wording for an email: the admin's version if saved, else the default."""
    from .models import EmailTemplate

    base = {f: DEFAULTS[key][f] for f in FIELDS}
    row = db.get(EmailTemplate, key)
    if row:
        base.update(subject=row.subject or base["subject"], intro=row.intro, outro=row.outro)
    return base


def build(key: str, ctx: dict, override: dict | None = None) -> dict | None:
    """Returns the filled-in wording for template ``key`` (or None if it isn't an editable email)."""
    if key not in DEFAULTS:
        return None
    if override is None:
        from .db import SessionLocal

        db = SessionLocal()
        try:
            raw = stored(db, key)
        finally:
            db.close()
    else:
        raw = override
    values = placeholder_values(ctx)
    intro, outro = fill(raw["intro"], values), fill(raw["outro"], values)
    return {
        "subject": " ".join(fill(raw["subject"], values).split()),
        "intro": intro,
        "outro": outro,
        "intro_html": to_html(intro),
        "outro_html": to_html(outro),
    }


def sample_context(site_ctx: dict) -> dict:
    """Made-up data for previewing an email in the admin."""
    family = SimpleNamespace(id=0, alias="The Evergreen Family")
    item = SimpleNamespace(id=12, title="Winter coat", recipient="Girl, age 8", size="Youth M",
                           description="Purple or teal if possible.", link_url="", category="clothing",
                           family=family)
    claimant = SimpleNamespace(name="Alex", email="alex@example.com", token="sample")
    claim = SimpleNamespace(id=0, quantity=1, confirm_token="sample", item=item, claimant=claimant, note="")
    from .config import settings

    deadline = site_ctx.get("deadline")
    return dict(site_ctx, claim=claim, item=item, family=family, claimant=claimant, claims=[claim],
                manage_url=f"{settings.BASE_URL}/my/sample", confirm_url=f"{settings.BASE_URL}/confirm/sample",
                window_hours=24, reason="The family's needs changed.", days_left=5 if deadline else None)
