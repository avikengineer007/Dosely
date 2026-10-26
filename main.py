"""
Dosely — FastAPI Application Entry Point
==========================================
Run locally::

    uvicorn main:app --reload --port 8000

Or with gunicorn::

    gunicorn main:app -k uvicorn.workers.UvicornWorker -w 2 --bind 0.0.0.0:8000
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Logging — MUST be first import so all subsequent loggers are filtered
# ---------------------------------------------------------------------------
from dosely.logging import setup_secure_logging
setup_secure_logging()

import contextlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dosely.api.prescriptions import get_repo, get_storage, router as prescriptions_router
from dosely.api.router import router
from dosely.background import purge_scheduler_context
from dosely.config import PURGE_DAYS, PURGE_INTERVAL_HOURS


from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

LANDING_PAGE_PATH = Path(__file__).resolve().parent / "dosely.html"
APP_PAGE_PATH = Path(__file__).resolve().parent / "frontend" / "app.html"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        path = request.url.path

        # Standard baseline security headers for all routes
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Content-Security-Policy:
        # Route "/" allows cdnjs (scripts), googleapis (styles), gstatic (fonts)
        if path == "/":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self'"
            )
        else:
            # Stricter CSP for /app and API routes
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'; "
                "font-src 'self'; "
                "img-src 'self' data:; "
                "connect-src 'self'"
            )
        return response


# ---------------------------------------------------------------------------
# Lifespan — starts/stops the background purge job
# ---------------------------------------------------------------------------

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: register background jobs on startup, stop on shutdown."""
    repo = get_repo()
    storage = get_storage()
    async with purge_scheduler_context(
        repo=repo,
        storage=storage,
        days=PURGE_DAYS,
        interval_hours=PURGE_INTERVAL_HOURS,
    ):
        yield


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Dosely — Prescription Assistant API",
    description=(
        "Prescription assistant application: upload, confirm, match, and schedule medicines."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(prescriptions_router)
app.include_router(router)


@app.get("/", include_in_schema=False, response_class=HTMLResponse)
async def landing_page() -> HTMLResponse:
    """Serve single-file static landing page with Three.js visualization."""
    if not LANDING_PAGE_PATH.exists():
        return HTMLResponse("<h1>Dosely Landing Page Not Found</h1>", status_code=404)
    content = LANDING_PAGE_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content=content, status_code=200)


@app.get("/app", include_in_schema=False, response_class=HTMLResponse)
@app.get("/app/{path:path}", include_in_schema=False, response_class=HTMLResponse)
async def app_page(path: str = "") -> HTMLResponse:
    """Serve the Dosely application shell with deep-link support."""
    if not APP_PAGE_PATH.exists():
        return HTMLResponse("<h1>Dosely App Not Found</h1>", status_code=404)
    content = APP_PAGE_PATH.read_text(encoding="utf-8")
    return HTMLResponse(content=content, status_code=200)
