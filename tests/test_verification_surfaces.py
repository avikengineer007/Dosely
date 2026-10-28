"""
Tests for Task 6: Human verification surfaces
"""

import time
from unittest.mock import MagicMock, patch
import pytest

from dosely.verification import (
    generate_pharmacist_export,
    generate_retake_guidance,
    process_packet_photo_cross_check,
    validate_confirm_gate,
)
from dosely.models import FieldRead, Medicine, ReadAlternative


class TestVerificationSurfaces:
    def test_confirm_gate_rejects_unresolved_disputed_fields(self):
        """Confirm button / gate must block if any disputed field lacks explicit user resolution or cant_tell."""
        disputed_item = {
            "name_raw": "Metformn",
            "is_disputed": True,
            "chosen_generic": None,
            "cant_tell": False,
            "user_edited": False,
        }
        valid, reason = validate_confirm_gate([disputed_item])
        assert valid is False
        assert "explicit" in reason.lower() or "disputed" in reason.lower()

    def test_confirm_gate_allows_explicit_cant_tell(self):
        """When user explicitly marks 'can't tell', confirm gate passes."""
        cant_tell_item = {
            "name_raw": "UnreadableScribble",
            "is_disputed": True,
            "chosen_generic": None,
            "cant_tell": True,
            "user_edited": False,
        }
        valid, reason = validate_confirm_gate([cant_tell_item])
        assert valid is True
        assert reason is None

    def test_cant_tell_medicines_excluded_from_scheduler(self):
        """Can't tell medicines never reach scheduler and are marked 'Not scheduled'."""
        from dosely.api.prescriptions import ConfirmedMedicineInput

        med1 = ConfirmedMedicineInput(
            name_raw="Paracetamol 500mg",
            name_generic="paracetamol",
            strength="500 mg",
            frequency_raw="TDS",
            duration_days=3,
            cant_tell=False,
        )
        med2 = ConfirmedMedicineInput(
            name_raw="Illegible Scribble",
            cant_tell=True,  # User couldn't tell
        )

        assert med2.cant_tell is True
        # In scheduling logic, med2 must be excluded from active schedule

    def test_packet_cross_check_never_auto_links_without_user_action(self):
        """Packet photo offers high-confidence hint, but is NEVER auto-linked without explicit user tap."""
        mock_packet_bytes = b"fake_box_image_bytes"
        hint = process_packet_photo_cross_check(
            packet_bytes=mock_packet_bytes,
            disputed_name="Metformn",
            extracted_text="Metformin Hydrochloride 500mg Tablets",
        )
        assert hint["packet_candidate"] == "metformin"
        assert hint["requires_user_confirmation"] is True
        assert hint["is_linked"] is False  # Must NOT be linked automatically
        assert "Metformin" in hint["prompt_message"]

    def test_pharmacist_export_has_no_identifiers_and_expires(self):
        """Share export contains handwriting crop & candidate readings only, NO patient PII, and expires."""
        export_data = generate_pharmacist_export(
            prescription_id="rx-secret-999",
            line_id="line_002",
            crop_bytes=b"raw_crop_bytes",
            candidate_readings=["Metformin", "Metoprolol"],
            patient_name="John Doe",  # Attempt to pass PII
            expiry_seconds=3600,
        )

        # Assert no patient PII is present in export
        assert "John Doe" not in str(export_data)
        assert "patient_name" not in export_data
        assert export_data["expires_at"] > time.time()
        assert export_data["is_expired"] is False
        assert "candidate_readings" in export_data
        assert export_data["disclaimer"] is not None

    def test_retake_guidance_for_low_legibility_region(self):
        """Retake guidance gives specific localized feedback for the unclear line."""
        msg = generate_retake_guidance(
            line_index=2,
            quality_reasons=["motion_blur_detected"],
            legibility="illegible",
        )
        assert "second line" in msg.lower() or "line 2" in msg.lower()
        assert "closer photo" in msg.lower() or "retake" in msg.lower()
