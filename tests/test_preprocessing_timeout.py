"""
tests/test_preprocessing_timeout.py
===================================
Unit tests for process-pool timeout enforcement in the image preprocessing pipeline.

Verifies:
  1. A hung processing stage is cleanly terminated (CPU released).
  2. Fallback runs quality assessment on the original input image.
  3. Image quality tier is capped at 'fair' (or 'poor' if original failed checks; never 'good').
  4. Manifest records timed_out=True and records warning.
"""

from __future__ import annotations

import time
import cv2
import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from dosely.preprocessing import (
    PipelineManifest,
    QualityReport,
    assess_quality,
    preprocess_with_timeout,
)


def _make_good_test_prescription_bytes() -> bytes:
    """Render a high-quality, sharp synthetic prescription PNG."""
    w, h = 600, 800
    img = np.full((h, w, 3), 245, dtype=np.uint8)
    # Add clinic header and sharp lines
    cv2.putText(img, "APOLLO CLINIC & PHARMACY", (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2)
    cv2.line(img, (30, 80), (w - 30, 80), (50, 50, 50), 2)
    for i in range(12):
        y = 130 + i * 45
        cv2.putText(img, f"Tab. Medication-{i+1} 500mg - 1 Tab TDS x 5 Days", (40, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (10, 10, 10), 2)
    cv2.line(img, (w - 200, h - 80), (w - 40, h - 80), (50, 50, 50), 2)
    cv2.putText(img, "Dr. Mukherjee", (w - 180, h - 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (60, 60, 60), 1)
    success, buf = cv2.imencode(".png", img)
    assert success
    return buf.tobytes()


class TestProcessTimeoutEnforcement:
    def test_normal_image_completes_without_timeout(self):
        good_bytes = _make_good_test_prescription_bytes()
        png_out, manifest, quality = preprocess_with_timeout(good_bytes, timeout_seconds=10.0)

        assert isinstance(png_out, bytes)
        assert len(png_out) > 0
        assert isinstance(manifest, PipelineManifest)
        assert manifest.timed_out is False
        assert len(manifest.stages) > 0
        assert isinstance(quality, QualityReport)

    def test_hung_stage_terminated_cpu_released_and_capped_at_fair(self):
        """
        Simulate a hung stage by imposing an impossibly short timeout (0.001s).
        Verifies:
          - Process terminates and releases CPU within the timeout budget.
          - Original quality assessment runs.
          - Quality is capped at 'fair' (or 'poor').
          - Manifest records timed_out=True.
        """
        good_bytes = _make_good_test_prescription_bytes()
        t0 = time.perf_counter()
        png_out, manifest, quality = preprocess_with_timeout(good_bytes, timeout_seconds=0.001)
        elapsed = time.perf_counter() - t0

        # Terminated quickly — did not hang
        assert elapsed < 3.0, f"Process took {elapsed:.2f}s; failed to terminate quickly"

        # Manifest records timeout
        assert manifest.timed_out is True
        assert any("timeout" in w.lower() for w in manifest.warnings)

        # Quality tier is capped at 'fair' or 'poor' (never 'good')
        assert quality.image_quality in ("fair", "poor")
        assert any("timed out" in r.lower() for r in quality.reasons)

        # Output bytes match original input bytes
        assert png_out == good_bytes

    def test_manifest_to_dict_includes_timed_out_flag(self):
        good_bytes = _make_good_test_prescription_bytes()
        _, manifest, _ = preprocess_with_timeout(good_bytes, timeout_seconds=0.001)
        d = manifest.to_dict()
        assert "timed_out" in d
        assert d["timed_out"] is True
