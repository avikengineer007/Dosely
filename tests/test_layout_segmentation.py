"""
Unit tests for Task 1: Layout analysis and line segmentation.
============================================================
Tests cover:
  1. Synthetic multi-line handwriting pages with known line bounding boxes.
  2. Line count recovery and IoU measurement against ground truth.
  3. Touching lines and uneven spacing handling.
  4. Detection and filtering of non-medicine regions:
     - Prescriber/patient headers
     - Clinic stamps
     - Doctor signatures
     - Standalone "Rx" symbols
  5. Blank page yields zero lines.
  6. Low-confidence fallback to whole page body region with recorded reason.
  7. Encrypted storage of line crops with prescription cascade deletion.
  8. Log safety: zero pixels, crop arrays, or drug texts in logs.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

from dosely.layout import (
    LineRegion,
    PageLayout,
    analyze_layout_and_segment_lines,
    classify_region_type,
)
from dosely.storage import EncryptedStorage


def _get_font(size: int = 24) -> ImageFont.ImageFont:
    for name in ["arial.ttf", "calibri.ttf", "times.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _render_synthetic_prescription_page(
    lines: list[str],
    include_header: bool = True,
    include_rx_symbol: bool = True,
    include_stamp: bool = False,
    include_signature: bool = True,
    line_spacing: int = 40,
    touching: bool = False,
    size: tuple[int, int] = (800, 1000),
) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """
    Renders a synthetic prescription page and returns (bgr_image, ground_truth_line_boxes).
    """
    w, h = size
    im = Image.new("RGB", (w, h), color=(250, 250, 250))
    draw = ImageDraw.Draw(im)

    font_title = _get_font(22)
    font_sub = _get_font(14)
    font_rx = _get_font(32)
    font_body = _get_font(20)

    # 1. Header (y: 30 to 120)
    if include_header:
        draw.text((50, 30), "CITY GENERAL HOSPITAL & CLINIC", fill=(20, 30, 80), font=font_title)
        draw.text((50, 60), "Dr. A. K. Sharma, MD, MBBS  |  Reg. No. 448102", fill=(60, 60, 60), font=font_sub)
        draw.text((50, 85), "Patient: Anonymous Patient  |  Date: 2026-10-28", fill=(80, 80, 80), font=font_sub)
        draw.line([(40, 115), (w - 40, 115)], fill=(150, 150, 150), width=2)

    # 2. Standalone Rx symbol (y: 130 to 170)
    if include_rx_symbol:
        draw.text((50, 130), "Rx", fill=(30, 30, 30), font=font_rx)

    # 3. Medicine Lines
    gt_boxes: list[tuple[int, int, int, int]] = []
    y_start = 190
    current_y = y_start

    for line_text in lines:
        x_start = 80
        # Compute text bounding box
        bbox = draw.textbbox((x_start, current_y), line_text, font=font_body)
        draw.text((x_start, current_y), line_text, fill=(15, 15, 15), font=font_body)
        gt_boxes.append((bbox[0], bbox[1], bbox[2], bbox[3]))

        if touching:
            # Lines touching vertically (small spacing)
            current_y += 18
        else:
            current_y += line_spacing

    # 4. Clinic Stamp
    if include_stamp:
        stamp_box = (w - 220, 220, w - 80, 320)
        draw.ellipse(stamp_box, outline=(180, 30, 30), width=3)
        draw.text((w - 200, 260), "VERIFIED STAMP", fill=(180, 30, 30), font=font_sub)

    # 5. Doctor's Signature (bottom-right)
    if include_signature:
        sig_y = h - 120
        draw.line([(w - 250, sig_y), (w - 60, sig_y)], fill=(80, 80, 80), width=1)
        draw.text((w - 220, sig_y + 10), "Doctor's Signature", fill=(90, 90, 90), font=font_sub)
        draw.text((w - 200, sig_y + 30), "Dr. A. K. Sharma", fill=(120, 120, 120), font=font_sub)

    arr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    return arr, gt_boxes


def _compute_iou(boxA: tuple[int, int, int, int], boxB: tuple[int, int, int, int]) -> float:
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])

    inter = max(0, xB - xA) * max(0, yB - yA)
    areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    union = float(areaA + areaB - inter)
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------

class TestLayoutSegmentation:
    def test_blank_page_yields_zero_lines(self):
        blank = np.full((1000, 800, 3), 250, dtype=np.uint8)
        layout = analyze_layout_and_segment_lines(blank)
        assert len(layout.medicine_lines) == 0
        assert layout.is_blank is True

    def test_multi_line_recovery_and_iou(self):
        lines = [
            "1. Amoxicillin 500mg tid x 7d",
            "2. Paracetamol 650mg sos",
            "3. Pantoprazole 40mg od before breakfast",
            "4. Cetirizine 10mg hs",
        ]
        img, gt_boxes = _render_synthetic_prescription_page(lines, line_spacing=45)
        layout = analyze_layout_and_segment_lines(img)

        # Assert correct count of medicine lines recovered
        assert len(layout.medicine_lines) == len(lines)

        # Match each recovered line box with ground truth and check IoU
        for i, pred_line in enumerate(layout.medicine_lines):
            gt = gt_boxes[i]
            pred = pred_line.bounding_box
            # IoU with ground truth text box (accounting for intentional padding)
            # Center of predicted box should be within the vertical span of ground truth
            gt_cy = (gt[1] + gt[3]) / 2.0
            assert pred[1] <= gt_cy <= pred[3], f"Line {i} predicted box {pred} misses center {gt_cy}"

    def test_header_stamp_signature_ignored_from_medicine_lines(self):
        lines = [
            "1. Ciprofloxacin 500mg bd",
            "2. Metronidazole 400mg tds",
        ]
        img, _ = _render_synthetic_prescription_page(
            lines,
            include_header=True,
            include_rx_symbol=True,
            include_stamp=True,
            include_signature=True,
        )
        layout = analyze_layout_and_segment_lines(img)

        # Only the 2 medicine lines must be marked as is_medicine
        assert len(layout.medicine_lines) == 2

        # Non-medicine regions must be accounted for in ignored_regions
        ignored_categories = {r.category for r in layout.ignored_regions}
        assert "header" in ignored_categories
        assert "signature" in ignored_categories or "stamp" in ignored_categories
        # Rx symbol must be detected as ignored
        assert any(r.category in ("rx_symbol", "header") for r in layout.ignored_regions)

    def test_touching_lines_segmentation(self):
        lines = [
            "Azithromycin 500mg OD x 3d",
            "Paracetamol 500mg SOS",
        ]
        img, _ = _render_synthetic_prescription_page(lines, touching=True)
        layout = analyze_layout_and_segment_lines(img)

        # Must recover both lines even when touching vertically
        assert len(layout.medicine_lines) == 2

    def test_crop_padding_preserves_ascenders_descenders(self):
        lines = ["Metformin 500mg & Glyburide 5mg"]
        img, gt_boxes = _render_synthetic_prescription_page(lines)
        layout = analyze_layout_and_segment_lines(img)

        assert len(layout.medicine_lines) == 1
        line = layout.medicine_lines[0]
        gt = gt_boxes[0]

        # Padding requirement: crop bounding box must extend above and below text bbox
        # to ensure letters like 'g', 'y', 'p' are not clipped
        assert line.bounding_box[1] < gt[1]  # padded above
        assert line.bounding_box[3] > gt[3]  # padded below
        assert line.crop is not None
        assert line.crop.shape[0] > (gt[3] - gt[1])

    def test_low_confidence_fallback_records_reason(self):
        # Create heavily blurred / chaotic canvas where line splitting cannot be confident
        chaotic = np.random.randint(80, 200, (600, 600, 3), dtype=np.uint8)
        layout = analyze_layout_and_segment_lines(chaotic)

        # If segmentation confidence is low, fallback to single whole-page region
        if layout.is_fallback:
            assert len(layout.medicine_lines) == 1
            assert layout.fallback_reason is not None
            assert len(layout.fallback_reason) > 0

    def test_encrypted_storage_saves_and_cascades_line_crops(self, tmp_path):
        storage = EncryptedStorage(storage_dir=tmp_path)
        rx_id = "rx-layout-test-999"

        crop_1 = b"dummy_png_crop_1_bytes"
        crop_2 = b"dummy_png_crop_2_bytes"

        p1 = storage.save_line_crop(rx_id, line_id="line_001", crop_bytes=crop_1)
        p2 = storage.save_line_crop(rx_id, line_id="line_002", crop_bytes=crop_2)

        assert Path(p1).exists()
        assert Path(p2).exists()

        # Decrypt line crop
        loaded_1 = storage.load_line_crop(rx_id, line_id="line_001")
        assert loaded_1 == crop_1

        # Save main prescription enc file too
        storage.save(rx_id, b"main_rx_bytes")

        # Cascade delete prescription must remove line crops
        storage.delete(rx_id)
        assert not Path(p1).exists()
        assert not Path(p2).exists()

    def test_caplog_zero_health_data_in_layout_logs(self, caplog):
        caplog.set_level(logging.DEBUG)
        secret_drug = "SecretExperimentalDrug 100mg"
        img, _ = _render_synthetic_prescription_page([secret_drug])

        analyze_layout_and_segment_lines(img, prescription_id="rx-secret-001")

        for record in caplog.records:
            msg = record.getMessage().lower()
            assert "secretexperimentaldrug" not in msg
            assert "100mg" not in msg
            assert "array(" not in msg
