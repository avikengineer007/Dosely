"""
Tests for Dosely Preprocessing Pipeline and Quality Assessment (Task 1).
======================================================================
Covers:
  - Pipeline of pure stages (Stage, StageReport, PipelineManifest)
  - Configuration of stages from config (enable flags, parameters)
  - Manifest safety (no image pixels, filenames, or content leaked)
  - QualityReport metrics computed on original image
  - Synthetic defect detection (blur, dark, bright, glare, tiny text, extreme skew)
  - Blank page and pure noise detection (must stay 'poor')
  - Safety rule: enhancement can NEVER upgrade a poor original to good
  - Hard failure preservation
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import pytest

from dosely.config import (
    PIPELINE_STAGES,
    QUALITY_THRESHOLDS,
    STAGE_CONTRAST_ENABLED,
    STAGE_DESKEW_ENABLED,
    STAGE_GRAYSCALE_ENABLED,
)
from dosely.preprocessing import (
    PipelineManifest,
    QualityReport,
    StageReport,
    assess_quality,
    preprocess_with_manifest,
    run_pipeline,
)


# ---------------------------------------------------------------------------
# Synthetic image generators for testing
# ---------------------------------------------------------------------------

def _create_clean_prescription_image(
    text: str = "Paracetamol 500mg TDS\\nAmoxicillin 250mg BD",
    font_size: int = 24,
    size: tuple[int, int] = (800, 600),
    skew_angle: float = 0.0,
) -> np.ndarray:
    """Render a clean black-on-white prescription document."""
    w, h = size
    paper_color = (245, 245, 245)
    pil_img = Image.new("RGB", (w, h), color=paper_color)
    draw = ImageDraw.Draw(pil_img)
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    draw.text((60, 50), "CITY HOSPITAL - PRESCRIPTION", fill=(0, 0, 0), font=font)
    draw.line([(50, 90), (w - 50, 90)], fill=(0, 0, 0), width=2)
    y = 120
    for line in text.split("\\n"):
        draw.text((60, y), line, fill=(0, 0, 0), font=font)
        y += font_size + 15

    if abs(skew_angle) > 0.01:
        pil_img = pil_img.rotate(skew_angle, resample=Image.BICUBIC, fillcolor=paper_color)

    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


# ---------------------------------------------------------------------------
# Pipeline Architecture Tests
# ---------------------------------------------------------------------------

class TestPipelineArchitecture:
    def test_run_pipeline_returns_image_and_manifest(self):
        img = _create_clean_prescription_image()
        processed, manifest = run_pipeline(img)

        assert isinstance(processed, np.ndarray)
        assert isinstance(manifest, PipelineManifest)
        assert len(manifest.stages) > 0

    def test_stage_reports_required_fields(self):
        img = _create_clean_prescription_image()
        _, manifest = run_pipeline(img)

        for stage in manifest.stages:
            assert isinstance(stage, StageReport)
            assert isinstance(stage.name, str)
            assert isinstance(stage.applied, bool)
            assert isinstance(stage.parameters, dict)
            assert isinstance(stage.duration_ms, float)
            assert stage.duration_ms >= 0.0
            # Before and after metrics reported
            assert hasattr(stage, "metric_before")
            assert hasattr(stage, "metric_after")

    def test_stage_can_be_disabled_via_config(self):
        img = _create_clean_prescription_image()
        # Run with deskew disabled
        overrides = {"deskew": {"enabled": False}}
        _, manifest = run_pipeline(img, stage_configs=overrides)

        deskew_stage = next((s for s in manifest.stages if s.name == "deskew"), None)
        assert deskew_stage is not None
        assert deskew_stage.applied is False

    def test_manifest_is_safe_to_log_and_store(self):
        img = _create_clean_prescription_image()
        _, manifest = run_pipeline(img)
        manifest_dict = manifest.to_dict()

        # Must not contain raw image content or sensitive content
        manifest_str = str(manifest_dict)
        assert "Paracetamol" not in manifest_str
        assert "Amoxicillin" not in manifest_str
        assert "data:image" not in manifest_str
        assert "base64" not in manifest_str
        assert "ndarray" not in manifest_str


# ---------------------------------------------------------------------------
# Quality Assessment & Defect Tests
# ---------------------------------------------------------------------------

class TestQualityAssessment:
    def test_clean_image_assessed_as_good(self):
        img = _create_clean_prescription_image(font_size=24)
        report = assess_quality(img)

        assert isinstance(report, QualityReport)
        assert report.image_quality == "good"
        assert report.hard_failure is False
        assert len(report.reasons) == 0

    def test_blurry_image_detected_as_poor(self):
        clean = _create_clean_prescription_image()
        # Apply severe Gaussian blur
        blurry = cv2.GaussianBlur(clean, (45, 45), sigmaX=15.0)
        report = assess_quality(blurry)

        assert report.image_quality == "poor"
        assert any("blur" in r.lower() for r in report.reasons)

    def test_dark_image_detected_as_poor(self):
        clean = _create_clean_prescription_image()
        # Scale brightness way down
        dark = (clean.astype(np.float32) * 0.08).astype(np.uint8)
        report = assess_quality(dark)

        assert report.image_quality == "poor"
        assert any("dark" in r.lower() for r in report.reasons)

    def test_bright_washed_out_image_detected(self):
        clean = _create_clean_prescription_image()
        # Wash out image
        bright = np.clip(clean.astype(np.float32) + 220, 0, 255).astype(np.uint8)
        report = assess_quality(bright)

        assert report.image_quality in ("fair", "poor")
        assert any("bright" in r.lower() or "contrast" in r.lower() for r in report.reasons)

    def test_glare_image_detected(self):
        clean = _create_clean_prescription_image()
        h, w = clean.shape[:2]
        # Paint a bright specular saturation blob in the center
        glare = clean.copy()
        cv2.circle(glare, (w // 2, h // 2), 120, (255, 255, 255), -1)
        report = assess_quality(glare)

        assert report.image_quality in ("fair", "poor")
        assert any("glare" in r.lower() for r in report.reasons)

    def test_tiny_text_hard_failure(self):
        # Render image with tiny 6px font (below 8px hard floor)
        tiny = _create_clean_prescription_image(font_size=6)
        report = assess_quality(tiny)

        assert report.image_quality == "poor"
        assert report.hard_failure is True
        assert any("small" in r.lower() for r in report.reasons)

    def test_extreme_skew_detected(self):
        skewed = _create_clean_prescription_image(skew_angle=35.0)
        report = assess_quality(skewed)

        assert report.image_quality in ("fair", "poor")
        assert any("skew" in r.lower() for r in report.reasons)

    def test_blank_page_stays_poor(self):
        blank = np.full((600, 800, 3), 255, dtype=np.uint8)
        report = assess_quality(blank)

        assert report.image_quality == "poor"
        assert report.hard_failure is True

    def test_pure_noise_stays_poor(self):
        rng = np.random.default_rng(42)
        noise = rng.integers(0, 256, (600, 800, 3), dtype=np.uint8)
        report = assess_quality(noise)

        assert report.image_quality == "poor"
        assert report.hard_failure is True


# ---------------------------------------------------------------------------
# Safety Invariant Tests
# ---------------------------------------------------------------------------

class TestSafetyInvariants:
    def test_enhancement_never_upgrades_poor_original_to_good(self):
        """Rule: If original is poor, pipeline output quality MUST remain poor."""
        clean = _create_clean_prescription_image()
        blurry = cv2.GaussianBlur(clean, (35, 35), sigmaX=10.0)

        original_report = assess_quality(blurry)
        assert original_report.image_quality == "poor"

        # Encode to bytes and pass through preprocess_with_manifest
        _, buf = cv2.imencode(".png", blurry)
        png_bytes, manifest, final_quality = preprocess_with_manifest(buf.tobytes())

        assert final_quality.image_quality == "poor"
        assert any("blur" in r.lower() for r in final_quality.reasons)

    def test_hard_check_failure_on_original_always_marks_poor(self):
        """If original fails a hard check (e.g. text height under floor), result is poor."""
        tiny = _create_clean_prescription_image(font_size=6)
        _, buf = cv2.imencode(".png", tiny)
        _, _, final_quality = preprocess_with_manifest(buf.tobytes())

        assert final_quality.image_quality == "poor"
        assert final_quality.hard_failure is True
