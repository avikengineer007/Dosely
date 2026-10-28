"""
Unit and integration tests for Task 4: Pipeline Integration.
============================================================
Tests cover:
  1. Extractor uses preprocess_with_manifest and attaches manifest to output.
  2. Extractor adheres to API size limits (max dimension <= 1568px).
  3. Preprocessing time budget enforcement (fallback on timeout without hanging).
  4. Dual-image strategy for "fair" quality: triggers on disagreement.
  5. Manifest persistence alongside encrypted source image at rest.
  6. Caplog audit: zero health data, pixels, OCR text, or client filenames in logs.
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
from PIL import Image, ImageDraw
import pytest
from starlette.testclient import TestClient

from main import app
from dosely.config import API_MAX_IMAGE_DIMENSION, DUAL_READ_ON_FAIR_ENABLED
from dosely.extractor import extract, _ensure_api_image_dimensions
from dosely.models import Prescription
from dosely.storage import EncryptedStorage


def _make_dummy_image_bytes(w: int = 400, h: int = 300, text: str = "Paracetamol 500mg") -> bytes:
    pil_img = Image.new("RGB", (w, h), color=(250, 250, 250))
    draw = ImageDraw.Draw(pil_img)
    draw.text((20, 20), text, fill=(10, 10, 10))
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return buf.getvalue()


class TestExtractorIntegration:
    @patch("dosely.extractor._call_claude")
    @patch("dosely.extractor._get_client")
    def test_extractor_uses_preprocess_with_manifest(self, mock_client, mock_call_claude):
        mock_response = json.dumps({
            "prescription_id": "test-rx-123",
            "image_quality": "good",
            "medicines": [
                {
                    "name_raw": "Amoxicillin 500mg",
                    "confidence": 0.95,
                    "dose_amount": "500mg",
                    "frequency": "three_times_daily",
                }
            ],
            "needs_human_review": False,
        })
        mock_call_claude.return_value = mock_response

        raw_bytes = _make_dummy_image_bytes()
        rx = extract(raw_bytes, prescription_id="test-rx-123")

        assert isinstance(rx, Prescription)
        assert rx.pipeline_manifest is not None
        assert "stages" in rx.pipeline_manifest
        assert "total_duration_ms" in rx.pipeline_manifest

    def test_ensure_api_image_dimensions_resizes_large_images(self):
        huge_bytes = _make_dummy_image_bytes(w=2400, h=3200)
        resized_bytes = _ensure_api_image_dimensions(huge_bytes, max_dim=1568)

        img = Image.open(io.BytesIO(resized_bytes))
        w, h = img.size
        assert max(w, h) <= 1568
        # Aspect ratio preserved (3:4)
        assert abs((w / h) - (2400 / 3200)) < 0.05

    @patch("dosely.extractor._call_claude")
    @patch("dosely.extractor._get_client")
    def test_preprocessing_time_budget_timeout_fallback(self, mock_client, mock_call_claude):
        mock_response = json.dumps({
            "prescription_id": "test-rx-timeout",
            "image_quality": "fair",
            "medicines": [{"name_raw": "Paracetamol", "confidence": 0.8}],
            "needs_human_review": False,
        })
        mock_call_claude.return_value = mock_response

        raw_bytes = _make_dummy_image_bytes()

        # Patch run_pipeline to simulate a slow processing hang
        def _slow_pipeline(*args, **kwargs):
            import time
            time.sleep(0.1)
            raise TimeoutError("Simulated pipeline hang")

        with patch("dosely.extractor.preprocess_with_manifest", side_effect=_slow_pipeline):
            rx = extract(raw_bytes, prescription_id="test-rx-timeout")
            # Should not crash; should fall back
            assert isinstance(rx, Prescription)
            assert rx.prescription_id == "test-rx-timeout"

    @patch("dosely.extractor._call_claude")
    @patch("dosely.extractor._get_client")
    def test_dual_read_on_fair_flags_disagreement(self, mock_client, mock_call_claude):
        response_preprocessed = json.dumps({
            "prescription_id": "test-dual-1",
            "image_quality": "fair",
            "medicines": [{"name_raw": "Amoxicillin 500mg", "confidence": 0.85}],
            "needs_human_review": False,
        })
        response_source = json.dumps({
            "prescription_id": "test-dual-1",
            "image_quality": "fair",
            "medicines": [{"name_raw": "Ampicillin 250mg", "confidence": 0.80}],
            "needs_human_review": False,
        })
        # Return different extractions for the two calls
        mock_call_claude.side_effect = [response_preprocessed, response_source]

        raw_bytes = _make_dummy_image_bytes()
        from dosely.preprocessing import PipelineManifest, QualityReport
        mock_qr = QualityReport(
            image_quality="fair",
            reasons=["Contrast is low"],
            sharpness=100.0,
            motion_blur=0.1,
            brightness_mean=120.0,
            brightness_clipping_percent=0.0,
            contrast_rms=25.0,
            contrast_percentile_spread=50.0,
            resolution_megapixels=0.5,
            estimated_text_height_px=14.0,
            glare_percent=0.0,
            skew_angle=0.0,
            perspective_distortion=0.0,
            shadow_severity=10.0,
            page_coverage=90.0,
            hard_failure=False,
        )
        mock_manifest = PipelineManifest(stages=[], metrics={}, total_duration_ms=5.0)

        with patch("dosely.extractor.preprocess_with_manifest", return_value=(raw_bytes, mock_manifest, mock_qr)):
            with patch("dosely.extractor.DUAL_READ_ON_FAIR_ENABLED", True):
                rx = extract(raw_bytes, prescription_id="test-dual-1")
                assert rx.needs_human_review is True
                assert any("dual-read" in r.lower() or "disagreement" in r.lower() for r in rx.review_summary)


class TestManifestStorageAndCaplog:
    def test_encrypted_storage_saves_and_loads_manifest(self, tmp_path):
        storage = EncryptedStorage(storage_dir=tmp_path)
        manifest_data = {
            "stages": [{"name": "deskew", "applied": True}],
            "duration_ms": 12.5,
            "metrics": {"sharpness": 120.0},
        }

        rx_id = "test-manifest-persist-001"
        storage.save_manifest(rx_id, manifest_data)

        loaded = storage.load_manifest(rx_id)
        assert loaded == manifest_data

    @patch("dosely.extractor._call_claude")
    @patch("dosely.extractor._get_client")
    def test_caplog_zero_health_data_in_extractor_logs(self, mock_client, mock_call_claude, caplog):
        caplog.set_level(logging.DEBUG)
        secret_med = "SpecialSecretDrugX 999mg"
        mock_response = json.dumps({
            "prescription_id": "test-rx-caplog",
            "image_quality": "good",
            "medicines": [{"name_raw": secret_med, "confidence": 0.99}],
            "needs_human_review": False,
        })
        mock_call_claude.return_value = mock_response

        raw_bytes = _make_dummy_image_bytes(text=secret_med)
        extract(raw_bytes, prescription_id="test-rx-caplog")

        for record in caplog.records:
            msg = record.getMessage().lower()
            assert "specialsecretdrugx" not in msg
            assert "999mg" not in msg
            # Check forbidden logging patterns
            assert "array(" not in msg
            assert "pixels" not in msg
