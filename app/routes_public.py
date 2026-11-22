from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.orm import Session, selectinload

from . import site_settings as ss
from .config import settings
from .db import get_db
from .models import CATEGORIES, Claim, Claimant, Family, Item
from .security import check_csrf, client_ip, limiter
from .services import (
    ClaimError,
    create_claim,
    confirm_claim,
    mark_delivered,
    normalize_email,
    release_claim,
    send_manage_link,
)
from .web import flash, redirect, render

router = APIRouter()


def _scatter(item_id: int) -> int:
    """Stable pseudo-random order, so one family's items don't sit next to each other."""
    return (item_id * 2654435761) % 2**32


def _tree_items(db: Session, show_families: bool) -> tuple[list[Family], list[Item], list[dict]]:
    families = (
        db.query(Family)
        .options(selectinload(Family.items).selectinload(Item.claims))
        .filter(Family.is_active.is_(True))
        .order_by(Family.sort_order, Family.id)
        .all()
    )
    items = [item for fam in families for item in fam.items if item.is_active]
    if not show_families:
        items.sort(key=lambda i: _scatter(i.id))
    data = []
    for item in items:
        row = {
            "id": item.id,
            "title": item.title,
            "description": item.description,
            "category": item.category,
            "categoryLabel": item.category_label,
            "recipient": item.recipient,
            "size": item.size,
            "quantity": item.quantity,
            "remaining": item.remaining,
            "state": item.state,
            "link": item.link_url,
        }
        if show_families:  # otherwise never send family info to the browser at all
            row.update(familyId=item.family.id, family=item.family.alias,
                       familyDescription=item.family.description)
        data.append(row)
    families = [f for f in families if any(i.is_active for i in f.items)] if show_families else []
    return families, items, data


@router.get("/")
def tree(request: Request, db: Session = Depends(get_db)):
    show_families = ss.show_families(ss.get_all(db))
    families, all_items, items = _tree_items(db, show_families)
    total = sum(i["quantity"] for i in items)
    claimed = sum(i["quantity"] - i["remaining"] for i in items)
    return render(request, db, "tree.html", {
        "families": families,
        # Combined list: grouped by gift type, then alphabetical (no family clustering).
        "flat_items": sorted(all_items, key=lambda i: (list(CATEGORIES).index(i.category)
                                                       if i.category in CATEGORIES else 99, i.title.lower())),
        "items": items,
        "total": total,
        "claimed": claimed,
        "remember": request.session.get("visitor", {}),
    })


@router.get("/ornament/{item_id}")
def ornament(item_id: int, request: Request, db: Session = Depends(get_db)):
    item = db.get(Item, item_id)
    if not item or not item.is_active or not item.family.is_active:
        raise HTTPException(404, "That ornament isn't on the tree anymore.")
    return render(request, db, "ornament.html", {
        "item": item,
        "remember": request.session.get("visitor", {}),
        "form": {},
    })


@router.post("/ornament/{item_id}/claim")
def claim(
    item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(""),
    email: str = Form(""),
    quantity: int = Form(1),
    note: str = Form(""),
    website: str = Form(""),  # honeypot: humans never see this field
    remember: str = Form(""),
    csrf: str = Form(""),
):
    check_csrf(request, csrf)
    if website:
        return redirect("/")
    if not limiter.allow(f"claim:{client_ip(request)}", settings.CLAIMS_PER_IP_PER_HOUR, 3600):
        raise HTTPException(429, "Too many claims from your network in the last hour. Please try again later.")

    try:
        claim_obj, claimant, is_new = create_claim(db, item_id, name, email, quantity, note)
    except ClaimError as exc:
        item = db.get(Item, item_id)
        if not item:
            raise HTTPException(404, str(exc))
        return render(request, db, "ornament.html", {
            "item": item,
            "error": str(exc),
            "remember": {},
            "form": {"name": name, "email": email, "note": note},
        }, status_code=400)

    if remember:
        request.session["visitor"] = {"name": name.strip(), "email": claimant.email}
    else:
        request.session.pop("visitor", None)
    # In "emailed link" mode, only link this browser to the claimant's private page if they're brand
    # new or already linked; otherwise anyone could type someone else's email to see their claims.
    # In "email only" mode an email address is all it takes to look them up anyway.
    if (is_new or request.session.get("claimant_token") == claimant.token
            or ss.lookup_by_email(ss.get_all(db))):
        request.session["claimant_token"] = claimant.token
    request.session["last_claim"] = claim_obj.id
    return redirect("/thanks")


@router.get("/thanks")
def thanks(request: Request, db: Session = Depends(get_db)):
    claim_id = request.session.get("last_claim")
    claim_obj = db.get(Claim, claim_id) if claim_id else None
    if not claim_obj:
        return redirect("/")
    return render(request, db, "thanks.html", {"claim": claim_obj})


@router.get("/confirm/{token}")
def confirm(token: str, request: Request, db: Session = Depends(get_db)):
    claim_obj = confirm_claim(db, token)
    if not claim_obj:
        raise HTTPException(404, "That confirmation link isn't valid.")
    request.session["claimant_token"] = claim_obj.claimant.token
    if claim_obj.status == "claimed":
        flash(request, f"Thank you! Your ornament “{claim_obj.item.title}” is confirmed.", "success")
    elif claim_obj.status == "released":
        flash(request, "Sorry — that hold expired and someone else has since claimed the ornament.", "error")
    return redirect(f"/my/{claim_obj.claimant.token}")


# ------------------------------------------------------------------ release from an email link
# The email button opens a confirmation page rather than releasing straight away: mail providers
# and link scanners open links automatically, which must never give an ornament away.


def _claim_by_token(db: Session, token: str) -> Claim:
    claim_obj = db.query(Claim).filter(Claim.confirm_token == token).one_or_none()
    if not claim_obj:
        raise HTTPException(404, "That link isn't valid.")
    return claim_obj


@router.get("/release/{token}")
def release_page(token: str, request: Request, db: Session = Depends(get_db)):
    claim_obj = _claim_by_token(db, token)
    return render(request, db, "release.html", {"claim": claim_obj})


@router.post("/release/{token}")
def release_confirm(token: str, request: Request, db: Session = Depends(get_db), csrf: str = Form("")):
    check_csrf(request, csrf)
    claim_obj = _claim_by_token(db, token)
    if claim_obj.status in ("pending", "claimed"):
        release_claim(db, claim_obj, "Released by the claimant", notify=False)
        flash(request, f"“{claim_obj.item.title}” is back on the tree for someone else. "
                       "Thanks for letting us know!", "info")
    # Whoever has this link has the giver's email, so it's fine to open their page.
    request.session["claimant_token"] = claim_obj.claimant.token
    return redirect(f"/my/{claim_obj.claimant.token}")


# ------------------------------------------------------------------ "My ornaments"


@router.get("/my")
def my_lookup(request: Request, db: Session = Depends(get_db)):
    token = request.session.get("claimant_token")
    if token and db.query(Claimant).filter(Claimant.token == token).count():
        return redirect(f"/my/{token}")
    return render(request, db, "my_lookup.html", {"email": ""})


@router.post("/my")
def my_lookup_send(request: Request, db: Session = Depends(get_db), email: str = Form(""), csrf: str = Form("")):
    check_csrf(request, csrf)
    if not limiter.allow(f"lookup:{client_ip(request)}", 30, 3600):
        raise HTTPException(429, "Too many requests. Please try again later.")
    if ss.lookup_by_email(ss.get_all(db)):
        claimant = db.query(Claimant).filter(Claimant.email == normalize_email(email)).one_or_none()
        if not claimant:
            return render(request, db, "my_lookup.html", {
                "email": email,
                "error": "We couldn't find any ornaments claimed with that email. Check the spelling, "
                         "or pick an ornament from the tree!",
            }, status_code=404)
        request.session["claimant_token"] = claimant.token
        return redirect(f"/my/{claimant.token}")
    send_manage_link(db, email)
    flash(request, "If that email has claimed any ornaments, we've sent it a private link. Check your inbox!",
          "success")
    return redirect("/my")


@router.post("/my/forget")
def my_forget(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.pop("claimant_token", None)
    request.session.pop("visitor", None)
    flash(request, "This browser no longer remembers you.", "info")
    return redirect("/")


def _claimant(db: Session, token: str) -> Claimant:
    claimant = db.query(Claimant).filter(Claimant.token == token).one_or_none()
    if not claimant:
        raise HTTPException(404, "That link isn't valid. You can request a new one from the “My ornaments” page.")
    return claimant


@router.get("/my/{token}")
def my_claims(token: str, request: Request, db: Session = Depends(get_db)):
    claimant = _claimant(db, token)
    request.session["claimant_token"] = claimant.token
    active = [c for c in claimant.claims if c.status != "released"]
    released = [c for c in claimant.claims if c.status == "released"]
    return render(request, db, "my_claims.html", {"claimant": claimant, "active": active, "released": released})


def _own_claim(db: Session, claimant: Claimant, claim_id: int) -> Claim:
    claim_obj = db.get(Claim, claim_id)
    if not claim_obj or claim_obj.claimant_id != claimant.id:
        raise HTTPException(404, "Claim not found.")
    return claim_obj


@router.post("/my/{token}/claims/{claim_id}/release")
def my_release(token: str, claim_id: int, request: Request, db: Session = Depends(get_db), csrf: str = Form("")):
    check_csrf(request, csrf)
    claimant = _claimant(db, token)
    claim_obj = _own_claim(db, claimant, claim_id)
    if claim_obj.status in ("pending", "claimed"):
        release_claim(db, claim_obj, "Released by the claimant", notify=False)
        flash(request, f"“{claim_obj.item.title}” is back on the tree for someone else. Thanks for letting us know!",
              "info")
    return redirect(f"/my/{token}")


@router.post("/my/{token}/claims/{claim_id}/delivered")
def my_delivered(token: str, claim_id: int, request: Request, db: Session = Depends(get_db), csrf: str = Form("")):
    check_csrf(request, csrf)
    claimant = _claimant(db, token)
    claim_obj = _own_claim(db, claimant, claim_id)
    mark_delivered(db, claim_obj)
    flash(request, "Thank you! We'll email you once we've received it.", "success")
    return redirect(f"/my/{token}")


@router.post("/my/{token}/reminders")
def my_reminders(token: str, request: Request, db: Session = Depends(get_db), enabled: str = Form(""),
                 csrf: str = Form("")):
    check_csrf(request, csrf)
    claimant = _claimant(db, token)
    claimant.reminders_enabled = enabled == "1"
    db.commit()
    flash(request, "Reminder emails turned " + ("on." if claimant.reminders_enabled else "off."), "info")
    return redirect(f"/my/{token}")
