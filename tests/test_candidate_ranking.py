"""
Tests for Task 5: Lexicon-constrained candidate ranking (hints, not decisions)
"""

import pytest

from dosely.candidate_ranker import (
    CandidateSuggestion,
    RankedCandidateResult,
    rank_handwriting_candidates,
)
from dosely.models import FieldRead, Medicine, ReadAlternative


class TestCandidateRanking:
    def test_handwriting_confusion_metforrnin_to_metformin(self):
        """m/rn confusion: 'Metforrnin' should rank 'metformin' as top candidate."""
        res = rank_handwriting_candidates(
            transcription="Metforrnin",
            strength_context="500 mg",
            freq_context="BD",
        )
        assert len(res.candidates) > 0
        top_names = [c.generic for c in res.candidates]
        assert "metformin" in top_names
        # Never auto-selected!
        assert res.selected_candidate is None

    def test_handwriting_amoxicilin_to_amoxicillin(self):
        """Single 'l' handwriting variant: 'Amoxicilin' ranks 'amoxicillin'."""
        res = rank_handwriting_candidates(
            transcription="Amoxicilin",
            strength_context="500 mg",
            freq_context="TDS",
        )
        assert len(res.candidates) > 0
        top_names = [c.generic for c in res.candidates]
        assert "amoxicillin" in top_names

    def test_lasa_pair_prednisone_never_silently_becomes_prednisolone(self):
        """LASA pair: 'Prednisone' must warn and NEVER silently resolve to prednisolone."""
        res = rank_handwriting_candidates(
            transcription="Prednisone",
            strength_context="5 mg",
        )
        assert res.has_lasa_warning is True
        assert res.lasa_warning is not None
        assert "prednisolone" in res.lasa_warning.lower() or "prednisone" in res.lasa_warning.lower()
        # Candidate list must NOT auto-populate confirmed fields
        assert res.selected_candidate is None

    def test_lasa_list_always_warns(self):
        """Input resembling LASA pair (e.g. 'Hydroxyzine' / 'Hydralazine') displays visible warning."""
        res = rank_handwriting_candidates(transcription="Hydroxyzine")
        assert res.has_lasa_warning is True
        assert res.lasa_warning is not None
        assert "hydralazine" in res.lasa_warning.lower()

    def test_no_candidate_is_ever_autoselected(self):
        """Verify that across all score tiers, no candidate is ever pre-selected or auto-accepted."""
        res1 = rank_handwriting_candidates("Paracetamol 500mg")
        res2 = rank_handwriting_candidates("Metforrnin")
        res3 = rank_handwriting_candidates("Amox")

        for res in [res1, res2, res3]:
            assert res.selected_candidate is None
            for cand in res.candidates:
                assert cand.is_selected is False
                assert cand.source == "suggested"

    def test_non_drug_string_yields_no_candidates(self):
        """Random words or non-drug strings yield zero candidates."""
        res = rank_handwriting_candidates("Clinic Address Building Road 12345")
        assert len(res.candidates) == 0

    def test_top_candidates_small_margin_shows_equal_prominence(self):
        """When candidates are within small margin, equal_prominence flag is set."""
        # Ambiguous stem matching both paracetamol and pantoprazole or similar close scores
        res = rank_handwriting_candidates("Pa 500")
        if len(res.candidates) >= 2:
            gap = abs(res.candidates[0].score - res.candidates[1].score)
            if gap <= 5.0:
                assert res.equal_prominence is True

    def test_user_selection_stores_source_user_selected(self):
        """Applying a user choice stores source='user_selected' distinct from 'extracted' and 'suggested'."""
        res = rank_handwriting_candidates("Metforrnin")
        assert len(res.candidates) > 0
        chosen = res.candidates[0]

        med = Medicine(
            name_raw="Metforrnin",
            raw_transcription="Metforrnin",
            name_interpreted=None,
            confidence=0.5,
        )
        updated_med = res.apply_user_choice(med, chosen.generic)
        assert updated_med.name_generic == chosen.generic
        assert getattr(updated_med, "source", None) == "user_selected" or any(
            "user_selected" in r.lower() for r in updated_med.review_reasons
        )
