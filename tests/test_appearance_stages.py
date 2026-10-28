"""
Unit tests for Task 3: Appearance stages.
=========================================
Tests cover:
  - Illumination normalization (shadows/uneven lighting removal, background uniformity improvement)
  - Edge-preserving denoising with thin stroke retention measurement (>= 95% retention)
  - Adaptive CLAHE contrast enhancement
  - Mild unsharp mask for moderate blur only (skipped for severe blur)
  - Resolution upscaling with Lanczos when text height is between floor and target
  - Resolution upscaler respects maximum pixel count cap
  - Glare detection flags UI retake in QualityReport without inpainting over text
  - Binarization is OFF by default and available as experimental variant
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

from dosely.config import (
    MAX_PIXEL_COUNT,
    PIPELINE_STAGES,
    STAGE_BINARIZATION_ENABLED,
    STAGE_DENOISE_ENABLED,
    STAGE_ILLUMINATION_ENABLED,
    STAGE_RESOLUTION_ENABLED,
    STAGE_SHARPEN_ENABLED,
)
from dosely.preprocessing import (
    BinarizationStage,
    ContrastStage,
    DenoiseStage,
    IlluminationStage,
    ResolutionStage,
    SharpenStage,
    StageReport,
    assess_quality,
)


def _render_thin_stroke_text(
    text: str = "Paracetamol 500mg\nAmoxicillin 250mg\nCiprofloxacin 500mg",
    size: tuple[int, int] = (600, 400),
    stroke_width: int = 1,
    font_size: int = 20,
) -> np.ndarray:
    """Render thin, delicate handwriting/print strokes for stroke retention tests."""
    w, h = size
    pil_img = Image.new("RGB", (w, h), color=(245, 245, 245))
    draw = ImageDraw.Draw(pil_img)
    font = None
    for name in [
        "arial.ttf",
        "Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "DejaVuSans.ttf",
        "LiberationSans-Regular.ttf",
    ]:
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except Exception:
            pass
    if font is None:
        try:
            font = ImageFont.load_default(size=font_size)
        except Exception:
            font = ImageFont.load_default()

    y = 50
    lines = [l for l in text.replace("\\n", "\n").splitlines() if l.strip()]
    for line in lines:
        draw.text((40, y), line, fill=(20, 20, 20), font=font)
        y += font_size + 20

    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def _compute_stroke_retention(clean_gray: np.ndarray, processed_gray: np.ndarray) -> float:
    """
    Measure percentage of ink stroke pixels retained in processed image.
    Ink pixels are defined as pixels significantly darker than paper background.
    """
    # Clean ink mask: dark pixels on paper
    paper_val = np.percentile(clean_gray, 85)
    ink_mask = clean_gray < (paper_val - 40)
    total_ink_pixels = int(np.count_nonzero(ink_mask))
    if total_ink_pixels == 0:
        return 1.0

    # In processed image, check if ink pixels are still retained (not washed out into paper)
    processed_paper = np.percentile(processed_gray, 85)
    retained_ink = processed_gray[ink_mask] < (processed_paper - 20)
    retained_pixels = int(np.count_nonzero(retained_ink))
    return float(retained_pixels / total_ink_pixels)


# ---------------------------------------------------------------------------
# Test 1: Illumination Normalization
# ---------------------------------------------------------------------------

class TestIlluminationNormalization:
    def test_shadow_gradient_removed_and_background_uniformity_improved(self):
        clean = _render_thin_stroke_text()
        gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # Simulate heavy shadow gradient across the page (left dark: 110, right bright: 245)
        gradient = np.tile(np.linspace(0.45, 1.0, w, dtype=np.float32), (h, 1))
        shadowed = np.clip(gray.astype(np.float32) * gradient, 0, 255).astype(np.uint8)

        stage = IlluminationStage()
        normalized, report = stage.process(shadowed)

        assert report.applied is True
        # Background uniformity metric should improve (shadow variance drops)
        assert report.metric_after < report.metric_before
        # Strokes must remain legible
        retention = _compute_stroke_retention(gray, normalized)
        assert retention >= 0.90


# ---------------------------------------------------------------------------
# Test 2: Edge-Preserving Denoising with Stroke Retention
# ---------------------------------------------------------------------------

class TestDenoising:
    def test_thin_strokes_survive_denoising(self):
        clean = _render_thin_stroke_text(stroke_width=1)
        gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)

        # Add Gaussian noise
        rng = np.random.default_rng(42)
        noise = rng.normal(0, 12, gray.shape).astype(np.float32)
        noisy = np.clip(gray.astype(np.float32) + noise, 0, 255).astype(np.uint8)

        stage = DenoiseStage()
        denoised, report = stage.process(noisy)

        assert report.applied is True
        # Edge-preserving denoising must retain at least 95% of thin stroke pixels
        retention = _compute_stroke_retention(gray, denoised)
        assert retention >= 0.95, f"Stroke retention was {retention:.3f}, expected >= 0.95"


# ---------------------------------------------------------------------------
# Test 3: Adaptive Contrast
# ---------------------------------------------------------------------------

class TestAdaptiveContrast:
    def test_clahe_adapts_to_low_contrast(self):
        clean = _render_thin_stroke_text()
        gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
        # Low contrast image (faint gray on off-white)
        low_contrast = np.clip(gray.astype(np.float32) * 0.4 + 130, 0, 255).astype(np.uint8)

        stage = ContrastStage()
        enhanced, report = stage.process(low_contrast, config={"adaptive": True})

        assert report.applied is True
        # Contrast RMS should substantially increase
        assert report.metric_after > report.metric_before
        assert report.parameters.get("adaptive_clip_limit", 2.0) >= 2.0


# ---------------------------------------------------------------------------
# Test 4: Sharpening (Unsharp Mask for Moderate Blur Only)
# ---------------------------------------------------------------------------

class TestSharpening:
    def test_sharpening_applied_for_moderate_blur(self):
        clean = _render_thin_stroke_text()
        gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
        # Moderate blur (Laplacian variance ~80-120)
        moderate_blur = cv2.GaussianBlur(gray, (5, 5), sigmaX=1.1)

        stage = SharpenStage()
        sharpened, report = stage.process(moderate_blur)

        assert report.applied is True
        # Metric after (sharpness) should be higher than before
        assert report.metric_after > report.metric_before

    def test_sharpening_skipped_for_severe_blur(self):
        clean = _render_thin_stroke_text()
        gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)
        # Severe blur (Laplacian variance < 40)
        severe_blur = cv2.GaussianBlur(gray, (25, 25), sigmaX=8.0)

        stage = SharpenStage()
        out, report = stage.process(severe_blur)

        # Must skip to not pretend to recover motion blur
        assert report.applied is False
        assert any("severe blur" in w.lower() or "skipped" in w.lower() for w in report.warnings)


# ---------------------------------------------------------------------------
# Test 5: Resolution Upscaling
# ---------------------------------------------------------------------------

class TestResolutionStage:
    def test_upscaling_when_text_height_between_floor_and_target(self):
        # Render tiny text (font_size = 10, between 8px floor and 14px target)
        clean = _render_thin_stroke_text(font_size=10, size=(400, 300))
        h_before, w_before = clean.shape[:2]

        stage = ResolutionStage()
        upscaled, report = stage.process(clean, config={"target_text_height": 18.0})

        assert report.applied is True
        assert upscaled.shape[0] > h_before
        assert upscaled.shape[1] > w_before
        assert "lanczos" in report.parameters.get("interpolation", "").lower()

    def test_upscaling_capped_at_maximum_pixel_count(self):
        # Image that would exceed MAX_PIXEL_COUNT if upscaled
        huge = np.full((2500, 3500, 3), 245, dtype=np.uint8)  # 8.75 MP
        stage = ResolutionStage()
        out, report = stage.process(huge, config={"max_pixels": 8_000_000, "force_upscale_factor": 2.0})

        assert report.applied is False
        assert any("cap" in w.lower() or "limit" in w.lower() or "exceed" in w.lower() for w in report.warnings)
        assert out.shape == huge.shape


# ---------------------------------------------------------------------------
# Test 6: Glare Quality Report Flagging
# ---------------------------------------------------------------------------

class TestGlareReporting:
    def test_glare_flags_quality_report_without_inpainting(self):
        clean = _render_thin_stroke_text()
        h, w = clean.shape[:2]
        glare = clean.copy()
        # Create a large specular glare spot
        cv2.circle(glare, (w // 2, h // 2), 70, (255, 255, 255), -1)

        report = assess_quality(glare)
        # Must flag glare in reasons for UI retake prompt
        assert any("glare" in r.lower() for r in report.reasons)


# ---------------------------------------------------------------------------
# Test 7: Binarization Off by Default
# ---------------------------------------------------------------------------

class TestBinarization:
    def test_binarization_is_off_by_default(self):
        assert STAGE_BINARIZATION_ENABLED is False
        assert "binarization" not in PIPELINE_STAGES

    def test_experimental_binarization_stage(self):
        clean = _render_thin_stroke_text()
        gray = cv2.cvtColor(clean, cv2.COLOR_BGR2GRAY)

        stage = BinarizationStage()
        binarized, report = stage.process(gray, config={"enabled": True, "method": "sauvola"})

        assert report.applied is True
        # Output should be purely binary (0 or 255)
        unique_vals = np.unique(binarized)
        assert len(unique_vals) <= 2
