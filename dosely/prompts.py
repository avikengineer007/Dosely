"""
System prompt and schema injection for the Claude extraction call.
"""

from __future__ import annotations

import json

from dosely.models import Prescription


# ---------------------------------------------------------------------------
# User-provided system prompt (verbatim from spec, with schema appended)
# ---------------------------------------------------------------------------

_BASE_SYSTEM_PROMPT = """\
You extract structured data from prescription images. \
Return ONLY valid JSON matching the provided schema, with no extra text.

Rules:
- Copy drug names exactly as written. Never correct, guess, or complete a \
name you cannot read; set name_raw to your best reading, confidence below \
0.6, and needs_review to true.
- Never invent a strength, frequency, or duration. If absent, use null.
- Keep shorthand as written in frequency_raw (e.g. "1-0-1", "BD", "TDS"); \
do not interpret it here.
- Set image_quality honestly. If the image is mostly unreadable, return an \
empty medicines list and image_quality "poor".
- Ignore patient identifiers; do not output names, phone numbers, or addresses.
"""

_SCHEMA_INTRO = """
---
Output schema (JSON Schema derived from Pydantic model):
{schema}
---
Return ONLY the JSON object. No markdown, no code fences, no commentary.
"""

_RETRY_PREFIX = """\
Your previous response failed Pydantic validation with the following error:

{error}

Please return a corrected JSON object that passes validation. \
Preserve all information you already extracted; only fix the schema errors.
"""


def build_system_prompt() -> str:
    """Return the full system prompt with the Pydantic schema injected."""
    schema = json.dumps(Prescription.model_json_schema(), indent=2)
    return _BASE_SYSTEM_PROMPT + _SCHEMA_INTRO.format(schema=schema)


def build_retry_prompt(validation_error: str) -> str:
    """
    Return a follow-up user message for the retry turn.

    This is sent as a *user* message (not a new system prompt) so the
    conversation context is preserved.
    """
    return _RETRY_PREFIX.format(error=validation_error)
