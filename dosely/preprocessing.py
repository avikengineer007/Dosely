"""
Prescription Image Preprocessing
==================================
OpenCV pipeline: PDF/image -> deskewed, grayscale, contrast-normalised bytes.

Pipeline order:
  1. PDF -> first-page raster (PyMuPDF) if needed
  2. Decode to numpy array
  3. Grayscale conversion
  4. Deskew via Hough-line angle estimation
  5. CLAHE contrast normalisation
  6. Re-encode to PNG bytes

The output is always PNG bytes ready for base64 encoding.
"""

from __future__ import annotations

import io
import logging
import math
from typing import Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# PDF support is optional at import time so the module still loads
# even if PyMuPDF is not installed (image-only mode).
try:
    import fitz  # PyMuPDF
    _PYMUPDF_AVAILABLE = True
except ImportError:
    _PYMUPDF_AVAILABLE = False
    logger.warning(
        "PyMuPDF not installed. PDF input is disabled. "
        "Install with: pip install pymupdf"
    )


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

PDF_DPI: int = 200          # DPI for PDF rasterisation
CLAHE_CLIP_LIMIT: float = 2.0
CLAHE_TILE_GRID: Tuple[int, int] = (8, 8)
DESKEW_DELTA: float = 1.0   # degrees step for brute-force fine search
DESKEW_LIMIT: float = 10.0  # maximum tilt angle to correct (degrees)


# ---------------------------------------------------------------------------
# Step 1: Input normalisation
# ---------------------------------------------------------------------------

def _is_pdf(data: bytes) -> bool:
    """Check for PDF magic bytes."""
    return data[:4] == b"%PDF"


def _pdf_to_image_bytes(data: bytes, page_index: int = 0) -> bytes:
    """Rasterise one PDF page to PNG bytes."""
    if not _PYMUPDF_AVAILABLE:
        raise RuntimeError(
            "PyMuPDF is required for PDF processing. "
            "Install it with: pip install pymupdf"
        )
    doc = fitz.open(stream=data, filetype="pdf")
    if page_index >= len(doc):
        raise ValueError(
            f"PDF has {len(doc)} page(s); requested page {page_index}."
        )
    page = doc[page_index]
    mat = fitz.Matrix(PDF_DPI / 72, PDF_DPI / 72)  # 72 DPI is PDF default
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    png_bytes: bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


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


# ---------------------------------------------------------------------------
# Step 2: Grayscale
# ---------------------------------------------------------------------------

def _to_grayscale(img: np.ndarray) -> np.ndarray:
    """Convert BGR or already-grayscale array to single-channel uint8."""
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------------------
# Step 3: Deskew
# ---------------------------------------------------------------------------

def _estimate_skew_angle(gray: np.ndarray) -> float:
    """
    Estimate the dominant text skew angle using Hough line transform.

    Returns angle in degrees in [-DESKEW_LIMIT, +DESKEW_LIMIT].
    Returns 0.0 if no clear angle can be determined.
    """
    # Adaptive threshold -> binary
    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=15,
        C=4,
    )

    # Hough probabilistic line transform
    lines = cv2.HoughLinesP(
        binary,
        rho=1,
        theta=math.pi / 180,
        threshold=100,
        minLineLength=gray.shape[1] // 6,
        maxLineGap=20,
    )

    if lines is None or len(lines) == 0:
        logger.debug("Deskew: no Hough lines found; skipping.")
        return 0.0

    # OpenCV 4.x returns shape (N, 1, 4); OpenCV 5.x returns (N, 4).
    # Normalise to (N, 4) so unpacking is version-agnostic.
    lines_flat = lines.reshape(-1, 4)
    angles: list[float] = []
    for x1, y1, x2, y2 in lines_flat:
        dx, dy = int(x2) - int(x1), int(y2) - int(y1)
        if dx == 0:
            continue
        angle = math.degrees(math.atan2(dy, dx))
        # Keep only near-horizontal lines (text baseline lines)
        if abs(angle) < DESKEW_LIMIT:
            angles.append(angle)

    if not angles:
        logger.debug("Deskew: no near-horizontal lines; skipping.")
        return 0.0

    median_angle = float(np.median(angles))
    logger.debug("Deskew: estimated angle = %.2f°", median_angle)
    return median_angle


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
    """
    Detect and correct skew in a grayscale prescription image.

    Angles within 0.5° of zero are ignored (no visible tilt).
    """
    angle = _estimate_skew_angle(gray)
    if abs(angle) < 0.5:
        return gray
    logger.info("Deskew: correcting %.2f° of skew.", angle)
    return _rotate_image(gray, angle)


# ---------------------------------------------------------------------------
# Step 4: Contrast normalisation
# ---------------------------------------------------------------------------

def contrast_normalize(gray: np.ndarray) -> np.ndarray:
    """
    Apply CLAHE (Contrast Limited Adaptive Histogram Equalisation).

    CLAHE works per tile so it enhances local contrast without
    over-amplifying noise in already-bright regions.
    """
    clahe = cv2.createCLAHE(
        clipLimit=CLAHE_CLIP_LIMIT,
        tileGridSize=CLAHE_TILE_GRID,
    )
    return clahe.apply(gray)


# ---------------------------------------------------------------------------
# Step 5: Re-encode
# ---------------------------------------------------------------------------

def _array_to_png_bytes(gray: np.ndarray) -> bytes:
    success, buf = cv2.imencode(".png", gray)
    if not success:
        raise RuntimeError("cv2.imencode failed during PNG encoding.")
    return buf.tobytes()


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def preprocess(
    data: bytes,
    pdf_page: int = 0,
) -> bytes:
    """
    Full preprocessing pipeline.

    Parameters
    ----------
    data:
        Raw bytes of a prescription image (JPEG, PNG, TIFF, BMP) or PDF.
    pdf_page:
        Zero-indexed page to extract when ``data`` is a PDF.

    Returns
    -------
    bytes
        PNG-encoded, deskewed, grayscale, CLAHE-normalised image bytes.
    """
    # 1. Normalise input
    if _is_pdf(data):
        logger.info("Preprocessing: detected PDF input (page %d).", pdf_page)
        data = _pdf_to_image_bytes(data, page_index=pdf_page)

    img = _bytes_to_array(data)

    # 2. Grayscale
    gray = _to_grayscale(img)

    # 3. Deskew
    gray = deskew(gray)

    # 4. CLAHE contrast normalisation
    gray = contrast_normalize(gray)

    # 5. Re-encode to PNG
    return _array_to_png_bytes(gray)


def assess_image_quality(gray: np.ndarray) -> str:
    """
    Heuristic quality assessment based on image sharpness (Laplacian variance).

    Returns one of 'good', 'fair', 'poor'.
    The thresholds are tuned for 200-300 DPI prescription scans.
    """
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    logger.debug("Image quality assessment: Laplacian variance = %.1f", lap_var)

    if lap_var >= 150.0:
        return "good"
    elif lap_var >= 50.0:
        return "fair"
    else:
        return "poor"


def preprocess_and_assess(data: bytes, pdf_page: int = 0) -> tuple[bytes, str]:
    """
    Convenience wrapper that runs preprocessing and quality assessment together.

    Returns
    -------
    (png_bytes, quality_str):
        Preprocessed PNG bytes and 'good'|'fair'|'poor' quality label.
    """
    if _is_pdf(data):
        data = _pdf_to_image_bytes(data, page_index=pdf_page)

    img = _bytes_to_array(data)
    gray = _to_grayscale(img)
    gray = deskew(gray)
    quality = assess_image_quality(gray)   # assess before CLAHE for true reading
    gray = contrast_normalize(gray)
    return _array_to_png_bytes(gray), quality
