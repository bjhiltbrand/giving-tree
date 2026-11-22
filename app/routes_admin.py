from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from markupsafe import escape
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from . import email_copy, emailer
from . import site_settings as ss
from .db import get_db
from .models import (
    CATEGORIES,
    AdminUser,
    Claim,
    Claimant,
    EmailLog,
    EmailTemplate,
    Family,
    Item,
)
from .scheduler import run_jobs
from .security import check_csrf, client_ip, hash_password, limiter, verify_password
from .services import mark_fulfilled, release_claim, reopen_claim, resend_confirmation, site_context
from .templating import fmt_dt
from .web import flash, redirect, render

router = APIRouter(prefix="/admin")


def require_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    admin_id = request.session.get("admin_id")
    user = db.get(AdminUser, admin_id) if admin_id else None
    if not user:
        raise HTTPException(303, headers={"Location": f"/admin/login?next={request.url.path}"})
    return user


def _form_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except ValueError:
        return default


# ------------------------------------------------------------------ auth


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db), next: str = "/admin"):
    return render(request, db, "admin/login.html", {"next": next})


@router.post("/login")
def login(request: Request, db: Session = Depends(get_db), username: str = Form(""), password: str = Form(""),
          next: str = Form("/admin"), csrf: str = Form("")):
    check_csrf(request, csrf)
    if not limiter.allow(f"login:{client_ip(request)}", 10, 900):
        raise HTTPException(429, "Too many login attempts. Wait a few minutes and try again.")
    user = db.query(AdminUser).filter(AdminUser.username == username.strip()).one_or_none()
    if not user or not verify_password(password, user.password_hash):
        return render(request, db, "admin/login.html", {"next": next, "error": "Invalid username or password."},
                      status_code=400)
    request.session["admin_id"] = user.id
    request.session["admin_username"] = user.username
    if not next.startswith("/admin"):
        next = "/admin"
    return redirect(next)


@router.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    check_csrf(request, csrf)
    request.session.pop("admin_id", None)
    request.session.pop("admin_username", None)
    return redirect("/")


# ------------------------------------------------------------------ dashboard


@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    families = (
        db.query(Family).options(selectinload(Family.items).selectinload(Item.claims))
        .order_by(Family.sort_order, Family.id).all()
    )
    stats = {"items": 0, "claimed": 0, "fulfilled": 0}
    for fam in families:
        for item in fam.items:
            if item.is_active and fam.is_active:
                stats["items"] += item.quantity
                stats["claimed"] += item.claimed_qty
                stats["fulfilled"] += item.fulfilled_qty
    by_status = dict(db.query(Claim.status, func.count(Claim.id)).group_by(Claim.status).all())
    recent = db.query(Claim).order_by(Claim.created_at.desc()).limit(10).all()
    awaiting = db.query(Claim).filter(Claim.status == "delivered").order_by(Claim.delivered_at).all()
    failed_emails = db.query(EmailLog).filter(EmailLog.status == "failed").count()
    return render(request, db, "admin/dashboard.html", {
        "families": families, "stats": stats, "by_status": by_status, "recent": recent,
        "awaiting": awaiting, "failed_emails": failed_emails,
    })


# ------------------------------------------------------------------ families


@router.get("/families/new")
def family_new(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    return render(request, db, "admin/family_form.html", {"family": None})


@router.post("/families/new")
def family_create(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin),
                  alias: str = Form(...), description: str = Form(""), private_notes: str = Form(""),
                  sort_order: str = Form("0"), is_active: str = Form(""), csrf: str = Form("")):
    check_csrf(request, csrf)
    fam = Family(alias=alias.strip()[:120], description=description.strip(), private_notes=private_notes.strip(),
                 sort_order=_form_int(sort_order), is_active=bool(is_active))
    db.add(fam)
    db.commit()
    flash(request, f"Created {fam.alias}. Now add their wish list below.", "success")
    return redirect(f"/admin/families/{fam.id}")


@router.get("/families/{family_id}")
def family_detail(family_id: int, request: Request, db: Session = Depends(get_db),
                  admin: AdminUser = Depends(require_admin)):
    fam = db.get(Family, family_id)
    if not fam:
        raise HTTPException(404, "Family not found.")
    return render(request, db, "admin/family.html", {"family": fam})


@router.get("/families/{family_id}/edit")
def family_edit(family_id: int, request: Request, db: Session = Depends(get_db),
                admin: AdminUser = Depends(require_admin)):
    fam = db.get(Family, family_id)
    if not fam:
        raise HTTPException(404, "Family not found.")
    return render(request, db, "admin/family_form.html", {"family": fam})


@router.post("/families/{family_id}/edit")
def family_update(family_id: int, request: Request, db: Session = Depends(get_db),
                  admin: AdminUser = Depends(require_admin), alias: str = Form(...), description: str = Form(""),
                  private_notes: str = Form(""), sort_order: str = Form("0"), is_active: str = Form(""),
                  csrf: str = Form("")):
    check_csrf(request, csrf)
    fam = db.get(Family, family_id)
    if not fam:
        raise HTTPException(404, "Family not found.")
    fam.alias = alias.strip()[:120]
    fam.description = description.strip()
    fam.private_notes = private_notes.strip()
    fam.sort_order = _form_int(sort_order)
    fam.is_active = bool(is_active)
    db.commit()
    flash(request, "Family saved.", "success")
    return redirect(f"/admin/families/{fam.id}")


@router.post("/families/{family_id}/delete")
def family_delete(family_id: int, request: Request, db: Session = Depends(get_db),
                  admin: AdminUser = Depends(require_admin), csrf: str = Form("")):
    check_csrf(request, csrf)
    fam = db.get(Family, family_id)
    if not fam:
        raise HTTPException(404, "Family not found.")
    if any(c.status != "released" for i in fam.items for c in i.claims):
        flash(request, "This family has claimed items, so it can't be deleted. Hide it instead.", "error")
        return redirect(f"/admin/families/{fam.id}")
    db.delete(fam)
    db.commit()
    flash(request, "Family deleted.", "info")
    return redirect("/admin")


# ------------------------------------------------------------------ items


def _apply_item_form(item: Item, title, description, category, recipient, size, quantity, link_url, sort_order,
                     is_active) -> None:
    item.title = title.strip()[:200]
    item.description = description.strip()
    item.category = category if category in CATEGORIES else "other"
    item.recipient = recipient.strip()[:120]
    item.size = size.strip()[:80]
    item.quantity = max(1, _form_int(quantity, 1))
    link_url = link_url.strip()[:500]
    item.link_url = link_url if link_url.startswith(("http://", "https://")) or not link_url else "https://" + link_url
    item.sort_order = _form_int(sort_order)
    item.is_active = bool(is_active)


@router.post("/families/{family_id}/items")
def item_create(family_id: int, request: Request, db: Session = Depends(get_db),
                admin: AdminUser = Depends(require_admin), title: str = Form(...), description: str = Form(""),
                category: str = Form("other"), recipient: str = Form(""), size: str = Form(""),
                quantity: str = Form("1"), link_url: str = Form(""), sort_order: str = Form("0"),
                is_active: str = Form("1"), csrf: str = Form("")):
    check_csrf(request, csrf)
    fam = db.get(Family, family_id)
    if not fam:
        raise HTTPException(404, "Family not found.")
    item = Item(family_id=fam.id)
    _apply_item_form(item, title, description, category, recipient, size, quantity, link_url, sort_order, is_active)
    db.add(item)
    db.commit()
    flash(request, f"Added “{item.title}”.", "success")
    return redirect(f"/admin/families/{fam.id}#add-item")


@router.post("/families/{family_id}/items/bulk")
def item_bulk_create(family_id: int, request: Request, db: Session = Depends(get_db),
                     admin: AdminUser = Depends(require_admin), lines: str = Form(""),
                     default_category: str = Form("other"), csrf: str = Form("")):
    """One item per line:  Title | Recipient | Size | Category | Quantity   (only Title is required)."""
    check_csrf(request, csrf)
    fam = db.get(Family, family_id)
    if not fam:
        raise HTTPException(404, "Family not found.")
    cat_lookup = {k: k for k in CATEGORIES} | {v.lower(): k for k, v in CATEGORIES.items()}
    cat_lookup |= {"clothes": "clothing", "toy": "toys", "gift card": "giftcard", "gift cards": "giftcard",
                   "book": "books"}
    added = 0
    for raw in lines.splitlines():
        if not raw.strip():
            continue
        parts = [p.strip() for p in raw.split("|")] + [""] * 5
        title, recipient, size, cat, qty = parts[:5]
        if not title:
            continue
        category = cat_lookup.get(cat.lower(), default_category if default_category in CATEGORIES else "other")
        item = Item(family_id=fam.id)
        _apply_item_form(item, title, "", category, recipient, size, qty or "1", "", "0", "1")
        db.add(item)
        added += 1
    db.commit()
    flash(request, f"Added {added} item(s).", "success" if added else "info")
    return redirect(f"/admin/families/{fam.id}")


@router.get("/items/{item_id}")
def item_edit(item_id: int, request: Request, db: Session = Depends(get_db),
              admin: AdminUser = Depends(require_admin)):
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found.")
    return render(request, db, "admin/item_form.html", {"item": item})


@router.post("/items/{item_id}")
def item_update(item_id: int, request: Request, db: Session = Depends(get_db),
                admin: AdminUser = Depends(require_admin), title: str = Form(...), description: str = Form(""),
                category: str = Form("other"), recipient: str = Form(""), size: str = Form(""),
                quantity: str = Form("1"), link_url: str = Form(""), sort_order: str = Form("0"),
                is_active: str = Form(""), csrf: str = Form("")):
    check_csrf(request, csrf)
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found.")
    _apply_item_form(item, title, description, category, recipient, size, quantity, link_url, sort_order, is_active)
    if item.quantity < item.claimed_qty:
        item.quantity = item.claimed_qty
        flash(request, f"Quantity can't be lower than what's already claimed ({item.claimed_qty}).", "error")
    db.commit()
    flash(request, "Item saved.", "success")
    return redirect(f"/admin/families/{item.family_id}")


@router.post("/items/{item_id}/duplicate")
def item_duplicate(item_id: int, request: Request, db: Session = Depends(get_db),
                   admin: AdminUser = Depends(require_admin), csrf: str = Form("")):
    check_csrf(request, csrf)
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found.")
    copy = Item(family_id=item.family_id, title=item.title, description=item.description, category=item.category,
                recipient=item.recipient, size=item.size, quantity=item.quantity, link_url=item.link_url,
                sort_order=item.sort_order, is_active=item.is_active)
    db.add(copy)
    db.commit()
    flash(request, f"Duplicated “{item.title}”.", "success")
    return redirect(f"/admin/items/{copy.id}")


@router.post("/items/{item_id}/delete")
def item_delete(item_id: int, request: Request, db: Session = Depends(get_db),
                admin: AdminUser = Depends(require_admin), csrf: str = Form("")):
    check_csrf(request, csrf)
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(404, "Item not found.")
    family_id = item.family_id
    if any(c.status != "released" for c in item.claims):
        flash(request, "That item has been claimed, so it can't be deleted. Release the claim or hide the item.",
              "error")
        return redirect(f"/admin/families/{family_id}")
    db.delete(item)
    db.commit()
    flash(request, "Item deleted.", "info")
    return redirect(f"/admin/families/{family_id}")


# ------------------------------------------------------------------ claims


def _claims_query(db: Session, status: str, family_id: int | None, q: str):
    query = db.query(Claim).join(Item).join(Claimant)
    if status == "open":
        query = query.filter(Claim.status.in_(("pending", "claimed", "delivered")))
    elif status and status != "all":
        query = query.filter(Claim.status == status)
    if family_id:
        query = query.filter(Item.family_id == family_id)
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(func.lower(Claimant.email).like(like) | func.lower(Claimant.name).like(like)
                             | func.lower(Item.title).like(like))
    return query.order_by(Claim.created_at.desc())


@router.get("/claims")
def claims_list(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin),
                status: str = "open", family: str = "", q: str = ""):
    family_id = _form_int(family) or None
    claims = _claims_query(db, status, family_id, q).all()
    families = db.query(Family).order_by(Family.sort_order, Family.id).all()
    return render(request, db, "admin/claims.html", {
        "claims": claims, "families": families, "status": status, "family_id": family_id, "q": q,
    })


@router.get("/claims.csv")
def claims_csv(db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin), status: str = "all",
               family: str = "", q: str = ""):
    claims = _claims_query(db, status, _form_int(family) or None, q).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Claim ID", "Ornament #", "Family", "Item", "Recipient", "Size", "Qty", "Status", "Name", "Email",
                "Claimant note", "Admin note", "Claimed", "Dropped off", "Received", "Released", "Release reason"])
    for c in claims:
        w.writerow([c.id, c.item.id, c.item.family.alias, c.item.title, c.item.recipient, c.item.size, c.quantity,
                    c.status_label, c.claimant.name, c.claimant.email, c.note, c.admin_note, fmt_dt(c.created_at),
                    fmt_dt(c.delivered_at), fmt_dt(c.fulfilled_at), fmt_dt(c.released_at), c.release_reason])
    filename = f"giving-tree-claims-{datetime.now():%Y%m%d}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _claim_or_404(db: Session, claim_id: int) -> Claim:
    claim = db.get(Claim, claim_id)
    if not claim:
        raise HTTPException(404, "Claim not found.")
    return claim


def _back(request: Request, default: str = "/admin/claims") -> str:
    ref = request.headers.get("referer", "")
    if "/admin" in ref:
        return ref[ref.index("/admin"):]
    return default


@router.post("/claims/{claim_id}/fulfill")
def claim_fulfill(claim_id: int, request: Request, db: Session = Depends(get_db),
                  admin: AdminUser = Depends(require_admin), notify: str = Form("1"), csrf: str = Form("")):
    check_csrf(request, csrf)
    claim = _claim_or_404(db, claim_id)
    mark_fulfilled(db, claim, notify=notify == "1")
    flash(request, f"Marked “{claim.item.title}” as received" + (" and thanked the giver." if notify == "1" else "."),
          "success")
    return redirect(_back(request))


@router.post("/claims/{claim_id}/release")
def claim_release(claim_id: int, request: Request, db: Session = Depends(get_db),
                  admin: AdminUser = Depends(require_admin), reason: str = Form(""), notify: str = Form("1"),
                  csrf: str = Form("")):
    check_csrf(request, csrf)
    claim = _claim_or_404(db, claim_id)
    release_claim(db, claim, reason.strip() or "Released by an organizer", by_admin=True, notify=notify == "1")
    flash(request, f"Released “{claim.item.title}” back onto the tree.", "info")
    return redirect(_back(request))


@router.post("/claims/{claim_id}/reopen")
def claim_reopen(claim_id: int, request: Request, db: Session = Depends(get_db),
                 admin: AdminUser = Depends(require_admin), csrf: str = Form("")):
    check_csrf(request, csrf)
    claim = _claim_or_404(db, claim_id)
    reopen_claim(db, claim)
    flash(request, "Claim moved back to “Claimed”.", "info")
    return redirect(_back(request))


@router.post("/claims/{claim_id}/resend")
def claim_resend(claim_id: int, request: Request, db: Session = Depends(get_db),
                 admin: AdminUser = Depends(require_admin), csrf: str = Form("")):
    check_csrf(request, csrf)
    claim = _claim_or_404(db, claim_id)
    resend_confirmation(db, claim)
    flash(request, f"Re-sent the confirmation email to {claim.claimant.email}.", "success")
    return redirect(_back(request))


@router.post("/claims/{claim_id}/note")
def claim_note(claim_id: int, request: Request, db: Session = Depends(get_db),
               admin: AdminUser = Depends(require_admin), admin_note: str = Form(""), csrf: str = Form("")):
    check_csrf(request, csrf)
    claim = _claim_or_404(db, claim_id)
    claim.admin_note = admin_note.strip()
    db.commit()
    flash(request, "Note saved.", "success")
    return redirect(_back(request))


# ------------------------------------------------------------------ settings


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    return render(request, db, "admin/settings.html", {"values": ss.get_all(db)})


@router.post("/settings")
async def settings_save(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    updates = {}
    for key in ss.DEFAULTS:
        if key in ("claims_open", "show_families"):  # checkboxes: absent when unticked
            updates[key] = "1" if form.get(key) else "0"
        elif key == "require_confirmation":  # radio buttons sending "0" / "1"
            updates[key] = "1" if form.get(key) == "1" else "0"
        elif key == "lookup_mode":
            updates[key] = "link" if form.get(key) == "link" else "email"
        elif key in form:
            updates[key] = str(form.get(key)).strip()
    if updates.get("deadline") and ss.deadline(updates) is None:
        flash(request, "Deadline must be a date (YYYY-MM-DD).", "error")
        updates.pop("deadline")
    ss.save(db, updates)
    flash(request, "Settings saved.", "success")
    return redirect("/admin/settings")


@router.post("/test-email")
def test_email(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin),
               to: str = Form(""), csrf: str = Form("")):
    from . import emailer
    from .config import settings as env

    check_csrf(request, csrf)
    to = to.strip()
    if not to:
        flash(request, "Enter an address to send the test to.", "error")
        return redirect("/admin/settings")
    emailer.send(to, "test", {"site": ss.get_all(db), "smtp_host": env.SMTP_HOST}, wait=True)
    row = db.query(EmailLog).order_by(EmailLog.id.desc()).first()
    if row and row.status == "sent":
        flash(request, f"Test email sent to {to} via {env.SMTP_HOST}. Check the inbox (and spam folder).", "success")
    elif row and row.status == "logged":
        flash(request, "SMTP_HOST isn't set, so the email was only written to the log. Configure SMTP in .env.",
              "info")
    else:
        flash(request, f"Sending failed: {row.error if row else 'unknown error'}", "error")
    return redirect("/admin/settings")


@router.post("/run-jobs")
def run_jobs_now(request: Request, admin: AdminUser = Depends(require_admin), csrf: str = Form("")):
    check_csrf(request, csrf)
    run_jobs()
    flash(request, "Reminder job ran. Check the Emails page for anything that was sent.", "info")
    return redirect("/admin/emails")


# ------------------------------------------------------------------ email wording


def _email_form(form, key: str) -> dict:
    default = email_copy.DEFAULTS[key]
    subject = " ".join(str(form.get("subject", "")).split())[:300]
    return {
        "subject": subject or default["subject"],
        "intro": str(form.get("intro", "")).strip(),
        "outro": str(form.get("outro", "")).strip(),
    }


@router.get("/email-text")
def email_text_page(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin),
                    open: str = ""):
    custom = {row.key for row in db.query(EmailTemplate).all()}
    emails = [
        {"key": key, "meta": meta, "wording": email_copy.stored(db, key), "custom": key in custom}
        for key, meta in email_copy.DEFAULTS.items()
    ]
    return render(request, db, "admin/email_text.html", {"emails": emails, "open_key": open})


@router.post("/email-text/{key}")
async def email_text_save(key: str, request: Request, db: Session = Depends(get_db),
                          admin: AdminUser = Depends(require_admin)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    if key not in email_copy.DEFAULTS:
        raise HTTPException(404, "Unknown email.")
    values = _email_form(form, key)
    row = db.get(EmailTemplate, key) or EmailTemplate(key=key)
    row.subject, row.intro, row.outro = values["subject"], values["intro"], values["outro"]
    db.add(row)
    db.commit()
    flash(request, f"Saved the “{email_copy.DEFAULTS[key]['label']}” email.", "success")
    unknown = email_copy.unknown_placeholders(key, *values.values())
    if unknown:
        flash(request, "These placeholders aren't available in this email and will appear exactly as typed: "
              + ", ".join("{" + u + "}" for u in unknown), "error")
    return redirect(f"/admin/email-text?open={key}#{key}")


@router.post("/email-text/{key}/reset")
def email_text_reset(key: str, request: Request, db: Session = Depends(get_db),
                     admin: AdminUser = Depends(require_admin), csrf: str = Form("")):
    check_csrf(request, csrf)
    row = db.get(EmailTemplate, key)
    if row:
        db.delete(row)
        db.commit()
    flash(request, "Restored the original wording.", "info")
    return redirect(f"/admin/email-text?open={key}#{key}")


@router.post("/email-text/{key}/preview")
async def email_text_preview(key: str, request: Request, db: Session = Depends(get_db),
                             admin: AdminUser = Depends(require_admin)):
    form = await request.form()
    check_csrf(request, form.get("csrf"))
    if key not in email_copy.DEFAULTS:
        raise HTTPException(404, "Unknown email.")
    ctx = email_copy.sample_context(site_context(db))
    subject, text, html = emailer.render(key, ctx, copy_override=_email_form(form, key))
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Preview: {escape(subject)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{margin:0;font:14px/1.5 system-ui,sans-serif;background:#eee;color:#222}}
.bar{{background:#123f28;color:#fff;padding:12px 16px}} .bar b{{font-weight:600}}
.wrap{{max-width:760px;margin:0 auto;padding:16px}} iframe{{width:100%;height:720px;border:0;background:#fff;border-radius:8px}}
pre{{white-space:pre-wrap;background:#fff;padding:16px;border-radius:8px}}</style></head><body>
<div class="bar">Preview with sample data (giver “Alex”, ornament #12 “Winter coat”). Nothing was saved or sent.</div>
<div class="wrap"><p><b>Subject:</b> {escape(subject)}</p>
<iframe srcdoc="{escape(html)}" title="Email preview"></iframe>
<h3>Plain-text version</h3><pre>{escape(text)}</pre></div></body></html>"""
    return HTMLResponse(page)


# ------------------------------------------------------------------ emails log


@router.get("/emails")
def emails_page(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    emails = db.query(EmailLog).order_by(EmailLog.created_at.desc()).limit(200).all()
    return render(request, db, "admin/emails.html", {"emails": emails})


# ------------------------------------------------------------------ admins


@router.get("/users")
def users_page(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin)):
    users = db.query(AdminUser).order_by(AdminUser.username).all()
    return render(request, db, "admin/users.html", {"users": users, "me": admin})


@router.post("/users")
def users_create(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin),
                 username: str = Form(""), password: str = Form(""), csrf: str = Form("")):
    check_csrf(request, csrf)
    username = username.strip()
    if not username or len(password) < 8:
        flash(request, "Username is required and the password must be at least 8 characters.", "error")
    elif db.query(AdminUser).filter(AdminUser.username == username).count():
        flash(request, "That username already exists.", "error")
    else:
        db.add(AdminUser(username=username, password_hash=hash_password(password)))
        db.commit()
        flash(request, f"Added admin “{username}”.", "success")
    return redirect("/admin/users")


@router.post("/users/{user_id}/delete")
def users_delete(user_id: int, request: Request, db: Session = Depends(get_db),
                 admin: AdminUser = Depends(require_admin), csrf: str = Form("")):
    check_csrf(request, csrf)
    if user_id == admin.id:
        flash(request, "You can't delete yourself.", "error")
    else:
        user = db.get(AdminUser, user_id)
        if user:
            db.delete(user)
            db.commit()
            flash(request, "Admin removed.", "info")
    return redirect("/admin/users")


@router.post("/password")
def change_password(request: Request, db: Session = Depends(get_db), admin: AdminUser = Depends(require_admin),
                    current: str = Form(""), new: str = Form(""), csrf: str = Form("")):
    check_csrf(request, csrf)
    if not verify_password(current, admin.password_hash):
        flash(request, "Current password is incorrect.", "error")
    elif len(new) < 8:
        flash(request, "New password must be at least 8 characters.", "error")
    else:
        admin.password_hash = hash_password(new)
        db.commit()
        flash(request, "Password changed.", "success")
    return redirect("/admin/users")
