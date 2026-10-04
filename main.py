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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dosely.api.router import router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Dosely — Prescription Extraction API",
    description=(
        "Uploads a prescription image or PDF, extracts medicines with "
        "dosing schedules, and returns structured JSON validated against "
        "the Prescription Pydantic model."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"message": "Dosely API is running. Visit /docs for the API explorer."}
