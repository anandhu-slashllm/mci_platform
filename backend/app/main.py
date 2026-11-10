import asyncio
import logging
import mimetypes
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import (
    admin,
    analytics,
    attendance,
    auth,
    client_orgs,
    client_portal,
    cron,
    exports,
    job_order_chat,
    job_orders,
    notifications,
    payroll,
    ta_contracts,
    ta_portal,
    timesheets,
    worker_app,
    worker_reports,
    workers,
)
from app.services.shift_reminders import reminder_loop
from app.services.dispatch_tasks import dispatch_tasks_loop
from app.services.recommendation_refresh import recommendation_refresh_loop


def _configure_logging() -> None:
    """Ensure app + uvicorn logs appear on stderr (INFO by default)."""
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setLevel(level)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(h)
    logging.captureWarnings(True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


_configure_logging()

settings = get_settings()
logger = logging.getLogger(__name__)

# python:slim often has no .js entry → FileResponse uses text/plain; browsers won't run ES modules.
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/manifest+json", ".webmanifest")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logger.info(
        "Supabase url=%s anon_key set=%s service_role set=%s jwt_secret set=%s; background_mode=%s; db_pool_mode=%s",
        s.supabase_url,
        bool(s.supabase_anon_key),
        bool(s.supabase_service_role_key),
        bool(s.supabase_jwt_secret),
        s.background_mode,
        s.db_pool_mode,
    )
    if s.background_mode != "loop":
        # Serverless: scheduled work arrives via /internal/cron/* instead of in-process loops.
        yield
        return
    stop = asyncio.Event()
    tasks = [
        asyncio.create_task(reminder_loop(stop)),
        asyncio.create_task(recommendation_refresh_loop(stop)),
        asyncio.create_task(dispatch_tasks_loop(stop)),
    ]
    yield
    stop.set()
    for t in tasks:
        t.cancel()
    for t in tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="MCI Workforce Platform API", version="0.1.0", lifespan=lifespan)


class StripApiPrefixMiddleware:
    """Serve every route under both `/x` and `/api/x`.

    On Vercel the function is mounted at /api/*; the Vite dev proxy also uses /api. Accepting the
    prefix here lets one frontend build (API base "/api") work on Vercel, Docker and dev alike.
    """

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path") or ""
            if path == "/api" or path.startswith("/api/"):
                scope = dict(scope)
                scope["path"] = path[4:] or "/"
                raw = scope.get("raw_path")
                if isinstance(raw, (bytes, bytearray)) and raw.startswith(b"/api"):
                    scope["raw_path"] = bytes(raw[4:]) or b"/"
        await self.app(scope, receive, send)


app.add_middleware(StripApiPrefixMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(workers.router)
app.include_router(client_orgs.router)
app.include_router(ta_contracts.router)
app.include_router(job_orders.router)
app.include_router(job_order_chat.router)
app.include_router(worker_app.router)
app.include_router(worker_reports.router)
app.include_router(attendance.router)
app.include_router(timesheets.router)
app.include_router(payroll.router)
app.include_router(exports.router)
app.include_router(analytics.router)
app.include_router(notifications.router)
app.include_router(client_portal.router)
app.include_router(ta_portal.router)
app.include_router(admin.router)
app.include_router(cron.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


def _install_frontend_spa(app: FastAPI) -> None:
    """Serve Vite build from ./static when present (Docker image). Dev: omit static, use Vite on :5173."""
    static = Path(__file__).resolve().parent.parent / "static"
    if not static.is_dir():
        logger.warning("No ./static directory — serving API only (use Vite on :5173 for UI).")
        return
    index = static / "index.html"
    if not index.is_file():
        logger.warning("No ./static/index.html — serving API only.")
        return
    root = static.resolve()
    logger.info("Serving frontend from %s", root)

    assets_dir = static / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="vite-assets")

    html_headers = {"Cache-Control": "no-cache"}

    @app.get("/")
    async def spa_index():
        return FileResponse(index, media_type="text/html", headers=html_headers)

    @app.get("/{full_path:path}")
    async def spa_or_file(full_path: str):
        if full_path.startswith("assets/"):
            raise HTTPException(status_code=404)
        target = (static / full_path).resolve()
        if not target.is_relative_to(root):
            raise HTTPException(status_code=404)
        if target.is_file():
            return FileResponse(target)
        return FileResponse(index, media_type="text/html", headers=html_headers)


_install_frontend_spa(app)
