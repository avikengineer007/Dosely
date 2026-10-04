"""
Prescription Extractor
=======================
Orchestrates:
  1. Image preprocessing (deskew, grayscale, CLAHE)
  2. Claude API call (vision)
  3. JSON extraction from the response
  4. Pydantic validation
  5. Single automatic retry on validation failure
  6. Fallback to needs_human_review=True on second failure

Usage::

    from dosely.extractor import extract

    with open("rx.jpg", "rb") as f:
        result = await extract(f.read())

    print(result.model_dump_json(indent=2))

Environment variables
---------------------
ANTHROPIC_API_KEY   required  Your Anthropic API key
CLAUDE_MODEL        optional  Default: claude-3-5-sonnet-20241022
CLAUDE_MAX_TOKENS   optional  Default: 2048
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import uuid
from typing import Any

import anthropic
from pydantic import ValidationError

from dosely.models import ImageQuality, Prescription
from dosely.preprocessing import preprocess_and_assess
from dosely.prompts import build_retry_prompt, build_system_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CLAUDE_MODEL: str = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")
CLAUDE_MAX_TOKENS: int = int(os.getenv("CLAUDE_MAX_TOKENS", "2048"))

# Lazy singleton — created on first use so tests can monkey-patch env vars
_anthropic_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY environment variable is not set."
            )
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


# ---------------------------------------------------------------------------
# JSON extraction from model response
# ---------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```", re.IGNORECASE)


def _extract_json(text: str) -> str:
    """
    Extract a JSON string from the model's text response.

    Handles:
    - Bare JSON (ideal case — model followed the prompt)
    - JSON wrapped in markdown code fences
    """
    text = text.strip()
    fence_match = _JSON_FENCE_RE.search(text)
    if fence_match:
        return fence_match.group(1).strip()

    # Try to find raw JSON object
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start != -1 and brace_end > brace_start:
        return text[brace_start : brace_end + 1]

    # Return as-is and let json.loads raise a clear error
    return text


# ---------------------------------------------------------------------------
# Claude API helpers
# ---------------------------------------------------------------------------

def _build_image_content(png_bytes: bytes) -> dict[str, Any]:
    """Build an Anthropic vision content block from PNG bytes."""
    b64 = base64.standard_b64encode(png_bytes).decode()
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": b64,
        },
    }


def _call_claude(
    client: anthropic.Anthropic,
    messages: list[dict[str, Any]],
    system_prompt: str,
) -> str:
    """
    Make a single synchronous call to Claude and return the text response.
    """
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        system=system_prompt,
        messages=messages,  # type: ignore[arg-type]
    )
    first_block = response.content[0]
    return getattr(first_block, "text", str(first_block))


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def _parse_and_validate(raw_json: str, prescription_id: str) -> Prescription:
    """
    Parse raw JSON string and validate against the Prescription model.

    Injects `prescription_id` if the model didn't include one.
    Raises `ValidationError` on schema mismatch.
    """
    data: dict[str, Any] = json.loads(raw_json)

    # Inject ID if the model omitted it (common for LLMs)
    data.setdefault("prescription_id", prescription_id)

    return Prescription.model_validate(data)


# ---------------------------------------------------------------------------
# Fallback prescription on double failure
# ---------------------------------------------------------------------------

def _make_error_prescription(
    prescription_id: str,
    quality: str,
    error_msg: str,
) -> Prescription:
    """
    Return a minimal Prescription that marks the whole document for review.
    Called when both the initial attempt and the retry fail validation.
    """
    return Prescription(
        prescription_id=prescription_id,
        image_quality=ImageQuality(quality),
        medicines=[],
        needs_human_review=True,
        review_summary=[
            "Extraction failed after retry. Manual entry required.",
            f"Last error: {error_msg[:300]}",
        ],
    )


# ---------------------------------------------------------------------------
# Public extraction function
# ---------------------------------------------------------------------------

def extract(
    data: bytes,
    prescription_id: str | None = None,
    pdf_page: int = 0,
) -> Prescription:
    """
    Extract a structured Prescription from raw image or PDF bytes.

    Parameters
    ----------
    data:
        Raw bytes of a prescription image (JPEG, PNG, TIFF, BMP) or PDF.
    prescription_id:
        Optional stable ID for this extraction run. A UUID4 is generated
        when not provided.
    pdf_page:
        Zero-indexed page to extract when ``data`` is a PDF.

    Returns
    -------
    Prescription
        Always returns a valid Prescription object.
        On failure, ``needs_human_review`` is True and ``medicines`` is empty.
    """
    rx_id = prescription_id or str(uuid.uuid4())
    system_prompt = build_system_prompt()

    # ---------------------------------------------------------------
    # Step 1: Preprocess image
    # ---------------------------------------------------------------
    logger.info("[%s] Preprocessing image ...", rx_id)
    try:
        png_bytes, quality = preprocess_and_assess(data, pdf_page=pdf_page)
    except Exception as exc:
        logger.exception("[%s] Preprocessing failed: %s", rx_id, exc)
        return _make_error_prescription(rx_id, "poor", f"Preprocessing error: {exc}")

    logger.info("[%s] Image quality assessed as '%s'.", rx_id, quality)
    client = _get_client()

    # ---------------------------------------------------------------
    # Step 2: Initial Claude call
    # ---------------------------------------------------------------
    image_block = _build_image_content(png_bytes)
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                image_block,
                {
                    "type": "text",
                    "text": (
                        f"Extract the prescription data. "
                        f"Use prescription_id: \"{rx_id}\". "
                        f"The preprocessing pipeline assessed image_quality as \"{quality}\"; "
                        f"you may override this if you disagree."
                    ),
                },
            ],
        }
    ]

    logger.info("[%s] Calling Claude (%s) ...", rx_id, CLAUDE_MODEL)
    try:
        raw_response = _call_claude(client, messages, system_prompt)
        logger.debug("[%s] Raw response:\n%s", rx_id, raw_response[:500])
    except anthropic.APIError as exc:
        logger.exception("[%s] Claude API error: %s", rx_id, exc)
        return _make_error_prescription(rx_id, quality, f"API error: {exc}")

    # ---------------------------------------------------------------
    # Step 3: Parse + validate (attempt 1)
    # ---------------------------------------------------------------
    first_error_msg = ""
    try:
        json_str = _extract_json(raw_response)
        prescription = _parse_and_validate(json_str, rx_id)
        logger.info("[%s] Extraction succeeded on first attempt.", rx_id)
        return prescription

    except (json.JSONDecodeError, ValidationError) as exc:
        first_error_msg = str(exc)
        logger.warning(
            "[%s] First validation failed: %s. Retrying ...",
            rx_id,
            first_error_msg[:200],
        )

    # ---------------------------------------------------------------
    # Step 4: Retry — append assistant response + correction request
    # ---------------------------------------------------------------
    messages.append({"role": "assistant", "content": raw_response})
    messages.append(
        {
            "role": "user",
            "content": build_retry_prompt(first_error_msg),
        }
    )

    try:
        retry_response = _call_claude(client, messages, system_prompt)
        logger.debug("[%s] Retry response:\n%s", rx_id, retry_response[:500])
    except anthropic.APIError as exc:
        logger.exception("[%s] Claude API error on retry: %s", rx_id, exc)
        return _make_error_prescription(
            rx_id, quality, f"API error on retry: {exc}"
        )

    # ---------------------------------------------------------------
    # Step 5: Parse + validate (attempt 2)
    # ---------------------------------------------------------------
    try:
        json_str = _extract_json(retry_response)
        prescription = _parse_and_validate(json_str, rx_id)
        logger.info("[%s] Extraction succeeded on retry.", rx_id)
        return prescription

    except (json.JSONDecodeError, ValidationError) as second_error:
        logger.error(
            "[%s] Second validation failed: %s. Returning error prescription.",
            rx_id,
            str(second_error)[:200],
        )
        return _make_error_prescription(rx_id, quality, str(second_error))
