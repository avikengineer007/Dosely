"""
dosely/config.py
================
Application-wide configuration constants read from environment variables,
with safe defaults for development.

All sensitive settings (API keys, DB paths, encryption keys) must be supplied
via environment variables.  Never hard-code secrets here.
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Purge settings
# ---------------------------------------------------------------------------

#: Data retention period in days.  Prescriptions older than this are purged
#: by the background job and by POST /admin/purge.
PURGE_DAYS: int = int(os.environ.get("PURGE_DAYS", "30"))

#: How often the background purge job runs (in hours).
PURGE_INTERVAL_HOURS: float = float(os.environ.get("PURGE_INTERVAL_HOURS", "24"))

# ---------------------------------------------------------------------------
# JWT / Auth settings
# ---------------------------------------------------------------------------

JWT_SECRET_KEY: str = os.environ.get(
    "JWT_SECRET_KEY",
    "dosely-dev-secret-change-in-production",  # CHANGE in production
)
JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES: int = int(os.environ.get("JWT_EXPIRE_MINUTES", "60"))

# ---------------------------------------------------------------------------
# Storage settings
# ---------------------------------------------------------------------------

ENCRYPTED_UPLOADS_DIR: str = os.environ.get(
    "ENCRYPTED_UPLOADS_DIR", "data/encrypted_uploads"
)
DB_PATH: str = os.environ.get("DB_PATH", "data/prescriptions.db")
