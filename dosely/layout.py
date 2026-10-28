"""
Prescription Document Layout Analysis & Line Segmentation
=========================================================
Staged layout engine for detecting document text regions, splitting
handwritten and printed lines, and isolating non-medicine areas (headers,
stamps, signatures, Rx symbols).

Design Principles:
  1. No health data in logs: Log only line counts, IDs, bounding boxes,
     durations, and reason codes. Zero pixel arrays or text dumps in logs.
  2. Every region is accounted for: Kept as medicine, ignored with reason,
     or escalated as fallback. Never drop regions silently.
  3. Safe line crops: Generous padding ensures ascenders ('t','d','b','h')
     and descenders ('g','y','p','q') are preserved without clipping.
  4. Encrypted storage: Line crops are stored with the same MultiFernet
     symmetric encryption as source images.
  5. Full-resolution line crops: Line segmentation and line crops MUST ALWAYS
     be cut from the full-resolution image BEFORE any API downscale occurs.
     Downscaling the entire document first destroys fine pen strokes and character contours.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any, Optional, Sequence

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class LineRegion:
    """A detected text line or functional region on the prescription."""
    line_id: str
    order_index: int
    bounding_box: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    region_type: str = "handwritten"  # "handwritten" | "printed" | "mixed"
    category: str = "medicine_line"   # "medicine_line" | "header" | "stamp" | "signature" | "rx_symbol" | "footer"
    is_medicine: bool = True
    ignore_reason: Optional[str] = None
    crop: Optional[np.ndarray] = None  # In-memory crop with padding; never logged
    confidence: float = 1.0


@dataclass
class PageLayout:
    """Complete layout analysis result for a prescription page."""
    prescription_id: Optional[str] = None
    medicine_lines: list[LineRegion] = field(default_factory=list)
    ignored_regions: list[LineRegion] = field(default_factory=list)
    is_blank: bool = False
    is_fallback: bool = False
    fallback_reason: Optional[str] = None
    duration_ms: float = 0.0

    @property
    def line_count(self) -> int:
        return len(self.medicine_lines)

    def to_manifest_dict(self) -> dict[str, Any]:
        """
        Serializable audit dictionary safe for logging and persisting at rest.
        Contains IDs, counts, bounding boxes, categories, and reasons.
        ZERO pixel content or transcribed text.
        """
        return {
            "prescription_id": self.prescription_id,
            "line_count": len(self.medicine_lines),
            "ignored_count": len(self.ignored_regions),
            "is_blank": self.is_blank,
            "is_fallback": self.is_fallback,
            "fallback_reason": self.fallback_reason,
            "duration_ms": round(self.duration_ms, 2),
            "medicine_lines": [
                {
                    "line_id": r.line_id,
                    "order_index": r.order_index,
                    "bounding_box": list(r.bounding_box),
                    "region_type": r.region_type,
                    "confidence": round(r.confidence, 3),
                }
                for r in self.medicine_lines
            ],
            "ignored_regions": [
                {
                    "line_id": r.line_id,
                    "bounding_box": list(r.bounding_box),
                    "category": r.category,
                    "ignore_reason": r.ignore_reason,
                }
                for r in self.ignored_regions
            ],
        }


# ---------------------------------------------------------------------------
# Region Classification Helpers
# ---------------------------------------------------------------------------

def classify_region_type(crop_gray: np.ndarray) -> str:
    """
    Classify crop as 'printed', 'handwritten', or 'mixed'.
    Uses stroke thickness variance, gradient entropy, and connected component aspect ratios.
    """
    h, w = crop_gray.shape[:2]
    if h < 5 or w < 5:
        return "printed"

    bin_crop = cv2.adaptiveThreshold(
        crop_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 6
    )
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(bin_crop, connectivity=8)
    if num_labels <= 2:
        return "printed"

    widths = stats[1:, cv2.CC_STAT_WIDTH]
    heights = stats[1:, cv2.CC_STAT_HEIGHT]
    valid = (heights >= 4) & (widths >= 2)
    if not np.any(valid):
        return "printed"

    # Handwriting typically shows higher aspect ratio variance and stroke width irregularity
    aspect_ratios = widths[valid].astype(np.float32) / np.maximum(1, heights[valid]).astype(np.float32)
    ar_std = float(np.std(aspect_ratios))

    # Stroke thickness variance via distance transform
    dist = cv2.distanceTransform(bin_crop, cv2.DIST_L2, 3)
    stroke_vals = dist[dist > 0.5]
    stroke_std = float(np.std(stroke_vals)) if len(stroke_vals) > 10 else 0.0

    if stroke_std > 1.25 or ar_std > 0.85:
        return "handwritten"
    elif stroke_std > 0.8:
        return "mixed"
    return "printed"


# ---------------------------------------------------------------------------
# Core Layout & Line Segmentation Engine
# ---------------------------------------------------------------------------

def analyze_layout_and_segment_lines(
    img: np.ndarray,
    prescription_id: Optional[str] = None,
    vertical_padding_px: int = 10,
    horizontal_padding_px: int = 15,
) -> PageLayout:
    """
    Segment prescription page into ordered line regions, classifying and
    filtering non-medicine functional zones (headers, stamps, signatures, Rx).

    Parameters:
      img: Input BGR or Grayscale prescription image.
      prescription_id: Optional tracking identifier.
      vertical_padding_px: Padding above and below line to protect ascenders/descenders.
      horizontal_padding_px: Padding left and right of line.

    Returns:
      PageLayout with medicine lines and ignored functional regions.
    """
    t0 = time.perf_counter()
    h, w = img.shape[:2]

    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img.copy()

    # 1. Blank Page Check
    bin_inv = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 8
    )
    ink_ratio = float(np.count_nonzero(bin_inv)) / float(h * w)
    if ink_ratio < 0.0005:
        dt = (time.perf_counter() - t0) * 1000.0
        return PageLayout(
            prescription_id=prescription_id,
            medicine_lines=[],
            ignored_regions=[],
            is_blank=True,
            duration_ms=dt,
        )

    # 2. Extract Connected Components
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(bin_inv, connectivity=8)

    # Filter background, tiny noise dots, and whole-page borders
    valid_components: list[tuple[int, int, int, int, int]] = []  # (x, y, w, h, area)
    for i in range(1, num_labels):
        cx, cy = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
        cw, ch = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        carea = stats[i, cv2.CC_STAT_AREA]

        if carea < 6 or (cw < 3 and ch < 3):
            continue
        if cw > w * 0.95 and ch > h * 0.95:
            continue
        valid_components.append((cx, cy, cw, ch, carea))

    if not valid_components:
        dt = (time.perf_counter() - t0) * 1000.0
        return PageLayout(
            prescription_id=prescription_id,
            medicine_lines=[],
            ignored_regions=[],
            is_blank=True,
            duration_ms=dt,
        )

    # 3. Cluster all valid components into horizontal bands
    valid_components.sort(key=lambda c: (c[1], c[0]))
    median_h = float(np.median([c[3] for c in valid_components]))
    vertical_group_threshold = max(10.0, median_h * 0.75)

    raw_clusters: list[list[tuple[int, int, int, int, int]]] = []
    for comp in valid_components:
        cx, cy, cw, ch, carea = comp
        c_mid_y = cy + ch / 2.0
        placed = False
        for cluster in raw_clusters:
            cl_y1 = min(c[1] for c in cluster)
            cl_y2 = max(c[1] + c[3] for c in cluster)
            cl_mid_y = (cl_y1 + cl_y2) / 2.0
            if abs(c_mid_y - cl_mid_y) <= vertical_group_threshold:
                cluster.append(comp)
                placed = True
                break
        if not placed:
            raw_clusters.append([comp])

    # Handle touching lines splitting: check if any cluster spans multiple line heights
    refined_clusters: list[list[tuple[int, int, int, int, int]]] = []
    for cluster in raw_clusters:
        c_y1 = min(c[1] for c in cluster)
        c_y2 = max(c[1] + c[3] for c in cluster)
        c_height = c_y2 - c_y1

        if c_height > max(35.0, median_h * 2.1) and len(cluster) >= 4:
            sub_mask = np.zeros((c_height + 1, w), dtype=np.uint8)
            for (cx, cy, cw, ch, _) in cluster:
                sub_mask[cy - c_y1 : cy - c_y1 + ch, cx : cx + cw] = 255

            proj = np.sum(sub_mask, axis=1)
            mid_start = int(c_height * 0.3)
            mid_end = int(c_height * 0.7)
            if mid_end > mid_start:
                split_offset = mid_start + int(np.argmin(proj[mid_start:mid_end]))
                split_y = c_y1 + split_offset

                c1 = [c for c in cluster if (c[1] + c[3] / 2.0) <= split_y]
                c2 = [c for c in cluster if (c[1] + c[3] / 2.0) > split_y]
                if c1 and c2:
                    refined_clusters.append(c1)
                    refined_clusters.append(c2)
                    continue

        refined_clusters.append(cluster)

    # 4. Classify each band into medicine line vs non-medicine functional region
    medicine_lines: list[LineRegion] = []
    ignored_regions: list[LineRegion] = []
    ignored_counter = 0

    header_y_max = int(h * 0.15)
    footer_y_min = int(h * 0.92)
    sig_y_min = int(h * 0.75)
    sig_x_min = int(w * 0.35)

    for cluster in refined_clusters:
        bx1 = min(c[0] for c in cluster)
        by1 = min(c[1] for c in cluster)
        bx2 = max(c[0] + c[2] for c in cluster)
        by2 = max(c[1] + c[3] for c in cluster)
        band_w = bx2 - bx1
        band_h = by2 - by1

        # Skip tiny isolated noise speckles
        total_cluster_area = sum(c[4] for c in cluster)
        if total_cluster_area < 25 or band_w < 12 or band_h < 6:
            continue

        # A. Header check
        if by2 <= header_y_max or (by1 <= header_y_max and band_w >= 0.35 * w):
            ignored_counter += 1
            ignored_regions.append(
                LineRegion(
                    line_id=f"ignored_{ignored_counter}",
                    order_index=0,
                    bounding_box=(bx1, by1, bx2, by2),
                    region_type="printed",
                    category="header",
                    is_medicine=False,
                    ignore_reason="prescriber_and_clinic_header",
                )
            )
            continue

        # B. Standalone Rx symbol check (isolated near top-left, not a long text line)
        if by1 <= int(0.22 * h) and bx1 <= int(0.15 * w) and band_w <= int(0.18 * w):
            ignored_counter += 1
            ignored_regions.append(
                LineRegion(
                    line_id=f"ignored_{ignored_counter}",
                    order_index=0,
                    bounding_box=(bx1, by1, bx2, by2),
                    region_type="printed",
                    category="rx_symbol",
                    is_medicine=False,
                    ignore_reason="prescription_symbol",
                )
            )
            continue

        # C. Clinic Stamp (dense cluster in right-hand body or seal area)
        if (int(0.16 * h) <= by1 <= int(0.68 * h)) and (bx1 >= int(0.50 * w)) and (band_w >= 50 or band_h >= 50):
            ignored_counter += 1
            ignored_regions.append(
                LineRegion(
                    line_id=f"ignored_{ignored_counter}",
                    order_index=0,
                    bounding_box=(bx1, by1, bx2, by2),
                    region_type="mixed",
                    category="stamp",
                    is_medicine=False,
                    ignore_reason="clinic_verification_stamp",
                )
            )
            continue

        # D. Doctor Signature (bottom right quadrant)
        if by1 >= sig_y_min and bx1 >= sig_x_min:
            ignored_counter += 1
            ignored_regions.append(
                LineRegion(
                    line_id=f"ignored_{ignored_counter}",
                    order_index=0,
                    bounding_box=(bx1, by1, bx2, by2),
                    region_type="handwritten",
                    category="signature",
                    is_medicine=False,
                    ignore_reason="doctor_signature_block",
                )
            )
            continue

        # E. Page Footer
        if by1 >= footer_y_min:
            ignored_counter += 1
            ignored_regions.append(
                LineRegion(
                    line_id=f"ignored_{ignored_counter}",
                    order_index=0,
                    bounding_box=(bx1, by1, bx2, by2),
                    region_type="printed",
                    category="footer",
                    is_medicine=False,
                    ignore_reason="page_footer_meta",
                )
            )
            continue

        # F. Medicine Line (apply safe ascender/descender padding)
        lx1 = max(0, bx1 - horizontal_padding_px)
        ly1 = max(0, by1 - vertical_padding_px)
        lx2 = min(w, bx2 + horizontal_padding_px)
        ly2 = min(h, by2 + vertical_padding_px)

        crop = img[ly1:ly2, lx1:lx2]
        crop_gray = gray[ly1:ly2, lx1:lx2]
        reg_type = classify_region_type(crop_gray)

        medicine_lines.append(
            LineRegion(
                line_id="",
                order_index=0,
                bounding_box=(lx1, ly1, lx2, ly2),
                region_type=reg_type,
                category="medicine_line",
                is_medicine=True,
                crop=crop,
                confidence=0.94,
            )
        )

    # Sort final medicine lines from top to bottom and assign line IDs
    medicine_lines.sort(key=lambda r: r.bounding_box[1])
    for order, r in enumerate(medicine_lines):
        r.order_index = order
        r.line_id = f"line_{order + 1:03d}"

    # 5. Fallback Check: Low Confidence Fallback
    # If medicine candidates were present but no clean lines could be formed or segmentation confidence < 0.4
    # (e.g., highly chaotic noise, unresolvable clutter where cluster count is abnormal, or 0 lines extracted)
    is_chaotic = len(valid_components) > 250 or len(refined_clusters) > 40
    if (not medicine_lines and valid_components) or is_chaotic:
        dt = (time.perf_counter() - t0) * 1000.0
        bx1 = max(0, min(c[0] for c in valid_components) - horizontal_padding_px)
        by1 = max(0, min(c[1] for c in valid_components) - vertical_padding_px)
        bx2 = min(w, max(c[0] + c[2] for c in valid_components) + horizontal_padding_px)
        by2 = min(h, max(c[1] + c[3] for c in valid_components) + vertical_padding_px)

        fallback_crop = img[by1:by2, bx1:bx2]
        fallback_line = LineRegion(
            line_id="line_fallback_001",
            order_index=0,
            bounding_box=(bx1, by1, bx2, by2),
            region_type="mixed",
            category="medicine_line",
            is_medicine=True,
            crop=fallback_crop,
            confidence=0.35,
        )
        return PageLayout(
            prescription_id=prescription_id,
            medicine_lines=[fallback_line],
            ignored_regions=ignored_regions,
            is_blank=False,
            is_fallback=True,
            fallback_reason="low_segmentation_confidence_whole_page_body_preserved",
            duration_ms=dt,
        )

    dt = (time.perf_counter() - t0) * 1000.0
    return PageLayout(
        prescription_id=prescription_id,
        medicine_lines=medicine_lines,
        ignored_regions=ignored_regions,
        is_blank=False,
        is_fallback=False,
        fallback_reason=None,
        duration_ms=dt,
    )
