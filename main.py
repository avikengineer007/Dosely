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
import hmac
import json
import os

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from dosely.api.prescriptions import get_repo, get_storage, router as prescriptions_router
from dosely.api.router import router
from dosely.background import purge_scheduler_context, run_purge_job
from dosely.cloud_db import is_vercel
from dosely.config import PURGE_DAYS, PURGE_INTERVAL_HOURS


from pathlib import Path
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

LANDING_PAGE_PATH = Path(__file__).resolve().parent / "dosely.html"
APP_PAGE_PATH = Path(__file__).resolve().parent / "frontend" / "app.html"
STATIC_DIR = Path(__file__).resolve().parent / "static"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Normalize Vercel serverless rewrite path (?__vc_path=...) before routing
        orig_scope_path = request.scope.get("path", "/")
        vc_path = request.query_params.get("__vc_path")
        ignored_targets = ("/main.py", "main.py", "/api/index", "/api/index.py", "api/index", "api/index.py", "/$1", "/:path*")
        if vc_path and vc_path not in ignored_targets:
            clean_path = "/" + vc_path.lstrip("/")
            request.scope["path"] = clean_path
            request.scope["raw_path"] = clean_path.encode("utf-8")
            qs_parts = [
                p for p in request.scope.get("query_string", b"").split(b"&")
                if p and not p.startswith(b"__vc_path=")
            ]
            request.scope["query_string"] = b"&".join(qs_parts)
        elif orig_scope_path in ignored_targets:
            request.scope["path"] = "/"
            request.scope["raw_path"] = b"/"

        response: Response = await call_next(request)
        path = request.scope.get("path", request.url.path)

        response.headers["X-Debug-Orig-Path"] = str(orig_scope_path)
        response.headers["X-Debug-Scope-Path"] = str(request.scope.get("path"))
        response.headers["X-Debug-VC-Path"] = str(vc_path)
        response.headers["X-Debug-Raw-Path"] = str(request.scope.get("raw_path"))
        response.headers["X-Debug-Query"] = str(request.scope.get("query_string"))

        # Standard baseline security headers for all routes
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Permissions-Policy:
        # App route allows camera for prescription upload; other routes deny all sensitive capabilities
        if path.startswith("/app"):
            response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=(), payment=()"
        else:
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"

        # Content-Security-Policy:
        # Route "/" loads local same-origin three.min.js with 'self' (no cdnjs.cloudflare.com)
        if path == "/" or path.startswith("/static"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
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
                "img-src 'self' data: blob:; "
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
    if is_vercel():
        # On Vercel Serverless, periodic purges are triggered by Vercel Cron (/api/cron/purge)
        yield
        return
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

from starlette.staticfiles import StaticFiles
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging

_api_logger = logging.getLogger("dosely.api")

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return Response(
            content=json.dumps({
                "detail": "Not Found",
                "scope_path": str(request.scope.get("path")),
                "root_path": str(request.scope.get("root_path")),
                "url_path": str(request.url.path),
                "query": str(request.scope.get("query_string")),
                "vc_path": str(request.query_params.get("__vc_path")),
            }),
            status_code=404,
            media_type="application/json",
        )
    return Response(
        content=json.dumps({"detail": exc.detail}),
        status_code=exc.status_code,
        media_type="application/json",
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Sanitized 422 handler:
    Logs and returns only error loc and type.
    Never includes msg, input, or ctx which could leak submitted health data or PII.
    """
    clean_errors = []
    for err in exc.errors():
        clean_errors.append({
            "loc": list(err.get("loc", [])),
            "type": str(err.get("type", "validation_error")),
        })
    _api_logger.warning("Validation error on %s: %s", request.url.path, clean_errors)
    return Response(
        content=json.dumps({"detail": clean_errors}),
        status_code=422,
        media_type="application/json",
    )


@app.get("/api/cron/purge", include_in_schema=False)
async def vercel_cron_purge(request: Request) -> dict[str, int]:
    """
    Vercel Cron Job endpoint for daily data retention purge.
    Verifies Authorization: Bearer <CRON_SECRET>.
    """
    cron_secret = os.getenv("CRON_SECRET", "").strip()
    auth_header = request.headers.get("Authorization", "")
    expected = f"Bearer {cron_secret}"
    if not cron_secret or not hmac.compare_digest(auth_header.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized cron invocation.",
        )
    purged = run_purge_job(repo=get_repo(), storage=get_storage(), days=PURGE_DAYS)
    return {
        "purged_count": purged,
        "retention_days": PURGE_DAYS,
    }


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
