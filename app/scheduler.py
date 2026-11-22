"""Background job: expires unconfirmed claims and sends reminder emails.

Reminder rules
--------------
* Deadline reminders: for every N in "reminder_days" (e.g. 7,2) a reminder is due once
  today >= deadline - N days (and today <= deadline). A reminder is skipped if the claim
  was made *inside* that window (they just got a confirmation email, no need to nag).
* Follow-up: if "followup_days" > 0, a single check-in is sent that many days after the claim.
* All reminders due for the same person are bundled into one email.
* Only claims in status "claimed" get reminders, and only if the person hasn't opted out.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.exc import IntegrityError

from . import emailer
from . import site_settings as ss
from .config import settings
from .db import SessionLocal
from .models import Claim, ReminderLog, utcnow
from .services import manage_url, release_claim, site_context
from .templating import LOCAL_TZ, to_local

log = logging.getLogger("giving_tree.scheduler")
_scheduler: BackgroundScheduler | None = None


def expire_pending(db) -> int:
    values = ss.get_all(db)
    if not ss.as_bool(values["require_confirmation"]):
        return 0
    hours = max(1, ss.as_int(values["confirm_window_hours"], 24))
    cutoff = utcnow() - timedelta(hours=hours)
    expired = db.query(Claim).filter(Claim.status == "pending", Claim.created_at < cutoff).all()
    for claim in expired:
        release_claim(db, claim, "Confirmation link expired", by_admin=True)
    return len(expired)


def due_reminders(db, now_local: datetime | None = None) -> dict[int, list[tuple[Claim, list[str], bool]]]:
    """Returns {claimant_id: [(claim, [kinds], should_send), ...]}."""
    values = ss.get_all(db)
    deadline = ss.deadline(values)
    days = ss.reminder_days(values)
    followup = ss.as_int(values["followup_days"], 0)
    now_local = now_local or datetime.now(LOCAL_TZ)
    today = now_local.date()

    out: dict[int, list[tuple[Claim, list[str], bool]]] = defaultdict(list)
    claims = db.query(Claim).filter(Claim.status == "claimed").all()
    for claim in claims:
        done = {r.kind for r in claim.reminders}
        claimed_on = to_local(claim.created_at).date()
        kinds_send, kinds_skip = [], []

        if deadline and today <= deadline:
            for n in days:
                kind = f"deadline-{n}"
                if kind in done:
                    continue
                window_start = deadline - timedelta(days=n)
                if today >= window_start:
                    (kinds_send if claimed_on < window_start else kinds_skip).append(kind)

        if followup > 0 and "followup" not in done:
            if to_local(claim.created_at) + timedelta(days=followup) <= now_local:
                # Don't bother if the deadline has passed or a deadline reminder is going out anyway.
                if deadline and today > deadline:
                    kinds_skip.append("followup")
                else:
                    kinds_send.append("followup")

        if kinds_send or kinds_skip:
            enabled = claim.claimant.reminders_enabled
            if kinds_send and enabled:
                out[claim.claimant_id].append((claim, kinds_send, True))
            if kinds_skip or (kinds_send and not enabled):
                out[claim.claimant_id].append((claim, kinds_skip + ([] if enabled else kinds_send), False))
    return out


def send_reminders(db) -> int:
    grouped = due_reminders(db)
    sent = 0
    ctx_base = site_context(db)
    today = datetime.now(LOCAL_TZ).date()
    for _claimant_id, entries in grouped.items():
        to_send = [c for c, _k, send in entries if send]
        # Log first (unique constraint guarantees we never double-send even if two jobs overlap).
        try:
            for claim, kinds, send in entries:
                for kind in kinds:
                    db.add(ReminderLog(claim_id=claim.id, kind=kind, sent=send))
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        if to_send:
            claimant = to_send[0].claimant
            deadline = ctx_base["deadline"]
            ctx = dict(ctx_base, claimant=claimant, claims=to_send, manage_url=manage_url(claimant),
                       days_left=(deadline - today).days if deadline else None)
            emailer.send(claimant.email, "reminder", ctx)
            sent += 1
    return sent


def run_jobs() -> None:
    db = SessionLocal()
    try:
        expired = expire_pending(db)
        sent = send_reminders(db)
        if expired or sent:
            log.info("Scheduler: expired %s pending claim(s), sent %s reminder email(s)", expired, sent)
    except Exception:
        log.exception("Scheduler run failed")
    finally:
        db.close()


def start() -> None:
    global _scheduler
    if _scheduler:
        return
    _scheduler = BackgroundScheduler(timezone=settings.TIMEZONE)
    _scheduler.add_job(run_jobs, "interval", minutes=max(1, settings.SCHEDULER_INTERVAL_MINUTES),
                       id="giving-tree-jobs", next_run_time=datetime.now(LOCAL_TZ) + timedelta(seconds=30),
                       max_instances=1, coalesce=True)
    _scheduler.start()
    log.info("Scheduler started (every %s min)", settings.SCHEDULER_INTERVAL_MINUTES)


def shutdown() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
