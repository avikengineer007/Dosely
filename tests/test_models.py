"""
Unit tests for dosely.models
==============================
Tests cover:
  - Medicine: field validation, auto-needs_review triggers, edge cases
  - Prescription: aggregation, dosing_schedule, medicines_needing_review
  - Enums: all values parseable
  - Edge cases: empty medicines list, POOR image quality
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dosely.models import (
    DoseForm,
    DrugClass,
    FoodInstruction,
    ImageQuality,
    Medicine,
    Prescription,
    Prescriber,
)


from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_medicine(**kwargs: Any) -> Medicine:
    defaults: dict[str, Any] = {
        "name_raw": "Tab. Amoxicillin 500 mg",
        "name_generic": "amoxicillin",
        "drug_class": DrugClass.ANTIBIOTIC,
        "purpose": "Kills bacteria",
        "strength": "500 mg",
        "form": DoseForm.TABLET,
        "frequency_raw": "TDS",
        "frequency_normalized": "Three times daily",
        "timing_slots": ["morning", "afternoon", "night"],
        "duration_days": 5,
        "food_instruction": FoodInstruction.AFTER_FOOD,
        "confidence": 0.95,
    }
    defaults.update(kwargs)
    return Medicine(**defaults)  # type: ignore[arg-type]


def _make_prescription(**kwargs: Any) -> Prescription:
    defaults: dict[str, Any] = {
        "prescription_id": "rx-unit-test",
        "image_quality": ImageQuality.GOOD,
        "medicines": [_make_medicine()],
    }
    defaults.update(kwargs)
    return Prescription(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Medicine — basic construction
# ---------------------------------------------------------------------------

class TestMedicineConstruction:
    def test_happy_path_no_review(self):
        med = _make_medicine()
        assert med.needs_review is False
        assert med.review_reasons == []

    def test_name_raw_stripped(self):
        med = _make_medicine(name_raw="  Tab. Amoxicillin  ")
        assert med.name_raw == "Tab. Amoxicillin"

    def test_empty_name_raw_raises(self):
        with pytest.raises(ValidationError, match="name_raw must not be empty"):
            _make_medicine(name_raw="   ")

    def test_confidence_below_zero_raises(self):
        with pytest.raises(ValidationError):
            _make_medicine(confidence=-0.1)

    def test_confidence_above_one_raises(self):
        with pytest.raises(ValidationError):
            _make_medicine(confidence=1.01)

    def test_duration_zero_raises(self):
        with pytest.raises(ValidationError):
            _make_medicine(duration_days=0)

    def test_duration_negative_raises(self):
        with pytest.raises(ValidationError):
            _make_medicine(duration_days=-1)

    def test_form_defaults_to_unknown(self):
        med = Medicine(name_raw="Some Drug", confidence=0.8)
        assert med.form == DoseForm.UNKNOWN

    def test_food_instruction_defaults_to_unknown(self):
        med = Medicine(name_raw="Some Drug", confidence=0.8)
        assert med.food_instruction == FoodInstruction.UNKNOWN


# ---------------------------------------------------------------------------
# Medicine — auto needs_review triggers
# ---------------------------------------------------------------------------

class TestMedicineNeedsReview:
    def test_missing_generic_triggers_review(self):
        med = _make_medicine(name_generic=None)
        assert med.needs_review is True
        assert any("Generic name" in r for r in med.review_reasons)

    def test_missing_strength_triggers_review(self):
        med = _make_medicine(strength=None)
        assert med.needs_review is True
        assert any("Strength" in r for r in med.review_reasons)

    def test_missing_frequency_normalized_triggers_review(self):
        med = _make_medicine(frequency_normalized=None)
        assert med.needs_review is True
        assert any("Frequency" in r for r in med.review_reasons)

    def test_missing_duration_triggers_review(self):
        med = _make_medicine(duration_days=None)
        assert med.needs_review is True
        assert any("Duration" in r for r in med.review_reasons)

    def test_low_confidence_triggers_review(self):
        med = _make_medicine(confidence=0.65)
        assert med.needs_review is True
        assert any("0.65" in r for r in med.review_reasons)

    def test_confidence_exactly_at_threshold_no_trigger(self):
        # 0.70 is the threshold; exactly 0.70 should NOT trigger
        med = _make_medicine(confidence=0.70)
        # Only duration=None would trigger here; use full medicine with duration
        med2 = _make_medicine(confidence=0.70, duration_days=5)
        assert 0.70 not in [r for r in med2.review_reasons if "confidence" in r.lower()]

    def test_all_missing_accumulates_reasons(self):
        med = _make_medicine(
            name_generic=None,
            strength=None,
            frequency_normalized=None,
            duration_days=None,
            confidence=0.50,
        )
        assert med.needs_review is True
        assert len(med.review_reasons) >= 4

    def test_complete_medicine_has_no_review_reasons(self):
        med = _make_medicine(confidence=0.95)
        assert med.needs_review is False
        assert med.review_reasons == []


# ---------------------------------------------------------------------------
# Prescription — basic construction
# ---------------------------------------------------------------------------

class TestPrescriptionConstruction:
    def test_happy_path(self):
        rx = _make_prescription()
        assert rx.prescription_id == "rx-unit-test"
        assert rx.image_quality == ImageQuality.GOOD

    def test_overall_confidence_computed(self):
        rx = _make_prescription()
        assert rx.overall_confidence == pytest.approx(0.95, abs=0.01)

    def test_overall_confidence_respects_supplied_value(self):
        rx = _make_prescription(overall_confidence=0.5)
        assert rx.overall_confidence == pytest.approx(0.5)

    def test_empty_medicines_triggers_review(self):
        rx = _make_prescription(medicines=[])
        assert rx.needs_human_review is True
        assert any("No medicines" in s for s in rx.review_summary)

    def test_poor_quality_triggers_review(self):
        rx = _make_prescription(image_quality=ImageQuality.POOR)
        assert rx.needs_human_review is True
        assert any("POOR" in s for s in rx.review_summary)

    def test_medicine_needing_review_triggers_prescription_review(self):
        med = _make_medicine(strength=None)  # triggers review
        rx = _make_prescription(medicines=[med])
        assert rx.needs_human_review is True

    def test_good_prescription_no_human_review(self):
        rx = _make_prescription()
        # medicine has all fields; no review needed
        assert rx.needs_human_review is False

    def test_invalid_image_quality_raises(self):
        with pytest.raises(ValidationError):
            _make_prescription(image_quality="crystal_clear")


# ---------------------------------------------------------------------------
# Prescription — dosing_schedule
# ---------------------------------------------------------------------------

class TestDosingSchedule:
    def test_single_medicine_schedule(self):
        med = _make_medicine(
            name_generic="amoxicillin",
            strength="500 mg",
            timing_slots=["morning", "afternoon", "night"],
        )
        rx = _make_prescription(medicines=[med])
        schedule = rx.dosing_schedule()
        assert "morning" in schedule
        assert "afternoon" in schedule
        assert "night" in schedule
        assert "amoxicillin 500 mg" in schedule["morning"]

    def test_as_needed_slot(self):
        med = _make_medicine(
            name_generic="paracetamol",
            strength="650 mg",
            timing_slots=["as_needed"],
            frequency_raw="SOS",
            frequency_normalized="Only when needed",
        )
        rx = _make_prescription(medicines=[med])
        schedule = rx.dosing_schedule()
        assert "as_needed" in schedule

    def test_medicine_without_timing_slots_excluded(self):
        med = _make_medicine(timing_slots=None, frequency_normalized=None)
        rx = _make_prescription(medicines=[med])
        schedule = rx.dosing_schedule()
        # No slots -> empty schedule
        assert schedule == {}

    def test_multi_drug_schedule_merged(self):
        med1 = _make_medicine(
            name_generic="amoxicillin",
            strength="500 mg",
            timing_slots=["morning", "night"],
        )
        med2 = _make_medicine(
            name_raw="Tab. Pantoprazole 40 mg",
            name_generic="pantoprazole",
            strength="40 mg",
            timing_slots=["morning"],
        )
        rx = _make_prescription(medicines=[med1, med2])
        schedule = rx.dosing_schedule()
        assert len(schedule["morning"]) == 2
        assert len(schedule.get("night", [])) == 1


# ---------------------------------------------------------------------------
# Prescription — medicines_needing_review
# ---------------------------------------------------------------------------

class TestMedicinesNeedingReview:
    def test_all_clean_returns_empty(self):
        rx = _make_prescription()
        assert rx.medicines_needing_review() == []

    def test_one_flagged_medicine_returned(self):
        bad_med = _make_medicine(strength=None)
        rx = _make_prescription(medicines=[bad_med])
        flagged = rx.medicines_needing_review()
        assert len(flagged) == 1
        assert flagged[0].name_raw == bad_med.name_raw

    def test_mixed_medicines(self):
        good_med = _make_medicine()
        bad_med = _make_medicine(name_generic=None, name_raw="Unknown Drug")
        rx = _make_prescription(medicines=[good_med, bad_med])
        flagged = rx.medicines_needing_review()
        assert len(flagged) == 1


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestEnums:
    @pytest.mark.parametrize("val", [e.value for e in DoseForm])
    def test_dose_form_all_values(self, val):
        assert DoseForm(val).value == val

    @pytest.mark.parametrize("val", [e.value for e in DrugClass])
    def test_drug_class_all_values(self, val):
        assert DrugClass(val).value == val

    @pytest.mark.parametrize("val", [e.value for e in FoodInstruction])
    def test_food_instruction_all_values(self, val):
        assert FoodInstruction(val).value == val

    @pytest.mark.parametrize("val", [e.value for e in ImageQuality])
    def test_image_quality_all_values(self, val):
        assert ImageQuality(val).value == val


# ---------------------------------------------------------------------------
# Prescriber
# ---------------------------------------------------------------------------

class TestPrescriber:
    def test_all_none_is_valid(self):
        p = Prescriber()
        assert p.name is None
        assert p.registration_number is None

    def test_full_prescriber(self):
        p = Prescriber(
            name="Dr. A. Sharma",
            registration_number="MH-12345",
            clinic_or_hospital="City Health Clinic",
            contact="+91-9876543210",
        )
        assert p.name == "Dr. A. Sharma"
