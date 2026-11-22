"""Shared helpers for HTML routes."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from . import site_settings as ss
from .security import csrf_token
from .templating import templates


def flash(request: Request, message: str, kind: str = "info") -> None:
    request.session.setdefault("flash", [])
    request.session["flash"] = request.session["flash"] + [[kind, message]]


def render(request: Request, db: Session, name: str, context: dict | None = None, status_code: int = 200):
    values = ss.get_all(db)
    ctx = {
        "request": request,
        "site": values,
        "deadline": ss.deadline(values),
        "show_families": ss.show_families(values),
        "csrf": csrf_token(request),
        "flashes": request.session.pop("flash", []),
        "my_token": request.session.get("claimant_token"),
        "admin_user": request.session.get("admin_username"),
    }
    ctx.update(context or {})
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)
