from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

from .config import settings
from .models import CLAIM_STATUSES

TEMPLATE_DIR = Path(__file__).parent / "templates"
LOCAL_TZ = ZoneInfo(settings.TIMEZONE)

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
env = templates.env


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(LOCAL_TZ)


def fmt_dt(dt: datetime | None) -> str:
    local = to_local(dt)
    if not local:
        return ""
    hour = local.hour % 12 or 12
    return f"{local.strftime('%b')} {local.day}, {local.year} {hour}:{local.strftime('%M %p')}"


def fmt_date(d: date | None) -> str:
    if not d:
        return ""
    return f"{d.strftime('%A, %B')} {d.day}, {d.year}"


env.filters["localdt"] = fmt_dt
env.filters["longdate"] = fmt_date
env.globals["CLAIM_STATUSES"] = CLAIM_STATUSES
env.globals["BASE_URL"] = settings.BASE_URL
