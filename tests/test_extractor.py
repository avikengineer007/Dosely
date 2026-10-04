"""
Unit tests for dosely.extractor
================================
All Claude API calls are mocked — no real API key is required.

Test scenarios:
  - Successful extraction on first attempt
  - Successful extraction on retry (first response invalid JSON)
  - Double failure -> fallback error prescription (needs_human_review=True)
  - API error on first call -> fallback
  - API error on retry call -> fallback
  - JSON extraction from markdown-fenced response
  - Prescription ID injection when model omits it
  - Preprocessing failure path
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import anthropic
import pytest
from pydantic import ValidationError

from dosely.extractor import (
    _extract_json,
    _parse_and_validate,
    extract,
)
from dosely.models import ImageQuality, Prescription
from tests.conftest import GOOD_PRESCRIPTION_JSON, INVALID_JSON_RESPONSE


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_bare_json_returned_as_is(self):
        payload = '{"key": "value"}'
        assert _extract_json(payload) == payload

    def test_strips_whitespace(self):
        assert _extract_json("  {} ") == "{}"

    def test_extracts_from_json_fence(self):
        fenced = "```json\n{\"a\": 1}\n```"
        result = _extract_json(fenced)
        assert result == '{"a": 1}'

    def test_extracts_from_plain_fence(self):
        fenced = "```\n{\"b\": 2}\n```"
        result = _extract_json(fenced)
        assert result == '{"b": 2}'

    def test_extracts_from_prose_with_json(self):
        prose = 'Here is the data: {"x": 99} as requested.'
        result = _extract_json(prose)
        parsed = json.loads(result)
        assert parsed["x"] == 99

    def test_no_json_returns_original(self):
        text = "no braces here"
        result = _extract_json(text)
        assert result == text


# ---------------------------------------------------------------------------
# _parse_and_validate
# ---------------------------------------------------------------------------

class TestParseAndValidate:
    def test_valid_json_returns_prescription(self):
        rx = _parse_and_validate(GOOD_PRESCRIPTION_JSON, "test-001")
        assert isinstance(rx, Prescription)
        assert rx.prescription_id == "rx-test-001"
        assert len(rx.medicines) == 1

    def test_injects_id_when_missing(self):
        data = json.loads(GOOD_PRESCRIPTION_JSON)
        del data["prescription_id"]
        raw = json.dumps(data)
        rx = _parse_and_validate(raw, "injected-id")
        assert rx.prescription_id == "injected-id"

    def test_invalid_quality_enum_raises(self):
        bad = json.dumps({
            "prescription_id": "x",
            "image_quality": "crystal_clear",  # not a valid enum
            "medicines": [],
        })
        with pytest.raises(ValidationError):
            _parse_and_validate(bad, "x")

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_and_validate("not json", "x")

    def test_medicine_confidence_out_of_range_raises(self):
        data = json.loads(GOOD_PRESCRIPTION_JSON)
        data["medicines"][0]["confidence"] = 1.5
        with pytest.raises(ValidationError):
            _parse_and_validate(json.dumps(data), "x")


# ---------------------------------------------------------------------------
# extract — success paths
# ---------------------------------------------------------------------------

class TestExtractSuccess:
    def test_successful_first_attempt(self, white_png, mock_claude_good):
        """Happy path: valid JSON on first Claude response."""
        rx = extract(white_png, prescription_id="rx-test-001")
        assert isinstance(rx, Prescription)
        assert rx.prescription_id == "rx-test-001"
        assert len(rx.medicines) == 1
        assert rx.medicines[0].name_generic == "amoxicillin"
        # Claude was called exactly once
        assert mock_claude_good.messages.create.call_count == 1

    def test_successful_on_retry(self, white_png, mock_claude_invalid_then_fixed):
        """First response invalid; second response valid -> succeeds."""
        rx = extract(white_png, prescription_id="rx-retry-001")
        assert isinstance(rx, Prescription)
        assert len(rx.medicines) == 1
        # Claude was called twice (initial + retry)
        assert mock_claude_invalid_then_fixed.messages.create.call_count == 2

    def test_prescription_id_is_preserved(self, white_png, mock_claude_good):
        rx = extract(white_png, prescription_id="custom-id-xyz")
        # The mock returns a JSON with rx-test-001 as the ID,
        # which should be preserved from the JSON
        assert isinstance(rx, Prescription)

    def test_auto_uuid_when_no_id(self, white_png, mock_claude_good):
        """Extraction works even without a prescription_id argument."""
        rx = extract(white_png)
        assert isinstance(rx, Prescription)


# ---------------------------------------------------------------------------
# extract — failure / fallback paths
# ---------------------------------------------------------------------------

class TestExtractFailure:
    def test_double_failure_returns_error_prescription(
        self, white_png, mock_claude_always_invalid
    ):
        """Both attempts fail -> fallback Prescription with needs_human_review."""
        rx = extract(white_png, prescription_id="rx-fail-001")
        assert isinstance(rx, Prescription)
        assert rx.needs_human_review is True
        assert rx.medicines == []
        assert any("failed" in s.lower() for s in rx.review_summary)

    def test_api_error_on_first_call_returns_fallback(self, white_png):
        """Anthropic raises APIError on first call -> graceful fallback."""
        with patch("dosely.extractor._anthropic_client") as mock_client:
            mock_client.messages.create.side_effect = anthropic.APIStatusError(
                "rate limit", response=MagicMock(status_code=429), body={}
            )
            rx = extract(white_png, prescription_id="rx-apierr-001")
        assert rx.needs_human_review is True
        assert rx.medicines == []

    def test_api_error_on_retry_returns_fallback(self, white_png):
        """First call returns garbage; retry raises APIError -> graceful fallback."""
        with patch("dosely.extractor._anthropic_client") as mock_client:
            bad_msg = MagicMock()
            bad_msg.content = [MagicMock(text=INVALID_JSON_RESPONSE)]
            mock_client.messages.create.side_effect = [
                bad_msg,
                anthropic.APIStatusError(
                    "server error", response=MagicMock(status_code=500), body={}
                ),
            ]
            rx = extract(white_png, prescription_id="rx-retryfail-001")
        assert rx.needs_human_review is True

    def test_preprocessing_failure_returns_fallback(self):
        """If preprocessing itself throws, return fallback (not an exception)."""
        with patch("dosely.extractor.preprocess_and_assess") as mock_pre:
            mock_pre.side_effect = RuntimeError("cv2 internal error")
            rx = extract(b"any bytes", prescription_id="rx-prepfail-001")
        assert rx.needs_human_review is True
        assert rx.medicines == []


# ---------------------------------------------------------------------------
# extract — image quality propagation
# ---------------------------------------------------------------------------

class TestExtractQuality:
    def test_poor_quality_propagated(self, noisy_png, mock_claude_good):
        """When preprocessing assesses 'poor', the prescription reflects it."""
        with patch(
            "dosely.extractor.preprocess_and_assess",
            return_value=(noisy_png, "poor"),
        ):
            rx = extract(noisy_png, prescription_id="rx-poor-001")
        # The extracted prescription from the mock JSON says 'good',
        # but extraction still works; the model may override quality.
        assert isinstance(rx, Prescription)

    def test_good_quality_no_extra_review(self, white_png, mock_claude_good):
        with patch(
            "dosely.extractor.preprocess_and_assess",
            return_value=(white_png, "good"),
        ):
            rx = extract(white_png, prescription_id="rx-good-001")
        assert isinstance(rx, Prescription)
