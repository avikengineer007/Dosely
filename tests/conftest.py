"""
Pytest configuration and shared fixtures.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import cv2  # type: ignore
import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"


# ---------------------------------------------------------------------------
# Image fixtures
# ---------------------------------------------------------------------------

def _make_white_png(width: int = 400, height: int = 200) -> bytes:
    """Create a minimal valid white PNG in-memory using OpenCV."""
    img = np.ones((height, width), dtype=np.uint8) * 255
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def _make_noisy_gray_png(width: int = 400, height: int = 200) -> bytes:
    """Create a noisy grayscale PNG (simulates a low-quality scan)."""
    rng = np.random.default_rng(42)
    img = rng.integers(20, 80, size=(height, width), dtype=np.uint8)
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


def _make_skewed_text_png(angle_deg: float = 5.0) -> bytes:
    """Create a white PNG with black horizontal lines at a slight angle."""
    img = np.ones((300, 600), dtype=np.uint8) * 255
    for y in range(30, 290, 40):
        cv2.line(img, (10, y), (590, y + int(590 * np.tan(np.radians(angle_deg)))), 0, 2)
    _, buf = cv2.imencode(".png", img)
    return buf.tobytes()


@pytest.fixture(scope="session")
def white_png() -> bytes:
    return _make_white_png()


@pytest.fixture(scope="session")
def noisy_png() -> bytes:
    return _make_noisy_gray_png()


@pytest.fixture(scope="session")
def skewed_png() -> bytes:
    return _make_skewed_text_png(angle_deg=4.0)


# ---------------------------------------------------------------------------
# Mock Claude client
# ---------------------------------------------------------------------------

GOOD_PRESCRIPTION_JSON = """{
  "prescription_id": "rx-test-001",
  "image_quality": "good",
  "medicines": [
    {
      "name_raw": "Tab. Amoxicillin 500 mg",
      "name_generic": "amoxicillin",
      "drug_class": "antibiotic",
      "purpose": "Kills bacteria causing the infection",
      "strength": "500 mg",
      "form": "tablet",
      "frequency_raw": "TDS",
      "frequency_normalized": "Three times daily (morning, afternoon & night)",
      "timing_slots": ["morning", "afternoon", "night"],
      "duration_days": 5,
      "food_instruction": "after_food",
      "confidence": 0.97,
      "needs_review": false,
      "review_reasons": []
    }
  ]
}"""

INVALID_JSON_RESPONSE = "Sorry, I cannot extract that."

BAD_SCHEMA_JSON = """{
  "prescription_id": "rx-bad-001",
  "image_quality": "unknown_quality_value",
  "medicines": []
}"""

FIXED_SCHEMA_JSON = """{
  "prescription_id": "rx-bad-001",
  "image_quality": "fair",
  "medicines": []
}"""


@pytest.fixture
def mock_claude_good() -> Generator[MagicMock, None, None]:
    """Mock Anthropic client that returns a valid prescription JSON."""
    with patch("dosely.extractor.anthropic.Anthropic") as MockClass:
        instance = MockClass.return_value
        msg = MagicMock()
        msg.content = [MagicMock(text=GOOD_PRESCRIPTION_JSON)]
        instance.messages.create.return_value = msg
        # Also patch the module-level client
        with patch("dosely.extractor._anthropic_client", instance):
            yield instance


@pytest.fixture
def mock_claude_invalid_then_fixed() -> Generator[MagicMock, None, None]:
    """Mock that returns invalid JSON first, then valid JSON on retry."""
    with patch("dosely.extractor.anthropic.Anthropic") as MockClass:
        instance = MockClass.return_value
        first_msg = MagicMock()
        first_msg.content = [MagicMock(text=INVALID_JSON_RESPONSE)]
        fixed_msg = MagicMock()
        fixed_msg.content = [MagicMock(text=GOOD_PRESCRIPTION_JSON)]
        instance.messages.create.side_effect = [first_msg, fixed_msg]
        with patch("dosely.extractor._anthropic_client", instance):
            yield instance


@pytest.fixture
def mock_claude_always_invalid() -> Generator[MagicMock, None, None]:
    """Mock that always returns invalid JSON (tests double-failure fallback)."""
    with patch("dosely.extractor.anthropic.Anthropic") as MockClass:
        instance = MockClass.return_value
        msg = MagicMock()
        msg.content = [MagicMock(text=INVALID_JSON_RESPONSE)]
        instance.messages.create.return_value = msg
        with patch("dosely.extractor._anthropic_client", instance):
            yield instance
