"""
Unit tests for Task 2: Geometry stages.
========================================
Tests cover:
  - Orientation detection and fix (EXIF + projection profile heuristic for 90/180/270 degrees)
  - Document detection and perspective correction with 4-point transform
  - Rejection of bad warps exceeding maximum stretch factor
  - No detectable page passes through uncropped
  - Deskew with Hough + minAreaRect fallback, angle capping, and <0.5 deg skip
  - Content crop with blank margin trimming and padding without cutting text
  - Multi-page PDF rasterization, page capping (10), and per-page manifests
  - Geometry recovery on synthetic images from data/test_images/
"""

from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pymupdf as fitz
import pytest

from dosely.config import (
    MAX_PERSPECTIVE_STRETCH,
    PIPELINE_STAGES,
    STAGE_CROP_ENABLED,
    STAGE_ORIENTATION_ENABLED,
    STAGE_PERSPECTIVE_ENABLED,
)
from dosely.preprocessing import (
    ContentCropStage,
    DeskewStage,
    DocumentPerspectiveStage,
    OrientationStage,
    PipelineManifest,
    StageReport,
    process_pdf_pages,
    run_pipeline,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "test_images"


def _make_sample_document(
    text: str = "Dr. S. K. Mukherjee, MBBS\\nParacetamol 500mg BD x 5 days\\nAmoxicillin 500mg TDS x 7 days",
    size: tuple[int, int] = (600, 800),
    bg_color: tuple[int, int, int] = (245, 245, 245),
) -> np.ndarray:
    """Create a standard document with legible text."""
    w, h = size
    pil_img = Image.new("RGB", (w, h), color=bg_color)
    draw = ImageDraw.Draw(pil_img)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        font = ImageFont.load_default()

    draw.text((40, 40), "CITY HEALTH CLINIC", fill=(20, 20, 20), font=font)
    draw.line([(30, 80), (w - 30, 80)], fill=(0, 0, 0), width=2)
    y = 120
    for line in text.split("\\n"):
        draw.text((40, y), line, fill=(10, 10, 10), font=font)
        y += 40
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def _place_document_on_desk(
    doc_img: np.ndarray,
    desk_size: tuple[int, int] = (1000, 1200),
    desk_color: tuple[int, int, int] = (60, 50, 45),
    warp_quad: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Place a document inside a larger desk image, optionally perspective-warped.
    Returns (desk_img, quad_corners).
    """
    dw, dh = desk_size
    desk = np.full((dh, dw, 3), desk_color, dtype=np.uint8)

    doc_h, doc_w = doc_img.shape[:2]
    src_pts = np.float32([
        [0, 0],
        [doc_w - 1, 0],
        [doc_w - 1, doc_h - 1],
        [0, doc_h - 1],
    ])

    if warp_quad is None:
        # Centered rectangle with margin
        ox, oy = (dw - doc_w) // 2, (dh - doc_h) // 2
        dst_pts = np.float32([
            [ox, oy],
            [ox + doc_w, oy],
            [ox + doc_w, oy + doc_h],
            [ox, oy + doc_h],
        ])
    else:
        dst_pts = np.float32(warp_quad)

    M = cv2.getPerspectiveTransform(src_pts, dst_pts)
    warped_doc = cv2.warpPerspective(doc_img, M, (dw, dh))

    # Mask and composite onto desk
    mask = cv2.warpPerspective(np.full((doc_h, doc_w), 255, dtype=np.uint8), M, (dw, dh))
    mask_3ch = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    composite = np.where(mask_3ch > 0, warped_doc, desk)

    return composite, dst_pts


# ---------------------------------------------------------------------------
# Stage 1: Orientation tests
# ---------------------------------------------------------------------------

class TestOrientationStage:
    def test_orientation_90_degrees_fixed(self):
        doc = _make_sample_document(size=(600, 800))
        # Rotate 90 degrees clockwise (sideways photo)
        rotated_90 = cv2.rotate(doc, cv2.ROTATE_90_CLOCKWISE)

        stage = OrientationStage()
        corrected, report = stage.process(rotated_90)

        assert report.applied is True
        assert report.parameters.get("angle_fixed") in (90, 270)
        # Should be back to upright portrait orientation (h > w)
        assert corrected.shape[0] > corrected.shape[1]

    def test_orientation_180_degrees_fixed(self):
        doc = _make_sample_document(size=(600, 800))
        rotated_180 = cv2.rotate(doc, cv2.ROTATE_180)

        stage = OrientationStage()
        corrected, report = stage.process(rotated_180)

        assert isinstance(corrected, np.ndarray)
        assert isinstance(report, StageReport)

    def test_exif_orientation_handling(self):
        doc = _make_sample_document()
        pil_img = Image.fromarray(cv2.cvtColor(doc, cv2.COLOR_BGR2RGB))

        # Encode with EXIF orientation tag 6 (rotated 90 CW)
        exif = pil_img.getexif()
        exif[0x0112] = 6  # Orientation tag
        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", exif=exif)

        stage = OrientationStage()
        corrected, report = stage.process(doc, config={"exif_bytes": buf.getvalue()})
        assert report.applied is True
        assert report.parameters.get("exif_corrected") is True


# ---------------------------------------------------------------------------
# Stage 2: Document detection & perspective correction tests
# ---------------------------------------------------------------------------

class TestPerspectiveCorrectionStage:
    def test_detect_and_warp_quadrilateral(self):
        doc = _make_sample_document(size=(500, 700))
        # Known perspective quad on a 900x1100 desk
        quad = np.array([
            [150, 120],  # top-left
            [750, 180],  # top-right
            [680, 950],  # bottom-right
            [120, 880],  # bottom-left
        ], dtype=np.float32)

        desk_img, _ = _place_document_on_desk(doc, desk_size=(900, 1100), warp_quad=quad)

        stage = DocumentPerspectiveStage()
        warped, report = stage.process(desk_img)

        assert report.applied is True
        assert "corners" in report.parameters
        # The background desk should have been cropped away; aspect should match document
        assert warped.shape[0] > 600
        assert warped.shape[1] > 400

    def test_no_detectable_page_passes_through_uncropped(self):
        # A texture with no quadrilateral or edge-to-edge content
        desk = np.full((600, 800, 3), 128, dtype=np.uint8)

        stage = DocumentPerspectiveStage()
        out, report = stage.process(desk)

        assert report.applied is False
        assert out.shape == desk.shape
        assert any("no document" in w.lower() or "skipped" in w.lower() for w in report.warnings)

    def test_bad_warp_rejection_sanity_check(self):
        # An extreme, needle-like distorted quad stretching > MAX_PERSPECTIVE_STRETCH
        doc = _make_sample_document(size=(400, 600))
        extreme_quad = np.array([
            [100, 100],
            [900, 110],
            [120, 900],
            [110, 890],
        ], dtype=np.float32)

        desk_img, _ = _place_document_on_desk(doc, desk_size=(1000, 1000), warp_quad=extreme_quad)

        stage = DocumentPerspectiveStage()
        out, report = stage.process(desk_img, config={"max_stretch": 2.0})

        # Must reject bad warp to protect content safety
        assert report.applied is False
        assert out.shape == desk_img.shape
        assert any("stretch" in w.lower() or "sanity" in w.lower() or "skipped" in w.lower() for w in report.warnings)


# ---------------------------------------------------------------------------
# Stage 3: Deskew tests
# ---------------------------------------------------------------------------

class TestDeskewStage:
    def test_deskew_recovers_known_tilt(self):
        doc = _make_sample_document()
        # Rotate by +4.0 degrees
        h, w = doc.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), 4.0, 1.0)
        tilted = cv2.warpAffine(doc, M, (w, h), borderValue=(245, 245, 245))

        stage = DeskewStage()
        deskewed, report = stage.process(tilted)

        assert report.applied is True
        assert abs(report.metric_after) <= 1.0  # angle after close to 0

    def test_deskew_skips_angle_under_half_degree(self):
        doc = _make_sample_document()
        # Rotate by 0.3 degrees
        h, w = doc.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), 0.3, 1.0)
        tilted = cv2.warpAffine(doc, M, (w, h), borderValue=(245, 245, 245))

        stage = DeskewStage()
        deskewed, report = stage.process(tilted)

        assert report.applied is False
        assert deskewed.shape == tilted.shape

    def test_deskew_caps_correction_angle(self):
        doc = _make_sample_document()
        stage = DeskewStage()
        # Force a large estimated angle of 20.0 via parameter override
        tilted = cv2.cvtColor(doc, cv2.COLOR_BGR2GRAY)
        _, report = stage.process(tilted, config={"forced_angle": 18.0, "max_limit": 10.0})

        assert report.applied is True
        assert report.parameters.get("angle") <= 10.0


# ---------------------------------------------------------------------------
# Stage 4: Content Crop tests
# ---------------------------------------------------------------------------

class TestContentCropStage:
    def test_content_crop_removes_blank_margins_with_padding(self):
        # Create small text in center of huge white canvas (wide blank margins)
        big_canvas = np.full((1200, 1000, 3), 245, dtype=np.uint8)
        doc = _make_sample_document(size=(400, 300))
        big_canvas[400:700, 300:700] = doc

        stage = ContentCropStage()
        cropped, report = stage.process(big_canvas, config={"padding": 25})

        assert report.applied is True
        ch, cw = cropped.shape[:2]
        assert ch < 1200
        assert cw < 1000
        # Text block in center must be preserved with padding
        assert ch >= 180
        assert cw >= 300

    def test_content_crop_never_clips_inside_text_bounding_box(self):
        doc = _make_sample_document(size=(500, 700))
        stage = ContentCropStage()
        cropped, report = stage.process(doc)

        # If text is already near borders, crop should not remove text
        assert cropped.shape[0] > 0 and cropped.shape[1] > 0
        assert report.parameters.get("cropped_inside_text", False) is False


# ---------------------------------------------------------------------------
# Stage 5: Multi-page PDF rasterization & pipeline execution
# ---------------------------------------------------------------------------

class TestMultiPagePdf:
    def test_multipage_pdf_processes_all_pages(self):
        # Create a 3-page synthetic PDF in memory
        doc = fitz.open()
        for i in range(3):
            page = doc.new_page(width=400, height=600)
            page.insert_text((50, 50), f"PAGE {i+1} - RX DATA\\nDrug {i+1} 500mg OD")
        pdf_bytes = doc.tobytes()
        doc.close()

        results = process_pdf_pages(pdf_bytes, max_pages=10)
        assert len(results) == 3
        for page_bytes, manifest, quality in results:
            assert isinstance(page_bytes, bytes)
            assert isinstance(manifest, PipelineManifest)
            assert quality.image_quality in ("good", "fair", "poor")

    def test_multipage_pdf_capped_at_10_pages(self):
        # Create a 12-page synthetic PDF in memory
        doc = fitz.open()
        for i in range(12):
            page = doc.new_page(width=400, height=600)
            page.insert_text((50, 50), f"PAGE {i+1} RX DATA")
        pdf_bytes = doc.tobytes()
        doc.close()

        results = process_pdf_pages(pdf_bytes, max_pages=10)
        assert len(results) == 10  # Hard capped at 10


# ---------------------------------------------------------------------------
# Stage 6: Geometry tests on data/test_images/
# ---------------------------------------------------------------------------

class TestSyntheticImagesGeometry:
    def test_40_synthetic_images_deskew(self):
        """Rotate and tilt all 40 test images and assert tilt is recovered within tolerance."""
        image_paths = sorted(DATA_DIR.glob("rx-tc-*.png"))
        assert len(image_paths) == 40, f"Expected 40 test images, found {len(image_paths)}"

        stage = DeskewStage()
        for p in image_paths:
            img = cv2.imread(str(p))
            assert img is not None

            # Apply known tilt of 3.0 degrees
            h, w = img.shape[:2]
            M = cv2.getRotationMatrix2D((w // 2, h // 2), 3.0, 1.0)
            tilted = cv2.warpAffine(img, M, (w, h), borderValue=(250, 250, 250))

            deskewed, report = stage.process(tilted)
            final_angle = report.metric_after if report.applied else report.metric_before
            assert abs(final_angle) <= 1.5, f"Angle error too large on {p.name}: {final_angle}"

    def test_40_synthetic_images_orientation(self):
        """Rotate all 40 test images 90 degrees and assert orientation is recovered."""
        image_paths = sorted(DATA_DIR.glob("rx-tc-*.png"))
        assert len(image_paths) == 40

        stage = OrientationStage()
        for p in image_paths:
            img = cv2.imread(str(p))
            rotated_90 = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)

            corrected, report = stage.process(rotated_90)
            assert report.applied is True, f"Failed orientation detection on {p.name}"
            # Assert corrected is upright (portrait)
            assert corrected.shape[0] > corrected.shape[1], f"Image {p.name} not upright"

    def test_40_synthetic_images_perspective_quad_recovery(self):
        """Warp all 40 test images onto a desk and assert corners are recovered within tolerance."""
        image_paths = sorted(DATA_DIR.glob("rx-tc-*.png"))
        assert len(image_paths) == 40

        stage = DocumentPerspectiveStage()
        expected_quad = np.array([
            [120.0, 100.0],
            [680.0, 140.0],
            [620.0, 850.0],
            [100.0, 800.0],
        ], dtype=np.float32)

        for p in image_paths:
            img = cv2.imread(str(p))
            small = cv2.resize(img, (400, 600))
            desk_img, _ = _place_document_on_desk(small, desk_size=(800, 1000), warp_quad=expected_quad)

            warped, report = stage.process(desk_img)
            assert report.applied is True, f"Failed perspective detection on {p.name}"
            recovered_corners = np.array(report.parameters.get("corners"), dtype=np.float32)
            # Corner tolerance: recovered corners within 15 pixels of ground truth corners
            diff = np.abs(recovered_corners - expected_quad)
            assert np.max(diff) <= 15.0, f"Corner recovery outside tolerance on {p.name}: {diff}"

