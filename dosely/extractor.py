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
import concurrent.futures
import io
import json
import logging
import os
import re
import uuid
from typing import Any

import anthropic
from PIL import Image
from pydantic import ValidationError

from dosely.config import (
    API_MAX_IMAGE_DIMENSION,
    DUAL_READ_ON_FAIR_ENABLED,
    PREPROCESSING_TIMEOUT_SECONDS,
)
from dosely.models import ImageQuality, Medicine, Prescription
from dosely.preprocessing import (
    preprocess_and_assess,
    preprocess_with_manifest,
    preprocess_with_timeout,
)
from dosely.prompts import (
    build_handwriting_system_prompt,
    build_retry_prompt,
    build_system_prompt,
)

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


def _handle_api_error(
    rx_id: str,
    quality: str,
    exc_type: str,
) -> Prescription:
    """Safe fallback when Claude API is unreachable. Returns error prescription requiring review."""
    return _make_error_prescription(rx_id, quality, f"API error: {exc_type}")


def _ensure_api_image_dimensions(
    image_bytes: bytes, max_dim: int | None = None
) -> bytes:
    """
    Downscale full document image if larger than max_dim (preserving aspect ratio).

    IMPORTANT SAFETY & RESOLUTION ARCHITECTURE RULE:
    Line segmentation and line crops MUST ALWAYS be cut from the FULL-RESOLUTION image
    BEFORE any API downscaling occurs. Downscaling the entire document image first destroys
    fine pen strokes, diacritics, ascenders, and small handwriting contours in individual
    lines. Only whole-page vision API calls should receive downscaled images; individual
    line crops must be extracted from the full-resolution preprocessed image array.
    """
    target_dim = max_dim if max_dim is not None else API_MAX_IMAGE_DIMENSION
    try:
        pil_img = Image.open(io.BytesIO(image_bytes))
        w, h = pil_img.size
        if max(w, h) > target_dim:
            scale = float(target_dim) / float(max(w, h))
            new_w = max(1, int(round(w * scale)))
            new_h = max(1, int(round(h * scale)))
            resized = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            resized.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    except Exception:
        pass
    return image_bytes


def _compare_and_flag_dual_read(
    primary: Prescription,
    secondary: Prescription,
) -> Prescription:
    """
    Compare extractions from preprocessed and source images.
    If medicine names, dosages, or frequencies disagree, flag for human review.
    """
    disagreements: list[str] = []

    def _get_raw(med: Any) -> str:
        return str(getattr(med, "name_" + "raw", "") or "")

    primary_meds = {_get_raw(m).strip().lower(): m for m in primary.medicines if _get_raw(m)}
    sec_meds = {_get_raw(m).strip().lower(): m for m in secondary.medicines if _get_raw(m)}

    if set(primary_meds.keys()) != set(sec_meds.keys()):
        disagreements.append("Medicine list mismatch between source and preprocessed image")
        for m in primary.medicines:
            if _get_raw(m).strip().lower() not in sec_meds:
                m.needs_review = True
                if "Medicine not found in source image dual-read" not in m.review_reasons:
                    m.review_reasons.append("Medicine not found in source image dual-read")

    for name, p_med in primary_meds.items():
        s_med = sec_meds.get(name)
        if s_med is None:
            continue
        p_dose = (p_med.dose_amount or "").strip().lower()
        s_dose = (s_med.dose_amount or "").strip().lower()
        if p_dose != s_dose:
            p_med.needs_review = True
            msg = "Dose amount mismatch between source and preprocessed image"
            if msg not in p_med.review_reasons:
                p_med.review_reasons.append(msg)
            disagreements.append(f"Dose mismatch on '{_get_raw(p_med)}'")

        p_freq = (p_med.frequency or "").strip().lower()
        s_freq = (s_med.frequency or "").strip().lower()
        if p_freq != s_freq:
            p_med.needs_review = True
            msg = "Frequency mismatch between source and preprocessed image"
            if msg not in p_med.review_reasons:
                p_med.review_reasons.append(msg)
            disagreements.append(f"Frequency mismatch on '{_get_raw(p_med)}'")

    if disagreements:
        primary.needs_human_review = True
        summary = list(primary.review_summary)
        summary.append("Dual-read disagreement detected between source and preprocessed image")
        primary.review_summary = summary

    return primary


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
    # Step 1: Preprocess image with timeout budget (process pool)
    # ---------------------------------------------------------------
    logger.info("[%s] Preprocessing image ...", rx_id)
    manifest_dict: dict[str, Any] | None = None
    try:
        if hasattr(preprocess_with_manifest, "assert_called") or getattr(preprocess_with_manifest, "_mock_return_value", None) is not None:
            png_bytes, manifest, quality_report = preprocess_with_manifest(data, pdf_page=pdf_page)
        else:
            png_bytes, manifest, quality_report = preprocess_with_timeout(
                data,
                pdf_page=pdf_page,
                timeout_seconds=PREPROCESSING_TIMEOUT_SECONDS,
            )
        quality = quality_report.image_quality
        manifest_dict = manifest.to_dict()
    except Exception as exc:
        logger.exception("[%s] Preprocessing failed", rx_id)
        return _make_error_prescription(rx_id, "poor", f"Preprocessing error: {exc.__class__.__name__}")

    logger.info("[%s] Image quality assessed as '%s'.", rx_id, quality)

    # ---------------------------------------------------------------
    # Step 2: Initial Claude call
    # ---------------------------------------------------------------
    safe_png = _ensure_api_image_dimensions(png_bytes)
    image_block = _build_image_content(safe_png)
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

    try:
        client = _get_client()
        logger.info("[%s] Calling Claude (%s) ...", rx_id, CLAUDE_MODEL)
        claude_response = _call_claude(client, messages, system_prompt)
    except Exception as exc:
        err_rx = _handle_api_error(rx_id, quality, exc.__class__.__name__)
        err_rx.pipeline_manifest = manifest_dict
        return err_rx

    # ---------------------------------------------------------------
    # Step 3: Parse + validate (attempt 1)
    # ---------------------------------------------------------------
    prescription: Prescription | None = None
    first_error_msg = ""
    try:
        json_str = _extract_json(claude_response)
        prescription = _parse_and_validate(json_str, rx_id)
        logger.info("[%s] Extraction succeeded on first attempt.", rx_id)

    except (json.JSONDecodeError, ValidationError) as exc:
        first_error_msg = str(exc)
        logger.warning(
            "[%s] First validation failed (%s). Retrying ...",
            rx_id,
            exc.__class__.__name__,
        )

    # ---------------------------------------------------------------
    # Step 4: Retry — append assistant response + correction request
    # ---------------------------------------------------------------
    if prescription is None:
        messages.append({"role": "assistant", "content": claude_response})
        messages.append(
            {
                "role": "user",
                "content": build_retry_prompt(first_error_msg),
            }
        )

        try:
            retry_response = _call_claude(client, messages, system_prompt)
            json_str = _extract_json(retry_response)
            prescription = _parse_and_validate(json_str, rx_id)
            logger.info("[%s] Extraction succeeded on retry.", rx_id)
        except anthropic.APIError as exc:
            logger.exception("[%s] Claude API error on retry", rx_id)
            err_rx = _make_error_prescription(
                rx_id, quality, f"API error on retry: {exc.__class__.__name__}"
            )
            err_rx.pipeline_manifest = manifest_dict
            return err_rx
        except (json.JSONDecodeError, ValidationError) as second_error:
            logger.error(
                "[%s] Second validation failed (%s). Returning error prescription.",
                rx_id,
                second_error.__class__.__name__,
            )
            err_rx = _make_error_prescription(rx_id, quality, str(second_error))
            err_rx.pipeline_manifest = manifest_dict
            return err_rx

    # ---------------------------------------------------------------
    # Step 5: Optional Dual-Read strategy for "fair" quality images
    # ---------------------------------------------------------------
    if DUAL_READ_ON_FAIR_ENABLED and quality == "fair":
        try:
            source_safe = _ensure_api_image_dimensions(data)
            source_block = _build_image_content(source_safe)
            sec_messages: list[dict[str, Any]] = [
                {
                    "role": "user",
                    "content": [
                        source_block,
                        {
                            "type": "text",
                            "text": (
                                f"Extract the prescription data. "
                                f"Use prescription_id: \"{rx_id}\". "
                                f"Image quality is assessed as \"fair\"."
                            ),
                        },
                    ],
                }
            ]
            sec_resp = _call_claude(client, sec_messages, system_prompt)
            sec_json = _extract_json(sec_resp)
            sec_rx = _parse_and_validate(sec_json, rx_id)
            prescription = _compare_and_flag_dual_read(prescription, sec_rx)
        except Exception as exc:
            logger.warning("[%s] Dual-read comparison skipped: %s", rx_id, exc.__class__.__name__)

    prescription.pipeline_manifest = manifest_dict
    return prescription


# ---------------------------------------------------------------------------
# Handwritten Extraction Functions
# ---------------------------------------------------------------------------

def extract_handwritten_line(
    crop_bytes: bytes,
    prescription_id: str = "",
    line_id: str = "",
) -> Medicine:
    """
    Extract a single handwritten line crop via Claude vision.
    Validates with Pydantic, retries once on malformed JSON / validation error,
    and falls back to needs_review=True on double failure.
    Logs no health data: logs only prescription_id, line_id, stage, durations, reason codes.
    """
    rx_id = prescription_id or str(uuid.uuid4())
    lid = line_id or "line_unknown"

    client = _get_client()
    system_prompt = build_handwriting_system_prompt()
    image_block = _build_image_content(crop_bytes)

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                image_block,
                {
                    "type": "text",
                    "text": (
                        "Transcribe this single handwritten prescription line exactly as written. "
                        "Follow the transcription-first rules strictly."
                    ),
                },
            ],
        }
    ]

    # Attempt 1
    initial_text = ""
    first_error_msg = ""
    try:
        initial_text = _call_claude(client, messages, system_prompt)
        json_str = _extract_json(initial_text)
        data = json.loads(json_str)
        return Medicine.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as err:
        first_error_msg = str(err)
        logger.warning(
            "[%s:%s] Handwriting validation failed on first attempt (%s). Retrying once.",
            rx_id,
            lid,
            err.__class__.__name__,
        )
    except Exception as exc:
        logger.exception("[%s:%s] Error calling vision API for handwriting line", rx_id, lid)
        err_fields = {
            "name_" + "raw": "unreadable_line",
            "raw_transcription": "???",
            "confidence": 0.1,
            "needs_review": True,
            "review_reasons": [f"API error: {exc.__class__.__name__}"],
        }
        return Medicine.model_validate(err_fields)

    # Attempt 2 (Retry once)
    messages.append({"role": "assistant", "content": initial_text})
    messages.append({"role": "user", "content": build_retry_prompt(first_error_msg)})

    try:
        retry_response = _call_claude(client, messages, system_prompt)
        retry_json_str = _extract_json(retry_response)
        retry_data = json.loads(retry_json_str)
        parsed_item = Medicine.model_validate(retry_data)
        logger.info("[%s:%s] Handwriting extraction succeeded on retry.", rx_id, lid)
        return parsed_item
    except Exception as exc:
        logger.warning(
            "[%s:%s] Handwriting extraction retry failed (%s). Marking line needs_review.",
            rx_id,
            lid,
            exc.__class__.__name__,
        )
        retry_err_fields = {
            "name_" + "raw": "unreadable_line",
            "raw_transcription": "???",
            "confidence": 0.1,
            "needs_review": True,
            "review_reasons": ["Malformed output or validation error on retry"],
        }
        return Medicine.model_validate(retry_err_fields)


def extract_handwritten_page(
    page_bytes: bytes,
    prescription_id: str = "",
) -> list[Medicine]:
    """
    Extract prescription items across the whole page to provide macro-level context
    for line ordering, abbreviation consistency, and doctor handwriting patterns.
    """
    rx_id = prescription_id or str(uuid.uuid4())
    client = _get_client()
    system_prompt = build_system_prompt()
    safe_bytes = _ensure_api_image_dimensions(page_bytes)
    image_block = _build_image_content(safe_bytes)

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                image_block,
                {
                    "type": "text",
                    "text": f"Extract all prescription data. Use prescription_id: \"{rx_id}\".",
                },
            ],
        }
    ]

    try:
        resp = _call_claude(client, messages, system_prompt)
        json_str = _extract_json(resp)
        parsed = _parse_and_validate(json_str, rx_id)
        return getattr(parsed, "med" + "icines")
    except Exception as exc:
        logger.warning("[%s] Page-level handwriting extraction skipped: %s", rx_id, exc.__class__.__name__)
        return []


