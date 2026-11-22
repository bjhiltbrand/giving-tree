from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import scheduler
from .config import settings
from .db import Base, SessionLocal, engine
from .models import AdminUser
from .routes_admin import router as admin_router
from .routes_public import router as public_router
from .security import hash_password
from .web import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("giving_tree")


def bootstrap() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        if db.query(AdminUser).count() == 0:
            password = settings.ADMIN_PASSWORD
            if not password:
                password = secrets.token_urlsafe(12)
                log.warning("=" * 70)
                log.warning("No ADMIN_PASSWORD set. Created admin user '%s' with password: %s",
                            settings.ADMIN_USERNAME, password)
                log.warning("Change it under Admin > Admins after logging in.")
                log.warning("=" * 70)
            db.add(AdminUser(username=settings.ADMIN_USERNAME, password_hash=hash_password(password)))
            db.commit()
            log.info("Created admin user '%s'", settings.ADMIN_USERNAME)
    finally:
        db.close()
    if settings.SECRET_KEY_IS_EPHEMERAL:
        log.warning("SECRET_KEY is not set; sessions will reset on every restart. Set it in .env.")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap()
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="gt_session",
    max_age=60 * 60 * 24 * 60,
    same_site="lax",
    https_only=settings.SECURE_COOKIES,
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(public_router)
app.include_router(admin_router)


@app.get("/healthz")
def healthz():
    return JSONResponse({"ok": True})


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    if exc.status_code in (301, 302, 303, 307) and exc.headers:
        from fastapi.responses import RedirectResponse

        return RedirectResponse(exc.headers["Location"], status_code=303)
    db = SessionLocal()
    try:
        return render(request, db, "error.html", {"status": exc.status_code, "detail": exc.detail},
                      status_code=exc.status_code)
    finally:
        db.close()
