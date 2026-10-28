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


_HANDWRITING_SYSTEM_PROMPT = """\
You extract handwritten prescription regions.
Transcribe exactly what is written, letter by letter, including uncertainty marks (e.g. use '?' for unreadable characters like 'Metf?rm?n').
Return ONLY valid JSON matching the provided schema, with no extra text or markdown fences.

Rules:
1. Transcribe exactly what is written, letter by letter, including uncertainty marks.
2. Never expand abbreviations beyond what is visible (keep 'OD', 'TDS', 'tab', 'cap' as written).
3. Never replace an unclear word with a plausible drug name.
4. If two readings are plausible, return both as alternatives with individual confidences (up to 3 candidate readings).
5. Return legibility 'illegible' and text null when unreadable; never guess.
6. Do not use medical knowledge to "fix" or normalize a word.
7. Do not output patient identifiers (names, addresses, phone numbers).
8. Transcription-first: record verbatim characters in raw_transcription before any interpretation.
9. Output JSON only.
"""


def build_system_prompt() -> str:
    """Return the full system prompt with the Pydantic schema injected."""
    schema = json.dumps(Prescription.model_json_schema(), indent=2)
    return _BASE_SYSTEM_PROMPT + _SCHEMA_INTRO.format(schema=schema)


def build_handwriting_system_prompt() -> str:
    """Return the handwriting system prompt with the Medicine schema injected."""
    from dosely.models import Medicine
    schema = json.dumps(Medicine.model_json_schema(), indent=2)
    return _HANDWRITING_SYSTEM_PROMPT + _SCHEMA_INTRO.format(schema=schema)


def build_retry_prompt(validation_error: str) -> str:
    """
    Return a follow-up user message for the retry turn.

    This is sent as a *user* message (not a new system prompt) so the
    conversation context is preserved.
    """
    return _RETRY_PREFIX.format(error=validation_error)

