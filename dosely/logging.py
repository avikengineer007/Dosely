"""
dosely/logging.py
=================
Structured logging with strict PII and health-data redaction.
Enforces Global Safety Rule 5: Never log prescription contents or patient identifiers.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

# Patterns that look like drug names with strengths or dosages
_SENSITIVE_PATTERNS = [
    # '<Word> <dosage>': Morphine 10mg, Amoxicillin 500 mg — MUST run before bare dosage
    re.compile(r"\b[A-Za-z][A-Za-z0-9-]+\s+\d+\s*(?:mg|mcg|ml|g|iu)\b", re.IGNORECASE),
    # Bare dosage amounts: 500mg, 10 mcg, 5 ml, etc.
    re.compile(r"\b\d+\s*(?:mg|mcg|ml|g|iu)\b", re.IGNORECASE),
    # Patient / prescriber identifiers
    re.compile(r"\b(?:patient|doctor|dr\.)\s+[A-Za-z]+", re.IGNORECASE),
]

_RESTRICTED_KEYS = {
    "name_raw",
    "name_generic",
    "patient_name",
    "prescriber",
    "raw_text",
    "medicines",
    "strength",
    "notes",
}


class HealthDataRedactionFilter(logging.Filter):
    """Logging filter that redacts potential medical content, patient names, and dosages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            for pat in _SENSITIVE_PATTERNS:
                record.msg = pat.sub("[REDACTED]", record.msg)
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Formats log records as JSON with operational metadata only."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include safe operational fields
        for field in ("prescription_id", "status_code", "latency_ms", "error_type"):
            if hasattr(record, field):
                log_entry[field] = getattr(record, field)

        # Redact any accidental structured fields
        for k in list(log_entry.keys()):
            if k in _RESTRICTED_KEYS:
                log_entry[k] = "[REDACTED]"

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            log_entry["exception"] = record.exc_text

        return json.dumps(log_entry)


def setup_secure_logging() -> None:
    """
    Configure secure, non-PII logging.

    Attaches a HealthDataRedactionFilter to:
    - root logger (catches everything)
    - dosely namespace logger
    - uvicorn, uvicorn.access, uvicorn.error
    - fastapi
    """
    redaction_filter = HealthDataRedactionFilter()

    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())
    handler.addFilter(redaction_filter)

    # Apply to root logger so all child loggers inherit it
    root = logging.getLogger()
    root.addFilter(redaction_filter)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)

    # Also apply directly to well-known loggers
    for name in ("dosely", "uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        named_logger = logging.getLogger(name)
        # Avoid duplicate filter instances
        if not any(isinstance(f, HealthDataRedactionFilter) for f in named_logger.filters):
            named_logger.addFilter(redaction_filter)
        if named_logger.level == logging.NOTSET:
            named_logger.setLevel(logging.INFO)
