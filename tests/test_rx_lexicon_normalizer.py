"""
Tests for Task 3: Prescription abbreviation and shorthand lexicon & normalizer
"""

import json
from pathlib import Path
import pytest

from dosely.rx_lexicon import (
    AMBIGUOUS,
    CanonicalFrequency,
    assess_strength_plausibility,
    normalize_handwritten_frequency,
)


class TestRxLexicon:
    def test_lexicon_file_exists_and_unreviewed(self):
        lexicon_path = Path(__file__).resolve().parent.parent / "data" / "rx_lexicon.json"
        assert lexicon_path.exists(), "data/rx_lexicon.json must exist"
        data = json.loads(lexicon_path.read_text(encoding="utf-8"))
        assert data.get("clinically_reviewed") is False, "Must not be marked clinically reviewed"

    @pytest.mark.parametrize(
        "raw_input, expected_canonical, expected_slots",
        [
            ("OD", "Once daily", ["morning"]),
            ("od", "Once daily", ["morning"]),
            ("BD", "Twice daily", ["morning", "night"]),
            ("BID", "Twice daily", ["morning", "night"]),
            ("TDS", "Three times daily", ["morning", "afternoon", "night"]),
            ("TID", "Three times daily", ["morning", "afternoon", "night"]),
            ("QID", "Four times daily", ["morning", "afternoon", "evening", "night"]),
            ("HS", "At bedtime", ["night"]),
            ("SOS", "As needed", ["as_needed"]),
            ("PRN", "As needed", ["as_needed"]),
            ("1-0-1", "Twice daily", ["morning", "night"]),
            ("1+0+1", "Twice daily", ["morning", "night"]),
            ("0-0-1", "Once daily at night", ["night"]),
            ("stat", "Immediately (single dose)", ["immediate"]),
            ("AF", "After food", None),
            ("BF", "Before food", None),
            ("empty stomach", "Before food / Empty stomach", None),
        ],
    )
    def test_lexicon_positive_mappings(self, raw_input, expected_canonical, expected_slots):
        res = normalize_handwritten_frequency(raw_input)
        assert res.status != AMBIGUOUS
        assert res.canonical == expected_canonical
        if expected_slots is not None:
            assert res.timing_slots == expected_slots

    @pytest.mark.parametrize(
        "ambiguous_input, expected_candidates",
        [
            ("O/B D", ["OD (Once daily)", "BD (Twice daily)"]),
            ("OD or BD", ["OD (Once daily)", "BD (Twice daily)"]),
            ("1/7", ["1 tablet", "7 days"]),
            ("tid or bid", ["TID (Three times daily)", "BID (Twice daily)"]),
        ],
    )
    def test_handwriting_confusions_produce_ambiguous(self, ambiguous_input, expected_candidates):
        res = normalize_handwritten_frequency(ambiguous_input)
        assert res.status == AMBIGUOUS
        assert res.canonical is None
        assert res.timing_slots is None  # Scheduler must get no default timings
        assert len(res.candidates) >= 2

    def test_normalizer_never_invents_frequency_not_in_transcription(self):
        """Random unmapped or gibberish strings must not be assigned a frequency."""
        res = normalize_handwritten_frequency("random xyz 999 unmapped")
        assert res.canonical is None
        assert res.timing_slots is None
        assert res.status == "unrecognized"

    @pytest.mark.parametrize(
        "drug_name, strength_str, is_plausible",
        [
            ("paracetamol", "500 mg", True),
            ("paracetamol", "650 mg", True),
            ("paracetamol", "5000 mg", False),  # Lethal / toxic overdose
            ("amoxicillin", "500 mg", True),
            ("amoxicillin", "25 g", False),     # Implausible unit confusion
            ("thyroxine", "50 mcg", True),
            ("thyroxine", "50 mg", False),      # 1000x overdose confusion (mcg vs mg)
        ],
    )
    def test_strength_plausibility_and_unit_confusion(self, drug_name, strength_str, is_plausible):
        assessment = assess_strength_plausibility(drug_name, strength_str)
        if is_plausible:
            assert assessment.is_plausible is True
            assert assessment.review_required is False
        else:
            assert assessment.is_plausible is False
            assert assessment.review_required is True
            assert len(assessment.reason) > 0
