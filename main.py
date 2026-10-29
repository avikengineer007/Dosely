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
from urllib.parse import urlsplit
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

LANDING_PAGE_PATH = Path(__file__).resolve().parent / "dosely.html"
APP_PAGE_PATH = Path(__file__).resolve().parent / "frontend" / "app.html"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _find_file(rel_path: str) -> Path | None:
    """Find a project file across local dev, testing, and Vercel serverless locations."""
    candidates = [
        Path(__file__).resolve().parent / rel_path,
        Path(__file__).resolve().parent.parent / rel_path,
        Path.cwd() / rel_path,
        Path("/var/task") / rel_path,
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


def _find_dir(rel_path: str) -> Path | None:
    """Find a project directory across local dev, testing, and Vercel serverless locations."""
    candidates = [
        Path(__file__).resolve().parent / rel_path,
        Path(__file__).resolve().parent.parent / rel_path,
        Path.cwd() / rel_path,
        Path("/var/task") / rel_path,
    ]
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    return None


# Pre-cache HTML templates in memory at module load time for instantaneous serving
_LANDING_HTML: str = ""
_landing_candidate = _find_file("dosely.html")
if _landing_candidate and _landing_candidate.exists():
    try:
        _LANDING_HTML = _landing_candidate.read_text(encoding="utf-8")
    except Exception:
        pass

_APP_HTML: str = ""
_app_candidate = _find_file("frontend/app.html")
if _app_candidate and _app_candidate.exists():
    try:
        _APP_HTML = _app_candidate.read_text(encoding="utf-8")
    except Exception:
        pass


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Normalize Vercel serverless rewrite path (?__vc_path=..., x-forwarded-uri, etc.) before routing
        orig_scope_path = request.scope.get("path", "/")
        vc_path = request.query_params.get("__vc_path")
        fwd_uri = request.headers.get("x-forwarded-uri") or request.headers.get("x-matched-path")
        fwd_path = urlsplit(fwd_uri).path if fwd_uri else None

        ignored_targets = {
            "/api",
            "/api/",
            "api",
            "/api/index",
            "/api/index/",
            "/api/index.py",
            "/api/index.py/",
            "api/index",
            "api/index.py",
            "/index",
            "/index.py",
            "index",
            "index.py",
            "/main.py",
            "main.py",
            "/$1",
            "/:path*",
            "",
        }

        if vc_path and vc_path not in ignored_targets:
            clean_path = "/" + vc_path.lstrip("/")
        elif fwd_path and fwd_path not in ignored_targets:
            clean_path = "/" + fwd_path.lstrip("/")
        elif orig_scope_path not in ignored_targets:
            clean_path = orig_scope_path
        else:
            clean_path = "/"

        request.scope["path"] = clean_path
        request.scope["raw_path"] = clean_path.encode("utf-8")
        request.scope["root_path"] = ""
        if hasattr(request, "_url"):
            delattr(request, "_url")

        qs_parts = [
            p for p in request.scope.get("query_string", b"").split(b"&")
            if p and not p.startswith(b"__vc_path=")
        ]
        request.scope["query_string"] = b"&".join(qs_parts)

        response: Response = await call_next(request)
        path = request.scope.get("path", request.url.path)

        response.headers["X-Debug-Orig-Scope"] = str(orig_scope_path)
        response.headers["X-Debug-VC-Path"] = str(vc_path)
        response.headers["X-Debug-Fwd-Uri"] = str(fwd_uri)
        response.headers["X-Debug-Fwd-Path"] = str(fwd_path)
        response.headers["X-Debug-Clean-Path"] = str(clean_path)
        response.headers["X-Debug-Final-Path"] = str(path)

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
    redirect_slashes=False,
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
_resolved_static = _find_dir("static")
if _resolved_static and _resolved_static.exists():
    app.mount("/static", StaticFiles(directory=str(_resolved_static)), name="static")
    app.mount("/api/static", StaticFiles(directory=str(_resolved_static)), name="api_static")

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging

_api_logger = logging.getLogger("dosely.api")

@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and request.method == "GET":
        p = request.scope.get("path", "")
        # If request is not for an API endpoint (/api/v1/...), serve frontend landing/app
        if not p.startswith("/api/v1"):
            if "/app" in p or request.query_params.get("__vc_path") == "/app":
                return await app_page()
            return await landing_page()
    return Response(
        content=json.dumps({"detail": exc.detail}),
        status_code=exc.status_code,
        media_type="application/json",
        headers=exc.headers or {},
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
@app.get("/api", include_in_schema=False, response_class=HTMLResponse)
@app.get("/api/", include_in_schema=False, response_class=HTMLResponse)
@app.get("/api/index", include_in_schema=False, response_class=HTMLResponse)
@app.get("/api/index.py", include_in_schema=False, response_class=HTMLResponse)
@app.get("/index", include_in_schema=False, response_class=HTMLResponse)
@app.get("/index.py", include_in_schema=False, response_class=HTMLResponse)
@app.get("/main.py", include_in_schema=False, response_class=HTMLResponse)
async def landing_page() -> HTMLResponse:
    """Serve single-file static landing page with Three.js visualization."""
    landing_path = _find_file("dosely.html")
    if landing_path and landing_path.exists():
        content = landing_path.read_text(encoding="utf-8")
    elif _LANDING_HTML:
        content = _LANDING_HTML
    else:
        return HTMLResponse("<h1>Dosely Landing Page Not Found</h1>", status_code=404)
    return HTMLResponse(content=content, status_code=200)


@app.get("/app", include_in_schema=False, response_class=HTMLResponse)
@app.get("/app/{path:path}", include_in_schema=False, response_class=HTMLResponse)
@app.get("/api/app", include_in_schema=False, response_class=HTMLResponse)
@app.get("/api/app/{path:path}", include_in_schema=False, response_class=HTMLResponse)
async def app_page(path: str = "") -> HTMLResponse:
    """Serve the Dosely application shell with deep-link support."""
    app_path = _find_file("frontend/app.html")
    if app_path and app_path.exists():
        content = app_path.read_text(encoding="utf-8")
    elif _APP_HTML:
        content = _APP_HTML
    else:
        return HTMLResponse("<h1>Dosely App Not Found</h1>", status_code=404)
    return HTMLResponse(content=content, status_code=200)
