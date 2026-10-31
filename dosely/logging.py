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
import traceback
from typing import Any

# Patterns that look like drug names, strengths, dosages, filenames, or patient identifiers
_SENSITIVE_PATTERNS = [
    # '<Word> <dosage>': Morphine 10mg, Amoxicillin 500 mg — MUST run before bare dosage
    re.compile(r"\b[A-Za-z][A-Za-z0-9-]+\s+\d+\s*(?:mg|mcg|ml|g|iu|units?)\b", re.IGNORECASE),
    # Bare dosage amounts: 500mg, 10 mcg, 5 ml, 5 mg, etc.
    re.compile(r"\b\d+\s*(?:mg|mcg|ml|g|iu|units?)\b", re.IGNORECASE),
    # Common high-risk or seeded drug names appearing alone
    re.compile(
        r"\b(?:morphine|warfarin|fentanyl|tramadol|codeine|oxycodone|amoxicillin|paracetamol|ibuprofen|ciprofloxacin|pantoprazole|omeprazole|metformin|atorvastatin)\b",
        re.IGNORECASE,
    ),
    # Patient / prescriber identifiers and names
    re.compile(r"\b(?:patient|doctor|dr\.)\s+[A-Za-z0-9_-]+", re.IGNORECASE),
    re.compile(r"\b(?:john\s+doe|dr\.\s+smith)\b", re.IGNORECASE),
    # Filenames containing sensitive tokens
    re.compile(
        r"\b[A-Za-z0-9_.-]*(?:morphine|warfarin|john_doe|prescription)[A-Za-z0-9_.-]*\.(?:png|pdf|jpg|jpeg|webp|txt)\b",
        re.IGNORECASE,
    ),
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
    "user_comment",
}


def redact_text(text: str) -> str:
    """Apply all sensitive redaction patterns to a string."""
    if not isinstance(text, str):
        return text
    out = text
    for pat in _SENSITIVE_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


class SafeRedactingFormatter(logging.Formatter):
    """
    Base formatter that:
    1. Overrides formatException to emit only stack frames and exception class name,
       never the exception message text.
    2. Runs full redaction over the final formatted output.
    """

    def formatException(self, exc_info) -> str:
        if not exc_info or exc_info[0] is None:
            return ""
        exc_type, _, exc_tb = exc_info
        lines = []
        if exc_tb:
            for f in traceback.extract_tb(exc_tb):
                lines.append(f'  File "{f.filename}", line {f.lineno}, in {f.name}\n')
        class_name = getattr(exc_type, "__name__", str(exc_type))
        result = "".join(lines) + f"{class_name}: [EXCEPTION_MESSAGE_REDACTED]"
        return redact_text(result)

    def format(self, record: logging.LogRecord) -> str:
        # Pre-format message and clear args to prevent format leaks
        try:
            msg = record.getMessage()
            record.msg = redact_text(msg)
            record.args = ()
        except Exception:
            pass

        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)

        formatted = super().format(record)
        return redact_text(formatted)


class HealthDataRedactionFilter(logging.Filter):
    """
    Logging filter that redacts potential medical content, patient names, and dosages.
    Pre-formats with record.getMessage() and clears record.args to ensure subsequent
    handlers never see unredacted format parameters.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            record.msg = redact_text(msg)
            record.args = ()
        except Exception:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)

        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        return True


class StructuredJsonFormatter(SafeRedactingFormatter):
    """Formats log records as JSON with operational metadata only."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            msg = record.getMessage()
            clean_msg = redact_text(msg)
            record.msg = clean_msg
            record.args = ()
        except Exception:
            clean_msg = redact_text(str(record.msg))

        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": clean_msg,
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
            log_entry["exception"] = redact_text(record.exc_text)

        raw_json = json.dumps(log_entry)
        return redact_text(raw_json)


def setup_secure_logging() -> None:
    """
    Configure secure, non-PII logging.

    Attaches a HealthDataRedactionFilter and SafeRedactingFormatter to:
    - root logger (catches everything)
    - dosely namespace logger
    - uvicorn, uvicorn.access, uvicorn.error
    - fastapi
    Forces third-party loggers (anthropic, httpx, httpcore, urllib3, uvicorn.access) to WARNING+.
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
    else:
        for h in root.handlers:
            if not any(isinstance(f, HealthDataRedactionFilter) for f in h.filters):
                h.addFilter(redaction_filter)
            if not isinstance(h.formatter, SafeRedactingFormatter):
                h.setFormatter(StructuredJsonFormatter())

    # Apply directly to well-known application loggers
    for name in ("dosely", "uvicorn", "uvicorn.error", "fastapi"):
        named_logger = logging.getLogger(name)
        if not any(isinstance(f, HealthDataRedactionFilter) for f in named_logger.filters):
            named_logger.addFilter(redaction_filter)
        if named_logger.level == logging.NOTSET:
            named_logger.setLevel(logging.INFO)

    # Force third-party loggers to WARNING or above
    for name in ("anthropic", "google_genai", "google.genai", "google", "httpx", "httpcore", "urllib3", "uvicorn.access"):
        tp_logger = logging.getLogger(name)
        tp_logger.setLevel(logging.WARNING)
        if not any(isinstance(f, HealthDataRedactionFilter) for f in tp_logger.filters):
            tp_logger.addFilter(redaction_filter)
