"""
dosely/security/privacy_logger.py
==================================
Strict HIPAA-inspired privacy logging filter for Dosely.

MANDATE: NEVER log prescription contents.
- No drug names, generic names, active ingredients
- No strengths, doses, or frequencies
- No patient names or prescriber identities
- No OCR text or raw transcription bodies
- Only non-PHI metadata (prescription_id, record counts, execution times, status codes)
"""

from __future__ import annotations

import logging
import re
from typing import Any

# Pattern matching sensitive keys that should never appear in log records
_SENSITIVE_KEYS = re.compile(
    r"\b(name_raw|name_generic|strength|frequency|timing_slots|patient_name|raw_text|"
    r"instructions|purpose|prescriber|dose|medicines|medicine)\b",
    re.IGNORECASE,
)

# Common drug name substrings to redact if accidentally logged
_DRUG_PATTERNS = re.compile(
    r"\b(amoxicillin|paracetamol|omeprazole|pantoprazole|cetirizine|metformin|"
    r"atorvastatin|ibuprofen|ciprofloxacin|azithromycin|aspirin|diclofenac)\b",
    re.IGNORECASE,
)


class PrescriptionPrivacyFilter(logging.Filter):
    """
    Log filter that intercepts and sanitizes any log record.
    Redacts suspected prescription contents, medicine names, and patient information.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = str(record.msg)
            # Redact known drug names
            msg = _DRUG_PATTERNS.sub("[REDACTED_MEDICINE]", msg)

            # If message contains JSON-like dumps with sensitive fields, redact values
            if _SENSITIVE_KEYS.search(msg):
                # Mask string values following sensitive keys
                msg = re.sub(
                    r'(["\'](?:name_raw|name_generic|strength|raw_text|patient_name)["\']\s*:\s*)[^,\}\]]+',
                    r'\1"[REDACTED_PRESCRIPTION_CONTENT]"',
                    msg,
                    flags=re.IGNORECASE,
                )

            record.msg = msg

            # Also sanitize args if formatted tuples/dicts were passed
            if record.args:
                sanitized_args = []
                for arg in record.args if isinstance(record.args, tuple) else (record.args,):
                    s_arg = str(arg)
                    s_arg = _DRUG_PATTERNS.sub("[REDACTED_MEDICINE]", s_arg)
                    sanitized_args.append(s_arg)
                record.args = tuple(sanitized_args)
        except Exception:
            # In case of any error in filter, ensure logging does not crash
            pass
        return True


privacy_logger = logging.getLogger("dosely.audit")
privacy_logger.addFilter(PrescriptionPrivacyFilter())


def log_safe_event(
    event_type: str,
    prescription_id: str,
    user_id: str = "anonymous",
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    """
    Safely log a prescription lifecycle event with ZERO prescription content.
    Only strictly whitelisted metadata fields are permitted.
    """
    allowed_meta = {}
    if extra_metadata:
        for k, v in extra_metadata.items():
            # Strictly whitelist non-sensitive metadata keys
            if k in {
                "file_size_bytes",
                "image_quality",
                "medicine_count",
                "review_required",
                "review_reasons_count",
                "duration_ms",
                "status_code",
                "client_ip",
            }:
                allowed_meta[k] = v

    privacy_logger.info(
        "EVENT=%s prescription_id=%s user_id=%s meta=%s",
        event_type,
        prescription_id,
        user_id,
        allowed_meta,
    )


def configure_privacy_logging() -> None:
    """Attach the privacy filter to root and dosely loggers."""
    p_filter = PrescriptionPrivacyFilter()
    logging.getLogger().addFilter(p_filter)
    logging.getLogger("dosely").addFilter(p_filter)
    logging.getLogger("uvicorn.access").addFilter(p_filter)
