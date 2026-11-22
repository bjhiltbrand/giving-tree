"""Outgoing email. Templates live in templates/email/ and define three blocks:
``subject``, ``text`` and ``html``. If SMTP_HOST is not configured, messages are
written to the application log (and the admin "Emails" page) instead of sent."""
from __future__ import annotations

import html as html_lib
import logging
import smtplib
import ssl
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from email.utils import make_msgid

from . import email_copy
from .config import settings
from .db import SessionLocal
from .models import EmailLog
from .templating import env

log = logging.getLogger("giving_tree.email")
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mailer")


def render(template_name: str, context: dict, copy_override: dict | None = None) -> tuple[str, str, str]:
    """``copy_override`` (subject/intro/outro) is used by the admin preview to show unsaved edits."""
    tmpl = env.get_template(f"email/{template_name}.html")
    context = dict(context, BASE_URL=settings.BASE_URL)
    context["copy"] = email_copy.build(template_name, context, copy_override)
    # Blocks rendered on their own don't see top-level {% import %}s, so pass the layout macros in.
    layout = env.get_template("email/_layout.html").make_module(context)
    ctx = tmpl.new_context(dict(context, L=layout))
    # Subject and plain-text body come from an autoescaping template; undo the HTML escaping there
    # so "Men's shoes" doesn't arrive as "Men&#39;s shoes".
    subject = html_lib.unescape("".join(tmpl.blocks["subject"](ctx)).strip())
    text = html_lib.unescape("".join(tmpl.blocks["text"](ctx)).strip()) + "\n"
    html = "".join(tmpl.blocks["html"](ctx)).strip()
    return subject, text, html


def _deliver(log_id: int, to: str, subject: str, text: str, html: str) -> None:
    status, error = "sent", ""
    try:
        if not settings.SMTP_HOST:
            log.info("EMAIL (SMTP not configured) to=%s subject=%s\n%s", to, subject, text)
            status = "logged"
        else:
            msg = EmailMessage()
            msg["From"] = settings.MAIL_FROM
            msg["To"] = to
            msg["Subject"] = subject
            msg["Message-ID"] = make_msgid(domain=settings.MAIL_FROM.split("@")[-1].strip("> ") or None)
            if settings.MAIL_REPLY_TO:
                msg["Reply-To"] = settings.MAIL_REPLY_TO
            msg.set_content(text)
            msg.add_alternative(html, subtype="html")

            if settings.SMTP_SECURITY == "ssl":
                server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30,
                                          context=ssl.create_default_context())
            else:
                server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30)
            with server:
                if settings.SMTP_SECURITY == "starttls":
                    server.starttls(context=ssl.create_default_context())
                if settings.SMTP_USERNAME:
                    server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
                server.send_message(msg)
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", f"{type(exc).__name__}: {exc}"
        log.exception("Failed to send email to %s", to)

    db = SessionLocal()
    try:
        row = db.get(EmailLog, log_id)
        if row:
            row.status = status
            row.error = error[:2000]
            db.commit()
    finally:
        db.close()


def send(to: str, template_name: str, context: dict, kind: str | None = None, wait: bool = False) -> None:
    """Queue an email. Rendering happens now (so the caller's objects are still loaded);
    delivery happens on a background thread unless ``wait`` is True."""
    if not to:
        return
    subject, text, html = render(template_name, context)
    db = SessionLocal()
    try:
        row = EmailLog(to_addr=to, subject=subject[:300], kind=kind or template_name, status="queued")
        db.add(row)
        db.commit()
        log_id = row.id
    finally:
        db.close()
    if wait:
        _deliver(log_id, to, subject, text, html)
    else:
        _pool.submit(_deliver, log_id, to, subject, text, html)
