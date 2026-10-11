"""
Dosely — FastAPI Application Entry Point
==========================================
Run locally::

    uvicorn main:app --reload --port 8000

Or with gunicorn::

    gunicorn main:app -k uvicorn.workers.UvicornWorker -w 2 --bind 0.0.0.0:8000
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from dosely.api.prescriptions import limiter, router as prescriptions_router
from dosely.api.router import router as legacy_extract_router
from dosely.security.privacy_logger import configure_privacy_logging

# ---------------------------------------------------------------------------
# Logging & Privacy Filter
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
configure_privacy_logging()

# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Dosely — Prescription Assistant API",
    description=(
        "Prescription extraction, review & confirmation workflow, "
        "and medication schedule generator. Protected by JWT auth, "
        "at-rest image encryption, and HIPAA-strict privacy logging."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# SlowAPI Rate Limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS Middleware (allows frontend on Vite dev server or standard ports)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Routers
# Both root-level (/prescriptions) and prefixed (/api/v1/prescriptions)
app.include_router(prescriptions_router)
app.include_router(prescriptions_router, prefix="/api/v1")
app.include_router(legacy_extract_router)

# Mount frontend build directory if it exists
frontend_dist = Path(__file__).resolve().parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/app", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "message": "Dosely Prescription API is running.",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "dosely-api"}
