"""
Tests for Task 8: Product safeguards
"""

from unittest.mock import patch
import pytest

from dosely.config import (
    HANDWRITING_BETA_BANNER,
    HANDWRITING_MODE,
    HANDWRITING_OFF_MESSAGE,
)
from dosely.telemetry import SafeTelemetryRecorder


class TestProductSafeguards:
    def test_handwriting_mode_default_is_beta(self):
        assert HANDWRITING_MODE == "beta"
        assert "best-effort" in HANDWRITING_BETA_BANNER.lower()
        assert "always check" in HANDWRITING_BETA_BANNER.lower()

    def test_safe_telemetry_records_only_counts_and_rates_zero_content(self):
        """Telemetry must aggregate daily counts and rates without any PII/PHI or content."""
        recorder = SafeTelemetryRecorder()
        recorder.record_line_event(region_type="handwritten", is_disputed=True, cant_tell=False, retake_requested=False)
        recorder.record_line_event(region_type="printed", is_disputed=False, cant_tell=False, retake_requested=False)
        recorder.record_line_event(region_type="handwritten", is_disputed=True, cant_tell=True, retake_requested=True)

        stats = recorder.get_daily_summary()
        assert stats["total_lines"] == 3
        assert stats["handwritten_lines"] == 2
        assert stats["printed_lines"] == 1
        assert stats["disputed_rate"] == 2 / 3
        assert stats["cant_tell_rate"] == 1 / 3
        assert stats["retake_rate"] == 1 / 3

        # Zero health data or PII keys
        forbidden_keys = {"text", "drug", "name", "pixels", "crop", "prescription_id", "user_id"}
        assert forbidden_keys.isdisjoint(stats.keys())

    def test_never_guess_rules_file_updated_with_new_rules(self):
        from pathlib import Path
        rules_path = Path(__file__).resolve().parent.parent / "never_guess_rules.md"
        assert rules_path.exists()
        text = rules_path.read_text(encoding="utf-8")
        assert "candidates are hints" in text.lower() or "candidate lists are hints" in text.lower()
        assert "disputed reads" in text.lower()
        assert "can't-tell" in text.lower() or "cant_tell" in text.lower() or "can't tell" in text.lower()

    def test_privacy_policy_updated_with_crops_packets_and_pharmacist_link(self):
        from pathlib import Path
        policy_path = Path(__file__).resolve().parent.parent / "privacy_policy_and_consent.md"
        assert policy_path.exists()
        text = policy_path.read_text(encoding="utf-8")
        assert "line crops" in text.lower() or "handwriting crops" in text.lower()
        assert "packet" in text.lower()
        assert "pharmacist" in text.lower()
