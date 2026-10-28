"""
dosely/config.py
================
Application-wide configuration constants read from environment variables,
with safe defaults for development.

All sensitive settings (API keys, DB paths, encryption keys) must be supplied
via environment variables.  Never hard-code secrets here.
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Purge settings
# ---------------------------------------------------------------------------

#: Data retention period in days.  Prescriptions older than this are purged
#: by the background job and by POST /admin/purge.
PURGE_DAYS: int = int(os.environ.get("PURGE_DAYS", "30"))

#: How often the background purge job runs (in hours).
PURGE_INTERVAL_HOURS: float = float(os.environ.get("PURGE_INTERVAL_HOURS", "24"))

# ---------------------------------------------------------------------------
# JWT / Auth settings
# ---------------------------------------------------------------------------

def get_jwt_secret_key() -> str:
    from dosely.storage import init_secrets
    return init_secrets()[1]


JWT_ALGORITHM: str = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRE_MINUTES: int = int(os.environ.get("JWT_EXPIRE_MINUTES", "60"))

# ---------------------------------------------------------------------------
# Storage settings
# ---------------------------------------------------------------------------

ENCRYPTED_UPLOADS_DIR: str = os.environ.get(
    "ENCRYPTED_UPLOADS_DIR", "data/encrypted_uploads"
)
DB_PATH: str = os.environ.get("DB_PATH", "data/prescriptions.db")

# ---------------------------------------------------------------------------
# Preprocessing Pipeline & Quality Settings
# ---------------------------------------------------------------------------

#: Ordered sequence of active pipeline stage names.
#: Deskew runs before illumination so rotational alignment precedes morphological background estimation.
PIPELINE_STAGES: list[str] = [
    "orientation",
    "perspective",
    "grayscale",
    "deskew",
    "illumination",
    "denoise",
    "contrast",
    "sharpen",
    "resolution",
    "crop",
]

#: Per-stage enable/disable toggles
STAGE_ORIENTATION_ENABLED: bool = os.environ.get("STAGE_ORIENTATION_ENABLED", "true").lower() in ("true", "1", "yes")
STAGE_PERSPECTIVE_ENABLED: bool = os.environ.get("STAGE_PERSPECTIVE_ENABLED", "true").lower() in ("true", "1", "yes")
STAGE_GRAYSCALE_ENABLED: bool = os.environ.get("STAGE_GRAYSCALE_ENABLED", "true").lower() in ("true", "1", "yes")
STAGE_DESKEW_ENABLED: bool = os.environ.get("STAGE_DESKEW_ENABLED", "true").lower() in ("true", "1", "yes")
STAGE_ILLUMINATION_ENABLED: bool = os.environ.get("STAGE_ILLUMINATION_ENABLED", "true").lower() in ("true", "1", "yes")
STAGE_DENOISE_ENABLED: bool = os.environ.get("STAGE_DENOISE_ENABLED", "true").lower() in ("true", "1", "yes")
STAGE_CONTRAST_ENABLED: bool = os.environ.get("STAGE_CONTRAST_ENABLED", "true").lower() in ("true", "1", "yes")
STAGE_SHARPEN_ENABLED: bool = os.environ.get("STAGE_SHARPEN_ENABLED", "true").lower() in ("true", "1", "yes")
STAGE_RESOLUTION_ENABLED: bool = os.environ.get("STAGE_RESOLUTION_ENABLED", "true").lower() in ("true", "1", "yes")
STAGE_CROP_ENABLED: bool = os.environ.get("STAGE_CROP_ENABLED", "true").lower() in ("true", "1", "yes")
STAGE_BINARIZATION_ENABLED: bool = os.environ.get("STAGE_BINARIZATION_ENABLED", "false").lower() in ("true", "1", "yes")

#: Stage-specific default parameters
CLAHE_CLIP_LIMIT: float = float(os.environ.get("CLAHE_CLIP_LIMIT", "2.0"))
CLAHE_TILE_GRID: tuple[int, int] = (8, 8)
DESKEW_LIMIT: float = float(os.environ.get("DESKEW_LIMIT", "10.0"))
DESKEW_MIN_ANGLE: float = float(os.environ.get("DESKEW_MIN_ANGLE", "0.5"))
MAX_PERSPECTIVE_STRETCH: float = float(os.environ.get("MAX_PERSPECTIVE_STRETCH", "2.0"))
MARGIN_PADDING_PX: int = int(os.environ.get("MARGIN_PADDING_PX", "25"))
PDF_MAX_PAGES: int = int(os.environ.get("PDF_MAX_PAGES", "10"))
MAX_PIXEL_COUNT: int = int(os.environ.get("MAX_PIXEL_COUNT", "8000000"))
TARGET_TEXT_HEIGHT_PX: float = float(os.environ.get("TARGET_TEXT_HEIGHT_PX", "18.0"))
DUAL_READ_ON_FAIR_ENABLED: bool = os.environ.get("DUAL_READ_ON_FAIR_ENABLED", "false").lower() in ("true", "1", "yes")
PREPROCESSING_TIMEOUT_SECONDS: float = float(os.environ.get("PREPROCESSING_TIMEOUT_SECONDS", "3.0"))

#: Maximum dimension (width or height) for whole-page images passed to vision APIs.
#: SAFETY INVARIANT: Individual line crops and segmentation MUST be cut from the full-resolution
#: preprocessed image before any whole-page downscaling occurs.
API_MAX_IMAGE_DIMENSION: int = int(os.environ.get("API_MAX_IMAGE_DIMENSION", "1568"))



#: Documented thresholds for original image quality assessment (good/fair/poor)
QUALITY_THRESHOLDS: dict[str, float] = {
    # Sharpness (Laplacian variance)
    "sharpness_good": 150.0,
    "sharpness_poor": 50.0,
    # Motion-blur anisotropy ratio (high ratio with moderate gradient indicates motion blur)
    "motion_blur_max_fair": 0.65,
    "motion_blur_max_poor": 0.85,
    # Brightness (mean pixel value, 0-255)
    "brightness_min_good": 55.0,
    "brightness_min_fair": 35.0,
    "brightness_min_poor": 25.0,
    "brightness_max_good": 220.0,
    "brightness_max_fair": 235.0,
    "brightness_max_poor": 242.0,
    # Clipping percentage (percentage of saturated 0 or 255 pixels)
    "clipping_max_fair": 25.0,
    "clipping_max_poor": 45.0,
    # Contrast (RMS std dev and percentile spread)
    "contrast_rms_min_good": 35.0,
    "contrast_rms_min_fair": 22.0,
    "contrast_rms_min_poor": 12.0,
    "contrast_spread_min_good": 70.0,
    "contrast_spread_min_fair": 45.0,
    "contrast_spread_min_poor": 20.0,
    # Resolution & Text height (pixels)
    "min_text_height_fair": 12.0,
    "min_text_height_hard_floor": 8.0,
    # Glare (% of saturated specular blobs)
    "glare_max_good": 2.0,
    "glare_max_fair": 4.0,
    "glare_max_poor": 8.0,
    # Skew angle (degrees)
    "skew_max_good": 5.0,
    "skew_max_fair": 15.0,
    # Shadow severity (variation of patch illumination)
    "shadow_max_fair": 45.0,
    "shadow_max_poor": 70.0,
    # Page coverage (% of frame)
    "page_coverage_min_fair": 35.0,
    "page_coverage_min_poor": 15.0,
}

# ---------------------------------------------------------------------------
# Handwriting Feature Flags & Safeguard Banners
# ---------------------------------------------------------------------------

#: Mode for handwritten prescription support: 'off' | 'beta' | 'on'
HANDWRITING_MODE: str = os.environ.get("HANDWRITING_MODE", "beta").lower()

#: User-facing banner shown during beta handwriting processing
HANDWRITING_BETA_BANNER: str = (
    "Handwriting is read on a best-effort basis. Always check each medicine against your prescription."
)

#: User-facing message returned when handwriting mode is disabled
HANDWRITING_OFF_MESSAGE: str = (
    "Handwritten prescription extraction is currently not supported. Please upload a clear printed prescription."
)


