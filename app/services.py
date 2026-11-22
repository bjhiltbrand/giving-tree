"""Claim workflow: creating, confirming, releasing and fulfilling claims (+ the emails they send)."""
from __future__ import annotations

import re
import threading

from sqlalchemy.orm import Session

from . import emailer
from . import site_settings as ss
from .models import Claim, Claimant, Item, utcnow

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_claim_lock = threading.Lock()  # app runs as a single process; serialises "check stock -> insert"


class ClaimError(Exception):
    pass


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def site_context(db: Session) -> dict:
    values = ss.get_all(db)
    return {
        "site": values,
        "deadline": ss.deadline(values),
    }


def manage_url(claimant: Claimant) -> str:
    from .config import settings

    return f"{settings.BASE_URL}/my/{claimant.token}"


def get_or_create_claimant(db: Session, email: str, name: str, phone: str = "") -> tuple[Claimant, bool]:
    email = normalize_email(email)
    claimant = db.query(Claimant).filter(Claimant.email == email).one_or_none()
    if claimant:
        # Don't let an anonymous visitor overwrite an existing person's name; only fill blanks.
        if name and not claimant.name:
            claimant.name = name
        if phone and not claimant.phone:
            claimant.phone = phone
        return claimant, False
    claimant = Claimant(email=email, name=name, phone=phone)
    db.add(claimant)
    db.flush()
    return claimant, True


def create_claim(db: Session, item_id: int, name: str, email: str, quantity: int, note: str,
                 phone: str = "") -> tuple[Claim, Claimant, bool]:
    values = ss.get_all(db)
    if not ss.as_bool(values["claims_open"]):
        raise ClaimError(values["closed_message"] or "Claiming is currently closed.")

    name = (name or "").strip()[:120]
    email = normalize_email(email)
    note = (note or "").strip()[:1000]
    if not name:
        raise ClaimError("Please enter your name.")
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise ClaimError("Please enter a valid email address.")

    require_confirm = ss.as_bool(values["require_confirmation"])
    max_qty = max(1, ss.as_int(values["max_claim_qty"], 5))

    with _claim_lock:
        item = db.get(Item, item_id)
        if not item or not item.is_active or not item.family.is_active:
            raise ClaimError("Sorry, that ornament is no longer on the tree.")
        db.refresh(item)
        remaining = item.remaining
        if remaining <= 0:
            raise ClaimError("Someone just claimed this ornament! Please choose another one.")
        quantity = max(1, min(int(quantity or 1), remaining, max_qty))

        claimant, is_new = get_or_create_claimant(db, email, name, phone)
        claim = Claim(
            item_id=item.id,
            claimant_id=claimant.id,
            quantity=quantity,
            note=note,
            status="pending" if require_confirm else "claimed",
            confirmed_at=None if require_confirm else utcnow(),
        )
        db.add(claim)
        db.commit()
        db.refresh(claim)

    ctx = dict(site_context(db), claim=claim, item=claim.item, family=claim.item.family,
               claimant=claimant, manage_url=manage_url(claimant))
    if require_confirm:
        from .config import settings

        ctx["confirm_url"] = f"{settings.BASE_URL}/confirm/{claim.confirm_token}"
        ctx["window_hours"] = ss.as_int(values["confirm_window_hours"], 24)
        emailer.send(claimant.email, "claim_verify", ctx)
    else:
        emailer.send(claimant.email, "claim_confirmation", ctx)
        notify_admin(db, "admin_new_claim", claim)
    return claim, claimant, is_new


def confirm_claim(db: Session, token: str) -> Claim | None:
    claim = db.query(Claim).filter(Claim.confirm_token == token).one_or_none()
    if not claim:
        return None
    if claim.status == "pending":
        claim.status = "claimed"
        claim.confirmed_at = utcnow()
        db.commit()
        ctx = dict(site_context(db), claim=claim, item=claim.item, family=claim.item.family,
                   claimant=claim.claimant, manage_url=manage_url(claim.claimant))
        emailer.send(claim.claimant.email, "claim_confirmation", ctx)
        notify_admin(db, "admin_new_claim", claim)
    elif claim.status == "released" and claim.release_reason == "Confirmation link expired":
        # Late click: re-activate if the item is still available.
        with _claim_lock:
            db.refresh(claim.item)
            if claim.item.remaining >= claim.quantity:
                claim.status = "claimed"
                claim.confirmed_at = utcnow()
                claim.released_at = None
                claim.release_reason = ""
                db.commit()
                notify_admin(db, "admin_new_claim", claim)
    return claim


def release_claim(db: Session, claim: Claim, reason: str, by_admin: bool = False, notify: bool = True) -> None:
    if claim.status in ("released", "fulfilled"):
        return
    claim.status = "released"
    claim.released_at = utcnow()
    claim.release_reason = reason[:200]
    db.commit()
    ctx = dict(site_context(db), claim=claim, item=claim.item, family=claim.item.family,
               claimant=claim.claimant, manage_url=manage_url(claim.claimant), by_admin=by_admin, reason=reason)
    if notify:
        emailer.send(claim.claimant.email, "released", ctx)
    if not by_admin:
        notify_admin(db, "admin_released", claim)


def mark_delivered(db: Session, claim: Claim) -> None:
    if claim.status != "claimed":
        return
    claim.status = "delivered"
    claim.delivered_at = utcnow()
    db.commit()
    notify_admin(db, "admin_delivered", claim)


def mark_fulfilled(db: Session, claim: Claim, notify: bool = True) -> None:
    if claim.status in ("fulfilled", "released"):
        return
    claim.status = "fulfilled"
    claim.fulfilled_at = utcnow()
    if not claim.delivered_at:
        claim.delivered_at = claim.fulfilled_at
    db.commit()
    if notify:
        ctx = dict(site_context(db), claim=claim, item=claim.item, family=claim.item.family,
                   claimant=claim.claimant, manage_url=manage_url(claim.claimant))
        emailer.send(claim.claimant.email, "fulfilled", ctx)


def reopen_claim(db: Session, claim: Claim) -> None:
    """Admin undo: move a fulfilled/delivered claim back to 'claimed'."""
    if claim.status in ("fulfilled", "delivered"):
        claim.status = "claimed"
        claim.fulfilled_at = None
        claim.delivered_at = None
        db.commit()


def send_manage_link(db: Session, email: str) -> None:
    claimant = db.query(Claimant).filter(Claimant.email == normalize_email(email)).one_or_none()
    if not claimant:
        return
    ctx = dict(site_context(db), claimant=claimant, manage_url=manage_url(claimant))
    emailer.send(claimant.email, "manage_link", ctx)


def resend_confirmation(db: Session, claim: Claim) -> None:
    from .config import settings

    values = ss.get_all(db)
    ctx = dict(site_context(db), claim=claim, item=claim.item, family=claim.item.family,
               claimant=claim.claimant, manage_url=manage_url(claim.claimant))
    if claim.status == "pending":
        ctx["confirm_url"] = f"{settings.BASE_URL}/confirm/{claim.confirm_token}"
        ctx["window_hours"] = ss.as_int(values["confirm_window_hours"], 24)
        emailer.send(claim.claimant.email, "claim_verify", ctx)
    else:
        emailer.send(claim.claimant.email, "claim_confirmation", ctx)


def notify_admin(db: Session, template: str, claim: Claim) -> None:
    values = ss.get_all(db)
    to = [a.strip() for a in (values.get("admin_notify_email") or "").split(",") if a.strip()]
    ctx = dict(site_context(db), claim=claim, item=claim.item, family=claim.item.family, claimant=claim.claimant)
    for addr in to:
        emailer.send(addr, template, ctx)
