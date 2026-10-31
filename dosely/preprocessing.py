"""
Prescription Image Preprocessing Pipeline
=========================================
Staged, measurable, safe preprocessing pipeline and quality assessment
for prescription photos.

Architecture:
  - Small pure stages: (image in, image + StageReport out).
  - PipelineManifest: records stages applied, durations, before/after metrics,
    and warnings. Contains zero pixel content; safe to log and store.
  - QualityReport: comprehensive assessment of input image across sharpness,
    motion blur, brightness, contrast, resolution, text height, glare, skew,
    perspective distortion, shadow severity, and page coverage.
  - Safety Invariant: Preprocessing may only make an image easier to read.
    Enhancement can NEVER upgrade a poor source image to good.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import io
import logging
import math
import time
from typing import Any, Protocol, Sequence, Tuple

import cv2  # type: ignore
import numpy as np
from PIL import Image, ImageOps

from dosely.config import (
    CLAHE_CLIP_LIMIT,
    CLAHE_TILE_GRID,
    DESKEW_LIMIT,
    DESKEW_MIN_ANGLE,
    MARGIN_PADDING_PX,
    MAX_PERSPECTIVE_STRETCH,
    MAX_PIXEL_COUNT,
    PDF_MAX_PAGES,
    PIPELINE_STAGES,
    PREPROCESSING_TIMEOUT_SECONDS,
    QUALITY_THRESHOLDS,
    STAGE_BINARIZATION_ENABLED,
    STAGE_CONTRAST_ENABLED,
    STAGE_CROP_ENABLED,
    STAGE_DENOISE_ENABLED,
    STAGE_DESKEW_ENABLED,
    STAGE_GRAYSCALE_ENABLED,
    STAGE_ILLUMINATION_ENABLED,
    STAGE_ORIENTATION_ENABLED,
    STAGE_PERSPECTIVE_ENABLED,
    STAGE_RESOLUTION_ENABLED,
    STAGE_SHARPEN_ENABLED,
    TARGET_TEXT_HEIGHT_PX,
)

logger = logging.getLogger(__name__)

# PDF support: prefer permissively licensed pypdfium2 (Apache-2.0 / BSD) over PyMuPDF (AGPL)
try:
    import pypdfium2 as pdfium  # type: ignore
    _PYPDFIUM_AVAILABLE = True
except ImportError:
    pdfium = None  # type: ignore
    _PYPDFIUM_AVAILABLE = False

try:
    import pymupdf as fitz  # type: ignore
    _PYMUPDF_AVAILABLE = True
except ImportError:
    try:
        import fitz  # type: ignore
        _PYMUPDF_AVAILABLE = True
    except ImportError:
        fitz = None  # type: ignore
        _PYMUPDF_AVAILABLE = False

if not _PYPDFIUM_AVAILABLE and not _PYMUPDF_AVAILABLE:
    logger.warning(
        "Neither pypdfium2 nor PyMuPDF installed. PDF input is disabled. "
        "Install with: pip install pypdfium2"
    )


PDF_DPI: int = 200


# ---------------------------------------------------------------------------
# Data Models: StageReport, PipelineManifest, QualityReport
# ---------------------------------------------------------------------------

@dataclass
class StageReport:
    """Report produced by an individual pipeline stage."""
    name: str
    applied: bool
    parameters: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    metric_before: float | None = None
    metric_after: float | None = None
    warnings: list[str] = field(default_factory=list)
    skip_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.applied and self.skip_reason is None:
            self.skip_reason = (
                self.parameters.get("skipped_reason")
                or self.parameters.get("reason")
                or ("already_upright" if self.name == "orientation" and self.parameters.get("angle_fixed") == 0 else None)
                or ("skew_below_threshold" if self.name == "deskew" and "min_angle" in self.parameters else None)
                or ("disabled_by_config" if self.parameters.get("enabled") is False else "skipped")
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "applied": self.applied,
            "skip_reason": self.skip_reason,
            "parameters": self.parameters,
            "duration_ms": round(self.duration_ms, 2),
            "metric_before": round(self.metric_before, 3) if self.metric_before is not None else None,
            "metric_after": round(self.metric_after, 3) if self.metric_after is not None else None,
            "warnings": self.warnings,
        }


@dataclass
class PipelineManifest:
    """
    Complete audit trail of pipeline execution.
    Contains strictly operational metrics and stage names — zero raw image pixels,
    paths, or medical/text content. Safe to log and store.
    """
    stages: list[StageReport] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    total_duration_ms: float = 0.0
    timed_out: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [s.to_dict() for s in self.stages],
            "metrics": self.metrics,
            "warnings": self.warnings,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "timed_out": self.timed_out,
        }

    def summary(self) -> str:
        applied = [s.name for s in self.stages if s.applied]
        return (
            f"PipelineManifest(stages_applied={applied}, "
            f"total_ms={self.total_duration_ms:.1f}, warnings={len(self.warnings)})"
        )


@dataclass
class QualityReport:
    """
    Multi-dimensional quality assessment computed on the source image.
    Maps to image_quality ('good', 'fair', 'poor') and human-readable reasons.
    """
    image_quality: str  # "good", "fair", "poor"
    reasons: list[str]  # Human-readable reasons for UI
    sharpness: float
    motion_blur: float
    brightness_mean: float
    brightness_clipping_percent: float
    contrast_rms: float
    contrast_percentile_spread: float
    resolution_megapixels: float
    estimated_text_height_px: float
    glare_percent: float
    skew_angle: float
    perspective_distortion: float
    shadow_severity: float
    page_coverage: float
    hard_failure: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_quality": self.image_quality,
            "reasons": self.reasons,
            "hard_failure": self.hard_failure,
            "metrics": {
                "sharpness": round(self.sharpness, 2),
                "motion_blur": round(self.motion_blur, 3),
                "brightness_mean": round(self.brightness_mean, 2),
                "brightness_clipping_percent": round(self.brightness_clipping_percent, 2),
                "contrast_rms": round(self.contrast_rms, 2),
                "contrast_percentile_spread": round(self.contrast_percentile_spread, 2),
                "resolution_megapixels": round(self.resolution_megapixels, 3),
                "estimated_text_height_px": round(self.estimated_text_height_px, 1),
                "glare_percent": round(self.glare_percent, 2),
                "skew_angle": round(self.skew_angle, 2),
                "perspective_distortion": round(self.perspective_distortion, 3),
                "shadow_severity": round(self.shadow_severity, 2),
                "page_coverage": round(self.page_coverage, 2),
            },
        }


# ---------------------------------------------------------------------------
# PDF and Array Helpers (Preserved and backwards-compatible)
# ---------------------------------------------------------------------------

def _is_pdf(data: bytes) -> bool:
    """Check for PDF magic bytes."""
    return data[:4] == b"%PDF"


def _pdf_to_image_bytes(data: bytes, page_index: int = 0) -> bytes:
    """Rasterise one PDF page to PNG bytes using pypdfium2 (permissive) or fitz fallback."""
    if _PYPDFIUM_AVAILABLE and pdfium is not None:
        doc = pdfium.PdfDocument(data)
        try:
            if page_index >= len(doc):
                raise ValueError(
                    f"PDF has {len(doc)} page(s); requested page {page_index}."
                )
            page = doc[page_index]
            pil_image = page.render(scale=PDF_DPI / 72.0).to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            return buf.getvalue()
        finally:
            doc.close()
    elif _PYMUPDF_AVAILABLE and fitz is not None:
        doc = fitz.open(stream=data, filetype="pdf")
        try:
            if page_index >= len(doc):
                raise ValueError(
                    f"PDF has {len(doc)} page(s); requested page {page_index}."
                )
            page = doc[page_index]
            mat = fitz.Matrix(PDF_DPI / 72, PDF_DPI / 72)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            png_bytes: bytes = pix.tobytes("png")
            return png_bytes
        finally:
            doc.close()
    else:
        raise RuntimeError(
            "pypdfium2 or PyMuPDF is required for PDF processing. "
            "Install pypdfium2 with: pip install pypdfium2"
        )


def _bytes_to_array(data: bytes) -> np.ndarray:
    """Decode image bytes (any common format) to a numpy BGR array."""
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(
            "Could not decode image bytes. "
            "Ensure the input is a valid JPEG, PNG, TIFF, or BMP."
        )
    return img


def _to_grayscale(img: np.ndarray) -> np.ndarray:
    """Convert BGR or already-grayscale array to single-channel uint8."""
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _array_to_png_bytes(img: np.ndarray) -> bytes:
    """Encode numpy array to PNG bytes."""
    success, buf = cv2.imencode(".png", img)
    if not success:
        raise RuntimeError("cv2.imencode failed during PNG encoding.")
    return buf.tobytes()


# ---------------------------------------------------------------------------
# Metric computation helpers
# ---------------------------------------------------------------------------

def _compute_sharpness(gray: np.ndarray) -> float:
    """Laplacian variance sharpness metric."""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _compute_motion_blur(gray: np.ndarray) -> float:
    """
    Directional gradient anisotropy estimate for motion blur.
    Returns 0.0 (isotropic, sharp/uniform) to 1.0 (strong directional streak).
    """
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    var_x = float(np.var(gx))
    var_y = float(np.var(gy))
    denom = var_x + var_y
    if denom < 1e-6:
        return 0.0
    return float(abs(var_x - var_y) / denom)


def _compute_brightness(gray: np.ndarray) -> tuple[float, float]:
    """Returns (mean_brightness, clipping_percentage)."""
    mean_val = float(np.mean(gray))
    under = float(np.mean(gray <= 10)) * 100.0
    over = float(np.mean(gray >= 245)) * 100.0
    return mean_val, under + over


def _compute_contrast(gray: np.ndarray) -> tuple[float, float]:
    """Returns (rms_contrast, percentile_spread_99_1)."""
    rms = float(np.std(gray))
    p99, p1 = np.percentile(gray, [99, 1])
    return rms, float(p99 - p1)


def _compute_resolution_and_text_height(img: np.ndarray) -> tuple[float, float]:
    """
    Computes megapixels and estimated median text height in pixels.
    Uses connected components analysis on dark ink strokes.
    """
    h, w = img.shape[:2]
    megapixels = (h * w) / 1_000_000.0

    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Downscale large images for character analysis speed
    scale = 1.0
    if max(h, w) > 1200:
        scale = 1200.0 / max(h, w)
        work_gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    else:
        work_gray = gray

    # Adaptive binarization to extract ink strokes
    block_size = max(15, (int(min(work_gray.shape) * 0.03) // 2) * 2 + 1)
    binary = cv2.adaptiveThreshold(
        work_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block_size, 10
    )

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    sh, sw = work_gray.shape

    heights: list[float] = []
    for i in range(1, num_labels):
        cw = stats[i, cv2.CC_STAT_WIDTH]
        ch = stats[i, cv2.CC_STAT_HEIGHT]
        carea = stats[i, cv2.CC_STAT_AREA]

        # Ignore full-width borders, page lines, or tiny noise dots
        if cw > sw * 0.85 or ch > sh * 0.85:
            continue
        if ch < 3 or cw < 2 or carea < 6:
            continue
        # Stroke / character aspect ratio filtering
        if 0.1 <= (cw / ch) <= 5.0 and ch <= sh * 0.25:
            heights.append(ch / scale)

    if len(heights) >= 4:
        estimated_height = float(np.median(heights))
    else:
        estimated_height = 0.0

    return megapixels, estimated_height



def _compute_glare(gray: np.ndarray) -> float:
    """
    Detects saturated specular highlights/blobs (>248) and returns percentage area.
    Uniform white digital scans (>85% white) are not considered glare.
    """
    sat_mask = (gray >= 248).astype(np.uint8) * 255
    total_sat_fraction = float(np.mean(sat_mask > 0))
    # If almost the whole page is digitally 100% white (> 85%), it's a digital render/scan, not glare
    if total_sat_fraction > 0.85:
        return 0.0

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    opened = cv2.morphologyEx(sat_mask, cv2.MORPH_OPEN, kernel)
    glare_pixels = int(np.count_nonzero(opened))
    return float((glare_pixels / gray.size) * 100.0)


def _estimate_skew_angle(gray: np.ndarray, max_detect_angle: float = 45.0) -> float:
    """
    Estimate dominant text baseline tilt angle using Hough lines and contour minAreaRect fallback.
    Returns angle in [-max_detect_angle, +max_detect_angle] or 0.0.
    """
    # 1. Hough lines method
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 15, 4
    )

    lines = cv2.HoughLinesP(
        binary,
        rho=1,
        theta=math.pi / 180,
        threshold=80,
        minLineLength=max(20, gray.shape[1] // 8),
        maxLineGap=20,
    )

    if lines is not None and len(lines) > 0:
        lines_flat = lines.reshape(-1, 4)
        angles: list[float] = []
        for x1, y1, x2, y2 in lines_flat:
            dx, dy = int(x2) - int(x1), int(y2) - int(y1)
            if dx == 0:
                continue
            angle = math.degrees(math.atan2(dy, dx))
            if abs(angle) <= max_detect_angle:
                angles.append(angle)
        if angles:
            return float(np.median(angles))

    # 2. Fallback: minAreaRect on non-trivial text contours
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    rect_angles: list[float] = []
    for c in contours:
        if cv2.contourArea(c) < 50:
            continue
        rect = cv2.minAreaRect(c)
        angle = rect[-1]
        if angle < -45:
            angle = 90 + angle
        if abs(angle) <= max_detect_angle:
            rect_angles.append(angle)

    if rect_angles:
        return float(np.median(rect_angles))

    return 0.0



def _compute_perspective_and_coverage(gray: np.ndarray) -> tuple[float, float]:
    """
    Detects paper quadrilateral to estimate perspective distortion and page coverage.
    Returns (distortion_score, page_coverage_percentage).
    """
    h, w = gray.shape
    total_area = float(h * w)

    # Edge detection
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_quad = None
    max_area = 0.0

    for c in contours:
        area = cv2.contourArea(c)
        if area < total_area * 0.15:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        if len(approx) == 4 and area > max_area:
            best_quad = approx
            max_area = area

    if best_quad is not None:
        pts = best_quad.reshape(4, 2)
        # Order corners: top-left, top-right, bottom-right, bottom-left
        s = pts.sum(axis=1)
        diff = np.diff(pts, axis=1)
        tl = pts[np.argmin(s)]
        br = pts[np.argmax(s)]
        tr = pts[np.argmin(diff)]
        bl = pts[np.argmax(diff)]

        top_len = float(np.linalg.norm(tr - tl))
        bottom_len = float(np.linalg.norm(br - bl))
        left_len = float(np.linalg.norm(bl - tl))
        right_len = float(np.linalg.norm(br - tr))

        h_ratio = abs(top_len - bottom_len) / max(top_len, bottom_len, 1e-5)
        v_ratio = abs(left_len - right_len) / max(left_len, right_len, 1e-5)
        distortion = float(max(h_ratio, v_ratio))
        coverage = float((max_area / total_area) * 100.0)
        return distortion, coverage

    # Document fills the frame (e.g. clean crop or flat scan)
    return 0.0, 100.0


def _compute_shadow_severity(gray: np.ndarray) -> float:
    """
    Measures standard deviation of patch means across an 8x8 spatial grid.
    Higher values indicate severe uneven shadows or harsh illumination falloff.
    """
    h, w = gray.shape
    rows, cols = 8, 8
    bh, bw = h // rows, w // cols
    if bh < 4 or bw < 4:
        return 0.0

    means: list[float] = []
    for r in range(rows):
        for c in range(cols):
            patch = gray[r * bh : (r + 1) * bh, c * bw : (c + 1) * bw]
            means.append(float(np.mean(patch)))
    return float(np.std(means))


# ---------------------------------------------------------------------------
# Quality Assessment Engine
# ---------------------------------------------------------------------------

def assess_quality(
    img: np.ndarray,
    thresholds: dict[str, float] | None = None,
) -> QualityReport:
    """
    Comprehensive multi-dimensional quality assessment computed on the input image.

    Returns a QualityReport containing metrics, quality tier ('good', 'fair', 'poor'),
    and human-readable reason codes for UI presentation.
    """
    th = {**QUALITY_THRESHOLDS, **(thresholds or {})}
    gray = _to_grayscale(img)

    sharpness = _compute_sharpness(gray)
    motion_blur = _compute_motion_blur(gray)
    brightness_mean, clipping = _compute_brightness(gray)
    contrast_rms, contrast_spread = _compute_contrast(gray)
    resolution_mp, text_height = _compute_resolution_and_text_height(img)
    glare_pct = _compute_glare(gray)
    skew = _estimate_skew_angle(gray)
    perspective_dist, coverage = _compute_perspective_and_coverage(gray)
    shadow_sev = _compute_shadow_severity(gray)

    reasons: list[str] = []
    hard_failure = False

    # Check for blank page or uniform canvas
    if sharpness < 5.0 and (contrast_rms < 5.0 or contrast_spread < 10.0):
        reasons.append("Blank page or no content detected")
        hard_failure = True

    # Check for pure random noise (high frequency variance with zero text structure)
    if text_height == 0.0 and sharpness > 300.0 and contrast_rms > 40.0:
        reasons.append("Unreadable image or random noise")
        hard_failure = True

    # Hard checks that immediately force 'poor'
    if sharpness < th["sharpness_poor"]:
        reasons.append("Image is too blurry")
        hard_failure = True

    if brightness_mean < th["brightness_min_poor"]:
        reasons.append("Image is too dark")
        hard_failure = True

    if brightness_mean > th["brightness_max_poor"] and (contrast_spread < th["contrast_spread_min_fair"] or contrast_rms < th["contrast_rms_min_fair"]):
        reasons.append("Image is too bright and washed out")
        hard_failure = True

    if glare_pct > th["glare_max_poor"]:
        reasons.append("Severe glare over text")
        hard_failure = True
    elif glare_pct > th["glare_max_fair"]:
        reasons.append("Glare detected")

    if contrast_rms < th["contrast_rms_min_poor"] or contrast_spread < th["contrast_spread_min_poor"]:
        reasons.append("Contrast is too low to read text")
        hard_failure = True

    # Text height hard floor (if text was detected but is below minimum floor)
    if 0.0 < text_height < th["min_text_height_hard_floor"]:
        reasons.append("Text is too small for reliable extraction")
        hard_failure = True
    elif text_height == 0.0 and not hard_failure:
        # Non-blank but no readable text detected
        reasons.append("No readable text detected")
        hard_failure = True

    # Fair checks / Warnings
    if not hard_failure:
        if sharpness < th["sharpness_good"]:
            reasons.append("Image sharpness is fair")
        if motion_blur > th["motion_blur_max_fair"]:
            reasons.append("Motion blur detected")
        if brightness_mean < th["brightness_min_fair"]:
            reasons.append("Image is somewhat dark")
        if brightness_mean > th["brightness_max_fair"] and (contrast_spread < th["contrast_spread_min_good"] and contrast_rms < th["contrast_rms_min_good"]):
            reasons.append("Image is somewhat bright")
        if contrast_rms < th["contrast_rms_min_fair"]:
            reasons.append("Contrast is low")
        if text_height < th["min_text_height_fair"]:
            reasons.append("Text is relatively small")
        if abs(skew) > th["skew_max_fair"]:
            reasons.append("Extreme skew angle detected")
        if shadow_sev > th["shadow_max_fair"]:
            reasons.append("Uneven shadows across page")
        if coverage < th["page_coverage_min_fair"]:
            reasons.append("Document occupies too little of frame")

    # Tier mapping
    if hard_failure:
        tier = "poor"
    elif len(reasons) > 0:
        tier = "fair"
    else:
        tier = "good"

    return QualityReport(
        image_quality=tier,
        reasons=reasons,
        sharpness=sharpness,
        motion_blur=motion_blur,
        brightness_mean=brightness_mean,
        brightness_clipping_percent=clipping,
        contrast_rms=contrast_rms,
        contrast_percentile_spread=contrast_spread,
        resolution_megapixels=resolution_mp,
        estimated_text_height_px=text_height,
        glare_percent=glare_pct,
        skew_angle=skew,
        perspective_distortion=perspective_dist,
        shadow_severity=shadow_sev,
        page_coverage=coverage,
        hard_failure=hard_failure,
    )


def assess_image_quality(gray: np.ndarray) -> str:
    """
    Heuristic quality assessment based on image sharpness (Laplacian variance).
    Preserved for backwards compatibility with legacy callers and existing tests.
    """
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    logger.debug("Image quality assessment: Laplacian variance = %.1f", lap_var)
    if lap_var >= 150.0:
        return "good"
    elif lap_var >= 50.0:
        return "fair"
    else:
        return "poor"


# ---------------------------------------------------------------------------
# Pipeline Stages: Grayscale, Deskew, Contrast
# ---------------------------------------------------------------------------

class PipelineStage(Protocol):
    """Protocol for a pure preprocessing pipeline stage."""
    name: str

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        ...


class OrientationStage:
    name: str = "orientation"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_ORIENTATION_ENABLED)
        exif_bytes = cfg.get("exif_bytes")
        before_metric = float(img.shape[0] / max(1, img.shape[1]))

        if not enabled:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": False},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=before_metric,
                metric_after=before_metric,
            )

        # 1. EXIF orientation check
        if exif_bytes:
            try:
                pil_img = Image.open(io.BytesIO(exif_bytes))
                exif = pil_img.getexif()
                orientation_tag = exif.get(0x0112)
                if orientation_tag in (3, 6, 8):
                    transposed = ImageOps.exif_transpose(pil_img)
                    out = cv2.cvtColor(np.array(transposed), cv2.COLOR_RGB2BGR) if transposed.mode == "RGB" else np.array(transposed)
                    dt = (time.perf_counter() - t0) * 1000.0
                    return out, StageReport(
                        name=self.name,
                        applied=True,
                        parameters={"exif_corrected": True, "tag": orientation_tag},
                        duration_ms=dt,
                        metric_before=before_metric,
                        metric_after=float(out.shape[0] / max(1, out.shape[1])),
                    )
            except Exception as e:
                logger.debug("EXIF transpose skipped: %s", e)

        # 2. Text-orientation heuristic using projection profiles
        gray = _to_grayscale(img)
        h, w = gray.shape

        scale = 1.0
        if max(h, w) > 800:
            scale = 800.0 / max(h, w)
            work_gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        else:
            work_gray = gray

        binary = cv2.adaptiveThreshold(
            work_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 8
        )

        h_proj = np.sum(binary, axis=1, dtype=np.float32)
        v_proj = np.sum(binary, axis=0, dtype=np.float32)

        var_h = float(np.var(h_proj))
        var_v = float(np.var(v_proj))

        fixed_angle = 0
        if var_v > 1.8 * (var_h + 1e-5):
            fixed_angle = 90
            out = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        elif h < w and var_v > 1.15 * (var_h + 1e-5):
            fixed_angle = 90
            out = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        else:
            out = img

        dt = (time.perf_counter() - t0) * 1000.0
        applied = fixed_angle != 0
        after_metric = float(out.shape[0] / max(1, out.shape[1]))

        return out, StageReport(
            name=self.name,
            applied=applied,
            parameters={"angle_fixed": fixed_angle, "var_h": round(var_h, 1), "var_v": round(var_v, 1)},
            duration_ms=dt,
            metric_before=before_metric,
            metric_after=after_metric,
        )


class DocumentPerspectiveStage:
    name: str = "perspective"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_PERSPECTIVE_ENABLED)
        max_stretch = float(cfg.get("max_stretch", MAX_PERSPECTIVE_STRETCH))
        min_doc_area_ratio = float(cfg.get("min_area_ratio", 0.15))

        h, w = img.shape[:2]

        if not enabled:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": False},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=0.0,
                metric_after=0.0,
            )

        gray = _to_grayscale(img)

        scale = 1.0
        if max(h, w) > 900:
            scale = 900.0 / max(h, w)
            work_gray = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        else:
            work_gray = gray

        blurred = cv2.GaussianBlur(work_gray, (7, 7), 0)
        edges = cv2.Canny(blurred, 30, 120)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = sorted(contours, key=cv2.contourArea, reverse=True)

        best_quad = None
        for c in contours:
            area = cv2.contourArea(c)
            if area < (work_gray.shape[0] * work_gray.shape[1] * min_doc_area_ratio):
                break
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.025 * peri, True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                best_quad = approx
                break

        if best_quad is None:
            dt = (time.perf_counter() - t0) * 1000.0
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"reason": "no_quad_detected"},
                duration_ms=dt,
                metric_before=0.0,
                metric_after=0.0,
                warnings=["No distinct document quadrilateral detected; skipped perspective warp."],
            )

        pts = best_quad.reshape(4, 2).astype(np.float32) / scale

        # Order corners: TL, TR, BR, BL
        s = pts.sum(axis=1)
        diff = np.diff(pts, axis=1)
        tl = pts[np.argmin(s)]
        br = pts[np.argmax(s)]
        tr = pts[np.argmin(diff)]
        bl = pts[np.argmax(diff)]

        top_len = float(np.linalg.norm(tr - tl))
        bottom_len = float(np.linalg.norm(br - bl))
        left_len = float(np.linalg.norm(bl - tl))
        right_len = float(np.linalg.norm(br - tr))

        h_ratio = max(top_len, bottom_len) / (min(top_len, bottom_len) + 1e-5)
        v_ratio = max(left_len, right_len) / (min(left_len, right_len) + 1e-5)
        stretch_factor = max(h_ratio, v_ratio)

        if stretch_factor > max_stretch:
            dt = (time.perf_counter() - t0) * 1000.0
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"stretch_factor": round(stretch_factor, 2), "max_allowed": max_stretch},
                duration_ms=dt,
                metric_before=stretch_factor,
                metric_after=stretch_factor,
                warnings=[f"Perspective warp rejected: stretch factor {stretch_factor:.2f} exceeds sanity limit."],
            )

        dst_w = max(50, int(round(max(top_len, bottom_len))))
        dst_h = max(50, int(round(max(left_len, right_len))))

        src_pts = np.float32([tl, tr, br, bl])
        dst_pts = np.float32([
            [0, 0],
            [dst_w - 1, 0],
            [dst_w - 1, dst_h - 1],
            [0, dst_h - 1],
        ])

        M = cv2.getPerspectiveTransform(src_pts, dst_pts)
        warped = cv2.warpPerspective(img, M, (dst_w, dst_h), flags=cv2.INTER_CUBIC)

        dt = (time.perf_counter() - t0) * 1000.0
        return warped, StageReport(
            name=self.name,
            applied=True,
            parameters={
                "corners": [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in [tl, tr, br, bl]],
                "dst_shape": [dst_h, dst_w],
                "stretch_factor": round(stretch_factor, 2),
            },
            duration_ms=dt,
            metric_before=stretch_factor,
            metric_after=0.0,
        )


class GrayscaleStage:
    name: str = "grayscale"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_GRAYSCALE_ENABLED)
        before_channels = float(img.shape[2] if img.ndim == 3 else 1)

        if not enabled:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": False},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=before_channels,
                metric_after=before_channels,
            )

        if img.ndim == 2:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": True, "already_gray": True},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=1.0,
                metric_after=1.0,
            )

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        dt = (time.perf_counter() - t0) * 1000.0
        return gray, StageReport(
            name=self.name,
            applied=True,
            parameters={"enabled": True},
            duration_ms=dt,
            metric_before=before_channels,
            metric_after=1.0,
        )


class DeskewStage:
    name: str = "deskew"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_DESKEW_ENABLED)
        min_angle = float(cfg.get("min_angle", DESKEW_MIN_ANGLE))
        max_limit = float(cfg.get("max_limit", DESKEW_LIMIT))

        max_detect = float(cfg.get("max_detect_angle", 15.0))
        gray = _to_grayscale(img)
        if "forced_angle" in cfg:
            angle_before = float(cfg["forced_angle"])
        else:
            angle_before = _estimate_skew_angle(gray, max_detect_angle=max_detect)

        if not enabled:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": False},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=angle_before,
                metric_after=angle_before,
            )

        if abs(angle_before) < min_angle:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"min_angle": min_angle, "max_limit": max_limit},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=angle_before,
                metric_after=angle_before,
            )

        # Cap angle
        clamped_angle = max(-max_limit, min(max_limit, angle_before))
        rotated = _rotate_image(gray, clamped_angle)
        angle_after = _estimate_skew_angle(rotated, max_detect_angle=max_detect)
        dt = (time.perf_counter() - t0) * 1000.0

        return rotated, StageReport(
            name=self.name,
            applied=True,
            parameters={"angle": clamped_angle, "clamped": clamped_angle != angle_before},
            duration_ms=dt,
            metric_before=angle_before,
            metric_after=angle_after,
        )


class IlluminationStage:
    name: str = "illumination"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_ILLUMINATION_ENABLED)
        gray = _to_grayscale(img)
        shadow_before = _compute_shadow_severity(gray)

        if not enabled:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": False},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=shadow_before,
                metric_after=shadow_before,
            )

        # Morphological background estimation with large kernel
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
        bg = cv2.morphologyEx(gray, cv2.MORPH_DILATE, kernel)
        bg = cv2.medianBlur(bg, 21)

        # Division normalization
        normalized = np.clip((gray.astype(np.float32) / (bg.astype(np.float32) + 1e-5)) * 255.0, 0, 255).astype(np.uint8)
        shadow_after = _compute_shadow_severity(normalized)
        dt = (time.perf_counter() - t0) * 1000.0

        out = cv2.cvtColor(normalized, cv2.COLOR_GRAY2BGR) if img.ndim == 3 else normalized

        return out, StageReport(
            name=self.name,
            applied=True,
            parameters={"method": "morphological_division", "kernel_size": 31},
            duration_ms=dt,
            metric_before=shadow_before,
            metric_after=shadow_after,
        )


class DenoiseStage:
    name: str = "denoise"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_DENOISE_ENABLED)
        gray = _to_grayscale(img)

        # Estimate high-frequency noise level
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        noise_level = float(np.std(gray.astype(np.float32) - blurred.astype(np.float32)))

        if not enabled or noise_level < 2.0:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": enabled, "noise_level": round(noise_level, 2)},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=noise_level,
                metric_after=noise_level,
            )

        # Scale strength with measured noise level (edge-preserving)
        h_strength = float(cfg.get("strength", min(12.0, max(4.0, noise_level * 0.7))))
        denoised = cv2.fastNlMeansDenoising(gray, None, h=h_strength, templateWindowSize=7, searchWindowSize=21)

        denoised_blurred = cv2.GaussianBlur(denoised, (3, 3), 0)
        noise_after = float(np.std(denoised.astype(np.float32) - denoised_blurred.astype(np.float32)))
        dt = (time.perf_counter() - t0) * 1000.0

        out = cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR) if img.ndim == 3 else denoised

        return out, StageReport(
            name=self.name,
            applied=True,
            parameters={"strength": round(h_strength, 2), "edge_preserving": True},
            duration_ms=dt,
            metric_before=noise_level,
            metric_after=noise_after,
        )


class ContrastStage:
    name: str = "contrast"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_CONTRAST_ENABLED)
        adaptive = cfg.get("adaptive", True)
        tile_grid = tuple(cfg.get("tile_grid", CLAHE_TILE_GRID))

        gray = _to_grayscale(img)
        contrast_before, _ = _compute_contrast(gray)

        if not enabled:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": False},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=contrast_before,
                metric_after=contrast_before,
            )

        if adaptive:
            if contrast_before < 25.0:
                clip_limit = 3.0
            elif contrast_before < 35.0:
                clip_limit = 2.5
            else:
                clip_limit = 1.8
        else:
            clip_limit = float(cfg.get("clip_limit", CLAHE_CLIP_LIMIT))

        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid)
        enhanced = clahe.apply(gray)
        contrast_after, _ = _compute_contrast(enhanced)
        dt = (time.perf_counter() - t0) * 1000.0

        out = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR) if img.ndim == 3 else enhanced

        return out, StageReport(
            name=self.name,
            applied=True,
            parameters={
                "clip_limit": clip_limit,
                "adaptive_clip_limit": clip_limit,
                "tile_grid": list(tile_grid),
                "adaptive": adaptive,
            },
            duration_ms=dt,
            metric_before=contrast_before,
            metric_after=contrast_after,
        )


class ContentCropStage:
    name: str = "crop"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_CROP_ENABLED)
        padding = int(cfg.get("padding", MARGIN_PADDING_PX))

        h, w = img.shape[:2]
        before_area = float(h * w)

        if not enabled:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": False},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=before_area,
                metric_after=before_area,
            )

        gray = _to_grayscale(img)
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 8
        )

        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)

        min_x, min_y = w, h
        max_x, max_y = 0, 0
        valid_components = 0

        for i in range(1, num_labels):
            cw = stats[i, cv2.CC_STAT_WIDTH]
            ch = stats[i, cv2.CC_STAT_HEIGHT]
            carea = stats[i, cv2.CC_STAT_AREA]
            cx = stats[i, cv2.CC_STAT_LEFT]
            cy = stats[i, cv2.CC_STAT_TOP]

            if cw > w * 0.95 or ch > h * 0.95:
                continue
            if carea < 6 or cw < 2 or ch < 3:
                continue

            valid_components += 1
            min_x = min(min_x, cx)
            min_y = min(min_y, cy)
            max_x = max(max_x, cx + cw)
            max_y = max(max_y, cy + ch)

        if valid_components < 3 or min_x >= max_x or min_y >= max_y:
            dt = (time.perf_counter() - t0) * 1000.0
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"reason": "no_valid_text_found"},
                duration_ms=dt,
                metric_before=before_area,
                metric_after=before_area,
            )

        crop_x1 = max(0, min_x - padding)
        crop_y1 = max(0, min_y - padding)
        crop_x2 = min(w, max_x + padding)
        crop_y2 = min(h, max_y + padding)

        cropped_inside_text = bool((crop_x1 > min_x) or (crop_y1 > min_y) or (crop_x2 < max_x) or (crop_y2 < max_y))

        crop_w = crop_x2 - crop_x1
        crop_h = crop_y2 - crop_y1
        after_area = float(crop_w * crop_h)

        if after_area >= 0.96 * before_area:
            dt = (time.perf_counter() - t0) * 1000.0
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"reason": "margins_already_minimal", "cropped_inside_text": False},
                duration_ms=dt,
                metric_before=before_area,
                metric_after=before_area,
            )

        cropped = img[crop_y1:crop_y2, crop_x1:crop_x2]
        dt = (time.perf_counter() - t0) * 1000.0
        return cropped, StageReport(
            name=self.name,
            applied=True,
            parameters={
                "padding": padding,
                "crop_box": [int(crop_x1), int(crop_y1), int(crop_x2), int(crop_y2)],
                "cropped_inside_text": cropped_inside_text,
            },
            duration_ms=dt,
            metric_before=before_area,
            metric_after=after_area,
        )


class SharpenStage:
    name: str = "sharpen"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_SHARPEN_ENABLED)

        gray = _to_grayscale(img)
        sharpness_before = _compute_sharpness(gray)

        if not enabled:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": False},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=sharpness_before,
                metric_after=sharpness_before,
            )

        # Mild unsharp mask for moderate blur only (Laplacian variance 50-200)
        # Never sharpen severe blur (< 50) to avoid hallucinatory edge artifacts
        if sharpness_before < 50.0:
            dt = (time.perf_counter() - t0) * 1000.0
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"sharpness": round(sharpness_before, 2), "skipped_reason": "severe_blur"},
                warnings=["Severe blur detected (sharpness < 50.0); sharpening skipped to avoid artifacts."],
                duration_ms=dt,
                metric_before=sharpness_before,
                metric_after=sharpness_before,
            )

        if sharpness_before > 250.0:
            dt = (time.perf_counter() - t0) * 1000.0
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"sharpness": round(sharpness_before, 2), "skipped_reason": "already_sharp"},
                duration_ms=dt,
                metric_before=sharpness_before,
                metric_after=sharpness_before,
            )

        # Apply mild unsharp mask
        strength = float(cfg.get("strength", 0.5))
        blurred = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.0)
        sharpened_gray = cv2.addWeighted(gray, 1.0 + strength, blurred, -strength, 0)
        sharpness_after = _compute_sharpness(sharpened_gray)
        dt = (time.perf_counter() - t0) * 1000.0

        if img.ndim == 3:
            blurred_color = cv2.GaussianBlur(img, (0, 0), sigmaX=1.0)
            out = cv2.addWeighted(img, 1.0 + strength, blurred_color, -strength, 0)
        else:
            out = sharpened_gray

        return out, StageReport(
            name=self.name,
            applied=True,
            parameters={"strength": strength, "filter": "unsharp_mask"},
            duration_ms=dt,
            metric_before=sharpness_before,
            metric_after=sharpness_after,
        )


class ResolutionStage:
    name: str = "resolution"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_RESOLUTION_ENABLED)
        target_text_height = float(cfg.get("target_text_height", TARGET_TEXT_HEIGHT_PX))
        min_text_height = float(cfg.get("min_text_height", 4.0))
        max_pixels = int(cfg.get("max_pixels", MAX_PIXEL_COUNT))
        force_factor = cfg.get("force_upscale_factor")

        h, w = img.shape[:2]
        gray = _to_grayscale(img)
        _, text_height = _compute_resolution_and_text_height(gray)

        if not enabled:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": False},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=text_height,
                metric_after=text_height,
            )

        factor: float = 1.0
        if force_factor is not None:
            factor = float(force_factor)
        elif text_height > 0.0 and min_text_height <= text_height < target_text_height:
            factor = min(2.5, target_text_height / text_height)

        if factor <= 1.05:
            dt = (time.perf_counter() - t0) * 1000.0
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"factor": 1.0, "reason": "already_sufficient_resolution"},
                duration_ms=dt,
                metric_before=text_height,
                metric_after=text_height,
            )

        new_w = int(round(w * factor))
        new_h = int(round(h * factor))

        if (new_w * new_h) > max_pixels:
            dt = (time.perf_counter() - t0) * 1000.0
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"factor": factor, "projected_pixels": new_w * new_h, "max_pixels": max_pixels},
                warnings=[f"Upscaling would exceed max pixel count ({max_pixels}); skipped."],
                duration_ms=dt,
                metric_before=text_height,
                metric_after=text_height,
            )

        upscaled = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        dt = (time.perf_counter() - t0) * 1000.0

        return upscaled, StageReport(
            name=self.name,
            applied=True,
            parameters={
                "factor": round(factor, 3),
                "interpolation": "cv2.INTER_LANCZOS4",
                "new_shape": [new_h, new_w],
            },
            duration_ms=dt,
            metric_before=text_height,
            metric_after=text_height * factor,
        )


class BinarizationStage:
    name: str = "binarization"

    def process(
        self, img: np.ndarray, config: dict[str, Any] | None = None
    ) -> tuple[np.ndarray, StageReport]:
        t0 = time.perf_counter()
        cfg = config or {}
        enabled = cfg.get("enabled", STAGE_BINARIZATION_ENABLED)
        method = str(cfg.get("method", "sauvola")).lower()

        if not enabled:
            return img, StageReport(
                name=self.name,
                applied=False,
                parameters={"enabled": False},
                duration_ms=(time.perf_counter() - t0) * 1000.0,
                metric_before=0.0,
                metric_after=0.0,
            )

        gray = _to_grayscale(img)
        if method == "otsu":
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        else:
            binary = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 10
            )

        out = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR) if img.ndim == 3 else binary
        dt = (time.perf_counter() - t0) * 1000.0

        return out, StageReport(
            name=self.name,
            applied=True,
            parameters={"method": method, "experimental": True},
            duration_ms=dt,
            metric_before=0.0,
            metric_after=1.0,
        )


_STAGE_REGISTRY: dict[str, type] = {
    "orientation": OrientationStage,
    "perspective": DocumentPerspectiveStage,
    "crop": ContentCropStage,
    "illumination": IlluminationStage,
    "denoise": DenoiseStage,
    "grayscale": GrayscaleStage,
    "contrast": ContrastStage,
    "deskew": DeskewStage,
    "sharpen": SharpenStage,
    "resolution": ResolutionStage,
    "binarization": BinarizationStage,
}



def _rotate_image(gray: np.ndarray, angle: float) -> np.ndarray:
    """Rotate a grayscale image by angle degrees about its centre."""
    h, w = gray.shape
    cx, cy = w // 2, h // 2
    M = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
    rotated = cv2.warpAffine(
        gray, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated


def deskew(gray: np.ndarray) -> np.ndarray:
    """Detect and correct skew in a grayscale prescription image."""
    stage = DeskewStage()
    out, _ = stage.process(gray)
    return out


def contrast_normalize(gray: np.ndarray) -> np.ndarray:
    """Apply CLAHE contrast normalisation."""
    stage = ContrastStage()
    out, _ = stage.process(gray)
    return out


# ---------------------------------------------------------------------------
# Pipeline Execution
# ---------------------------------------------------------------------------

def run_pipeline(
    img: np.ndarray,
    stages: Sequence[str] | None = None,
    stage_configs: dict[str, dict[str, Any]] | None = None,
) -> tuple[np.ndarray, PipelineManifest]:
    """
    Execute ordered pure stages on an image array.

    Returns:
        (final_image, PipelineManifest)
    """
    t_start = time.perf_counter()
    active_stages = list(stages if stages is not None else PIPELINE_STAGES)
    configs = stage_configs or {}

    current_img = img
    reports: list[StageReport] = []
    warnings: list[str] = []

    for stage_name in active_stages:
        stage_cls = _STAGE_REGISTRY.get(stage_name)
        if stage_cls is None:
            warnings.append(f"Unknown stage '{stage_name}' skipped.")
            continue
        stage_instance = stage_cls()
        cfg = configs.get(stage_name, {})
        current_img, report = stage_instance.process(current_img, config=cfg)
        reports.append(report)
        warnings.extend(report.warnings)

    total_dt = (time.perf_counter() - t_start) * 1000.0

    manifest = PipelineManifest(
        stages=reports,
        metrics={
            "initial_shape": list(img.shape),
            "final_shape": list(current_img.shape),
        },
        warnings=warnings,
        total_duration_ms=total_dt,
    )
    return current_img, manifest


# ---------------------------------------------------------------------------
# Public Entrypoints with Safety Guarantees
# ---------------------------------------------------------------------------

def preprocess_with_manifest(
    data: bytes,
    pdf_page: int = 0,
    stage_configs: dict[str, dict[str, Any]] | None = None,
) -> tuple[bytes, PipelineManifest, QualityReport]:
    """
    Execute full preprocessing pipeline with complete manifest and quality report.

    Enforces Safety Rule:
      - Source image quality is computed FIRST.
      - Enhancement can NEVER upgrade a poor source image to good or fair.
      - If the source image fails a hard check, the final quality remains 'poor'.

    Returns:
        (png_bytes, PipelineManifest, QualityReport)
    """
    if _is_pdf(data):
        logger.info("Preprocessing: detected PDF input (page %d).", pdf_page)
        data = _pdf_to_image_bytes(data, page_index=pdf_page)

    source_img = _bytes_to_array(data)

    # 1. Quality report on the source image
    source_quality = assess_quality(source_img)

    # 2. Run pipeline stages
    processed_img, manifest = run_pipeline(source_img, stage_configs=stage_configs)

    # 3. Assess processed image
    processed_quality = assess_quality(processed_img)

    # 4. Enforce Safety Invariants:
    # Enhancement can NEVER upgrade a 'poor' source image to good/fair.
    if source_quality.image_quality == "poor" or source_quality.hard_failure:
        final_quality = source_quality
    elif processed_quality.image_quality == "poor":
        final_quality = processed_quality
    elif source_quality.image_quality == "fair" or processed_quality.image_quality == "fair":
        combined_reasons = list(dict.fromkeys(source_quality.reasons + processed_quality.reasons))
        final_quality = QualityReport(
            image_quality="fair",
            reasons=combined_reasons,
            sharpness=source_quality.sharpness,
            motion_blur=source_quality.motion_blur,
            brightness_mean=source_quality.brightness_mean,
            brightness_clipping_percent=source_quality.brightness_clipping_percent,
            contrast_rms=source_quality.contrast_rms,
            contrast_percentile_spread=source_quality.contrast_percentile_spread,
            resolution_megapixels=source_quality.resolution_megapixels,
            estimated_text_height_px=source_quality.estimated_text_height_px,
            glare_percent=source_quality.glare_percent,
            skew_angle=source_quality.skew_angle,
            perspective_distortion=source_quality.perspective_distortion,
            shadow_severity=source_quality.shadow_severity,
            page_coverage=source_quality.page_coverage,
            hard_failure=False,
        )
    else:
        final_quality = source_quality

    manifest.metrics["source_quality"] = source_quality.image_quality
    manifest.metrics["final_quality"] = final_quality.image_quality
    manifest.warnings.extend(final_quality.reasons)

    png_bytes = _array_to_png_bytes(processed_img)
    return png_bytes, manifest, final_quality


def preprocess(data: bytes, pdf_page: int = 0) -> bytes:
    """
    Standard preprocessing pipeline. Backwards-compatible entrypoint.
    Returns PNG-encoded bytes.
    """
    png_bytes, _, _ = preprocess_with_manifest(data, pdf_page=pdf_page)
    return png_bytes


def preprocess_and_assess(data: bytes, pdf_page: int = 0) -> tuple[bytes, str]:
    """
    Preprocess image and return (png_bytes, quality_str).
    Backwards-compatible wrapper.
    """
    png_bytes, _, quality_report = preprocess_with_manifest(data, pdf_page=pdf_page)
    return png_bytes, quality_report.image_quality


def process_pdf_pages(
    data: bytes,
    max_pages: int = PDF_MAX_PAGES,
    dpi: int = PDF_DPI,
) -> list[tuple[bytes, PipelineManifest, QualityReport]]:
    """
    Rasterize and process multi-page PDFs using pypdfium2 (permissive) or fitz fallback.
    Caps pages at max_pages (default 10) and returns per-page (png_bytes, manifest, quality_report).
    """
    if _PYPDFIUM_AVAILABLE and pdfium is not None:
        doc = pdfium.PdfDocument(data)
        num_pages = min(len(doc), max_pages)
        results: list[tuple[bytes, PipelineManifest, QualityReport]] = []
        try:
            for idx in range(num_pages):
                page = doc[idx]
                pil_image = page.render(scale=dpi / 72.0).to_pil()
                buf = io.BytesIO()
                pil_image.save(buf, format="PNG")
                page_png = buf.getvalue()
                png_bytes, manifest, quality = preprocess_with_manifest(page_png)
                results.append((png_bytes, manifest, quality))
        finally:
            doc.close()
        return results
    elif _PYMUPDF_AVAILABLE and fitz is not None:
        doc = fitz.open(stream=data, filetype="pdf")
        num_pages = min(len(doc), max_pages)
        results = []
        try:
            for idx in range(num_pages):
                page = doc[idx]
                mat = fitz.Matrix(dpi / 72, dpi / 72)
                pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
                page_png = pix.tobytes("png")
                png_bytes, manifest, quality = preprocess_with_manifest(page_png)
                results.append((png_bytes, manifest, quality))
        finally:
            doc.close()
        return results
    else:
        raise RuntimeError(
            "pypdfium2 or PyMuPDF is required for PDF processing. "
            "Install pypdfium2 with: pip install pypdfium2"
        )


# ---------------------------------------------------------------------------
# Process-Level Timeout Runner
# ---------------------------------------------------------------------------

def preprocess_with_timeout(
    data: bytes,
    pdf_page: int = 0,
    stage_configs: dict[str, dict[str, Any]] | None = None,
    timeout_seconds: float = PREPROCESSING_TIMEOUT_SECONDS,
) -> tuple[bytes, PipelineManifest, QualityReport]:
    """
    Execute full preprocessing pipeline with strict process-level timeout enforcement.

    Unlike thread-based timeouts (which cannot terminate hung C-extensions or native OpenCV loops),
    this implementation runs pipeline execution in a ProcessPoolExecutor. If the stage hangs or
    exceeds timeout_seconds:
      1. The subprocess is forcibly terminated (killing the hung stage and releasing CPU).
      2. Safe fallback quality assessment runs on the source input image.
      3. Image quality tier is capped at 'fair' (or 'poor' if source failed checks; never 'good').
      4. Manifest records timed_out=True and logs the timeout in manifest warnings.
    """
    import concurrent.futures
    import multiprocessing as mp

    if timeout_seconds <= 0:
        return preprocess_with_manifest(data, pdf_page=pdf_page, stage_configs=stage_configs)

    try:
        mp_ctx = mp.get_context("spawn")
        executor = concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=mp_ctx)
    except Exception:
        executor = concurrent.futures.ProcessPoolExecutor(max_workers=1)

    try:
        future = executor.submit(
            preprocess_with_manifest,
            data,
            pdf_page=pdf_page,
            stage_configs=stage_configs,
        )
        return future.result(timeout=timeout_seconds)
    except (concurrent.futures.TimeoutError, TimeoutError):
        # Hung stage: forcibly terminate worker processes to release CPU immediately
        for proc in list(getattr(executor, "_processes", {}).values()):
            try:
                proc.kill()
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            try:
                proc.join(timeout=0.2)
            except Exception:
                pass
        executor.shutdown(wait=False, cancel_futures=True)

        # Fallback quality assessment on source input image
        try:
            if _is_pdf(data):
                source_png = _pdf_to_image_bytes(data, page_index=pdf_page)
            else:
                source_png = data
            source_img = _bytes_to_array(source_png)
            fallback_quality = assess_quality(source_img)
        except Exception:
            source_png = data
            fallback_quality = QualityReport(
                image_quality="fair",
                reasons=["Preprocessing timeout; source fallback assessment failed"],
                sharpness=0.0,
                motion_blur=0.0,
                brightness_mean=128.0,
                brightness_clipping_percent=0.0,
                contrast_rms=20.0,
                contrast_percentile_spread=40.0,
                resolution_megapixels=1.0,
                estimated_text_height_px=14.0,
                glare_percent=0.0,
                skew_angle=0.0,
                perspective_distortion=0.0,
                shadow_severity=0.0,
            )

        # Cap quality at "fair": if source was "good", cap to "fair"
        capped_tier = "fair" if fallback_quality.image_quality == "good" else fallback_quality.image_quality
        fallback_quality.image_quality = capped_tier
        fallback_quality.reasons.append(f"Preprocessing timed out after {timeout_seconds:.1f}s; quality capped at fair.")

        manifest = PipelineManifest(
            stages=[],
            metrics={"timed_out": True, "timeout_seconds": timeout_seconds},
            warnings=[f"Pipeline terminated due to timeout ({timeout_seconds:.1f}s budget exceeded). CPU released."],
            total_duration_ms=timeout_seconds * 1000.0,
            timed_out=True,
        )
        return source_png, manifest, fallback_quality
    finally:
        try:
            executor.shutdown(wait=False)
        except Exception:
            pass
 

