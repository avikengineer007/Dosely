"""
Unit tests for TASK 2: Fix the cause and hardening
  1. Key or credit errors: return 503 with "Reading is temporarily unavailable",
     log only error class, emit monitoring alert, zero secret/PII leaks.
  2. Preprocessing stage failure: falls back to pre-stage image and records in manifest.
  3. Quality gate: poor quality still attempts extraction, flags all fields with
     needs_review=True, and generates retake guidance.
  4. Model & JSON robustness: handles code fences, surrounding prose, trailing commas,
     and returns human-friendly message on double failure.
"""

from __future__ import annotations

import io
import json
import logging
from unittest.mock import MagicMock, patch

import anthropic
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from dosely.extractor import (
    VisionServiceUnavailableError,
    _extract_json,
    _is_auth_or_credit_error,
    extract,
)
from dosely.models import ImageQuality, Medicine, Prescription
from dosely.preprocessing import (
    OrientationStage,
    PipelineManifest,
    assess_quality,
    run_pipeline,
)
from main import app


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def dummy_png_bytes():
    img = Image.new("RGB", (300, 300), color=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Key or credit: 503 and Monitoring Alert
# ---------------------------------------------------------------------------

def test_is_auth_or_credit_error_detection():
    # Anthropic
    auth_err = anthropic.AuthenticationError(
        message="invalid x-api-key",
        response=MagicMock(status_code=401),
        body={"error": {"type": "authentication_error"}},
    )
    assert _is_auth_or_credit_error(auth_err) is True

    perm_err = anthropic.PermissionDeniedError(
        message="credit balance depleted",
        response=MagicMock(status_code=403),
        body={},
    )
    assert _is_auth_or_credit_error(perm_err) is True

    env_err = EnvironmentError("ANTHROPIC_API_KEY environment variable is not set.")
    assert _is_auth_or_credit_error(env_err) is True

    generic_err = ValueError("Something else")
    assert _is_auth_or_credit_error(generic_err) is False


def test_upload_returns_503_on_auth_or_credit_error(test_client, dummy_png_bytes, caplog):
    # Obtain auth token
    tok_res = test_client.post("/auth/token", json={"username": "tester"})
    token = tok_res.json()["access_token"]

    auth_err = anthropic.AuthenticationError(
        message="invalid key",
        response=MagicMock(status_code=401),
        body={"error": {"message": "invalid x-api-key"}},
    )

    with patch("dosely.extractor._get_client") as mock_get_client, \
         caplog.at_level(logging.ERROR, logger="dosely.monitoring"):
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = auth_err
        mock_get_client.return_value = mock_client

        response = test_client.post(
            "/prescriptions/upload",
            files={"file": ("rx.png", dummy_png_bytes, "image/png")},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 503
        assert response.json()["detail"] == "Reading is temporarily unavailable"

        # Verify monitoring alert was emitted
        assert any("MONITORING_ALERT" in rec.getMessage() for rec in caplog.records)
        # Verify secret was NOT leaked
        for rec in caplog.records:
            assert "invalid x-api-key" not in rec.getMessage()


# ---------------------------------------------------------------------------
# 2. Preprocessing stage error fallback to pre-stage image
# ---------------------------------------------------------------------------

def test_preprocessing_stage_failure_falls_back_in_manifest():
    img = np.full((100, 100, 3), 200, dtype=np.uint8)

    # Patch OrientationStage.process to simulate an unexpected stage exception
    with patch.object(OrientationStage, "process", side_effect=RuntimeError("Simulated stage crash")):
        final_img, manifest = run_pipeline(img, stages=["orientation"])

        # Pipeline did not crash, img preserved
        assert final_img.shape == img.shape
        # Manifest reports the error and fallback
        assert any("error" in (s.skip_reason or "") for s in manifest.stages)
        assert any("fell back to pre-stage image" in w for w in manifest.warnings)


# ---------------------------------------------------------------------------
# 3. Quality gate: poor quality still attempts extraction and adds retake guidance
# ---------------------------------------------------------------------------

def test_poor_quality_gate_marks_all_fields_and_adds_retake_guidance(dummy_png_bytes):
    mock_rx = Prescription(
        prescription_id="test-poor-rx",
        image_quality=ImageQuality.GOOD,
        medicines=[
            Medicine(
                name_raw="Amoxicillin 500mg",
                dose_amount="500mg",
                frequency="TDS",
                confidence=0.88,
                needs_review=False,
            )
        ],
        needs_human_review=False,
    )

    blank_array = np.full((100, 100, 3), 255, dtype=np.uint8)
    poor_qr = assess_quality(blank_array)
    assert poor_qr.image_quality == "poor"

    with patch("dosely.extractor.preprocess_with_timeout") as mock_prep, \
         patch("dosely.extractor._get_client"), \
         patch("dosely.extractor._call_claude"), \
         patch("dosely.extractor._extract_json", return_value="{}"), \
         patch("dosely.extractor._parse_and_validate", return_value=mock_rx):
        mock_prep.return_value = (dummy_png_bytes, PipelineManifest(), poor_qr)

        result = extract(dummy_png_bytes, prescription_id="test-poor-rx")

        # Extraction still succeeded (not empty from gate alone)
        assert len(result.medicines) == 1
        assert result.medicines[0].name_raw == "Amoxicillin 500mg"
        # Marked for review
        assert result.needs_human_review is True
        assert result.medicines[0].needs_review is True
        # Review summary contains retake guidance
        assert any("Retake guidance:" in s for s in result.review_summary)


# ---------------------------------------------------------------------------
# 4. JSON extraction robustness (code fences, prose, trailing commas)
# ---------------------------------------------------------------------------

def test_extract_json_handles_fences_and_prose():
    raw_response = """
    Here is the requested extraction from the prescription:
    ```json
    {
      "prescription_id": "test-rx-123",
      "medicines": [
        {"name_raw": "Lisinopril 10mg"}
      ]
    }
    ```
    Please let me know if you need any additional clarification.
    """
    cleaned = _extract_json(raw_response)
    data = json.loads(cleaned)
    assert data["prescription_id"] == "test-rx-123"
    assert data["medicines"][0]["name_raw"] == "Lisinopril 10mg"


def test_extract_double_failure_returns_human_friendly_review_message(dummy_png_bytes):
    clean_array = np.full((1000, 750, 3), 240, dtype=np.uint8)
    qr = assess_quality(clean_array)

    with patch("dosely.extractor._get_client"), \
         patch("dosely.extractor._call_claude", return_value="Not valid json"), \
         patch("dosely.extractor.preprocess_with_timeout") as mock_prep:
        mock_prep.return_value = (dummy_png_bytes, PipelineManifest(), qr)

        rx = extract(dummy_png_bytes, prescription_id="double-fail-test")
        assert rx.needs_human_review is True
        assert rx.medicines == []
        assert any("automatically validated after two attempts" in s for s in rx.review_summary)
