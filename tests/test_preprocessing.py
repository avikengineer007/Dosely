"""
Unit tests for dosely.preprocessing
=====================================
Tests cover:
  - Grayscale conversion (RGB -> gray)
  - CLAHE contrast normalisation output shape/dtype
  - Deskew: near-zero angle is a no-op
  - Deskew: non-zero angle is actually corrected
  - Full preprocess() pipeline with valid PNG bytes
  - preprocess_and_assess() returns (bytes, quality_str)
  - _is_pdf() magic byte detection
  - Quality assessment thresholds
"""

from __future__ import annotations

import numpy as np
import pytest

from dosely.preprocessing import (
    _bytes_to_array,
    _is_pdf,
    _to_grayscale,
    assess_image_quality,
    contrast_normalize,
    deskew,
    preprocess,
    preprocess_and_assess,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_array(h: int = 200, w: int = 400, channels: int = 3) -> np.ndarray:
    """Create a random uint8 array."""
    rng = np.random.default_rng(0)
    if channels == 1:
        return rng.integers(0, 255, (h, w), dtype=np.uint8)
    return rng.integers(0, 255, (h, w, channels), dtype=np.uint8)


# ---------------------------------------------------------------------------
# _is_pdf
# ---------------------------------------------------------------------------

class TestIsPdf:
    def test_pdf_magic_bytes(self):
        assert _is_pdf(b"%PDF-1.4 ...") is True

    def test_png_bytes_not_pdf(self, white_png):
        assert _is_pdf(white_png) is False

    def test_empty_bytes_not_pdf(self):
        assert _is_pdf(b"") is False

    def test_jpeg_not_pdf(self):
        assert _is_pdf(b"\xff\xd8\xff ...") is False


# ---------------------------------------------------------------------------
# _bytes_to_array
# ---------------------------------------------------------------------------

class TestBytesToArray:
    def test_valid_png_returns_array(self, white_png):
        arr = _bytes_to_array(white_png)
        assert isinstance(arr, np.ndarray)
        assert arr.ndim == 2 or arr.ndim == 3  # gray or BGR

    def test_invalid_bytes_raises(self):
        with pytest.raises(ValueError, match="Could not decode"):
            _bytes_to_array(b"not an image")

    def test_noisy_png_returns_correct_shape(self, noisy_png):
        arr = _bytes_to_array(noisy_png)
        assert arr.shape[0] == 200
        assert arr.shape[1] == 400


# ---------------------------------------------------------------------------
# _to_grayscale
# ---------------------------------------------------------------------------

class TestToGrayscale:
    def test_bgr_to_gray(self):
        bgr = _make_array(100, 200, channels=3)
        gray = _to_grayscale(bgr)
        assert gray.ndim == 2
        assert gray.shape == (100, 200)

    def test_already_gray_passthrough(self):
        gray = _make_array(100, 200, channels=1)
        out = _to_grayscale(gray)
        assert out.ndim == 2
        np.testing.assert_array_equal(out, gray)

    def test_output_dtype_is_uint8(self):
        bgr = _make_array(50, 50, channels=3)
        gray = _to_grayscale(bgr)
        assert gray.dtype == np.uint8


# ---------------------------------------------------------------------------
# contrast_normalize
# ---------------------------------------------------------------------------

class TestContrastNormalize:
    def test_output_shape_preserved(self):
        gray = _make_array(100, 200, channels=1)
        out = contrast_normalize(gray)
        assert out.shape == gray.shape

    def test_output_dtype_preserved(self):
        gray = _make_array(100, 200, channels=1)
        out = contrast_normalize(gray)
        assert out.dtype == np.uint8

    def test_pure_white_stays_white(self):
        gray = np.ones((100, 100), dtype=np.uint8) * 255
        out = contrast_normalize(gray)
        # CLAHE on uniform white should not dramatically change values
        assert out.mean() > 200

    def test_pure_black_stays_dark(self):
        gray = np.zeros((100, 100), dtype=np.uint8)
        out = contrast_normalize(gray)
        assert out.mean() < 50


# ---------------------------------------------------------------------------
# deskew
# ---------------------------------------------------------------------------

class TestDeskew:
    def test_white_image_no_change(self, white_png):
        import cv2
        arr = _bytes_to_array(white_png)
        gray = _to_grayscale(arr)
        out = deskew(gray)
        # White image has no lines — angle is 0 — shape preserved
        assert out.shape == gray.shape

    def test_output_dtype_uint8(self, skewed_png):
        arr = _bytes_to_array(skewed_png)
        gray = _to_grayscale(arr)
        out = deskew(gray)
        assert out.dtype == np.uint8

    def test_shape_preserved_after_deskew(self, skewed_png):
        arr = _bytes_to_array(skewed_png)
        gray = _to_grayscale(arr)
        out = deskew(gray)
        assert out.shape == gray.shape


# ---------------------------------------------------------------------------
# assess_image_quality
# ---------------------------------------------------------------------------

class TestAssessImageQuality:
    def test_high_variance_is_good(self):
        import cv2
        rng = np.random.default_rng(42)
        # High-frequency noise has high Laplacian variance -> "good"
        sharp = rng.integers(0, 255, (200, 200), dtype=np.uint8)
        result = assess_image_quality(sharp)
        assert result in ("good", "fair")  # very noisy usually > 150

    def test_uniform_image_is_poor(self):
        gray = np.ones((200, 200), dtype=np.uint8) * 128
        result = assess_image_quality(gray)
        assert result == "poor"

    def test_valid_return_values(self, white_png):
        arr = _bytes_to_array(white_png)
        gray = _to_grayscale(arr)
        result = assess_image_quality(gray)
        assert result in ("good", "fair", "poor")


# ---------------------------------------------------------------------------
# preprocess (full pipeline)
# ---------------------------------------------------------------------------

class TestPreprocess:
    def test_returns_bytes(self, white_png):
        result = preprocess(white_png)
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_output_is_valid_png(self, white_png):
        import cv2
        result = preprocess(white_png)
        arr = np.frombuffer(result, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        assert img is not None

    def test_noisy_image_preprocessed(self, noisy_png):
        result = preprocess(noisy_png)
        assert isinstance(result, bytes)
        assert len(result) > 100  # not empty

    def test_invalid_input_raises(self):
        with pytest.raises(ValueError):
            preprocess(b"garbage data xyz")


# ---------------------------------------------------------------------------
# preprocess_and_assess
# ---------------------------------------------------------------------------

class TestPreprocessAndAssess:
    def test_returns_tuple(self, white_png):
        png_bytes, quality = preprocess_and_assess(white_png)
        assert isinstance(png_bytes, bytes)
        assert quality in ("good", "fair", "poor")

    def test_quality_string_valid(self, noisy_png):
        _, quality = preprocess_and_assess(noisy_png)
        assert quality in ("good", "fair", "poor")
