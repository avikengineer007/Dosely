"""
Tests for Task 4: Multi-read consensus and disagreement detection
"""

from typing import Optional
from unittest.mock import MagicMock, patch
import pytest

from dosely.consensus import (
    ConsensusConfig,
    ConsensusFieldResult,
    ConsensusLineResult,
    reconcile_line_reads,
    run_line_multiread_consensus,
)
from dosely.models import FieldRead, Medicine, ReadAlternative


def _make_candidate_medicine(name: str, strength: str = "500 mg", freq: str = "BD", dur: Optional[int] = 5) -> Medicine:
    fields = {
        "name_" + "raw": name,
        "raw_transcription": name,
        "name_interpreted": name,
        "name_read": FieldRead(text=name, confidence=0.9),
        "strength": strength,
        "strength_read": FieldRead(text=strength, confidence=0.9),
        "frequency_raw": freq,
        "frequency_read": FieldRead(text=freq, confidence=0.9),
        "duration_days": dur,
        "duration_read": FieldRead(text=f"{dur} days", confidence=0.9) if dur else None,
        "confidence": 0.9,
    }
    return Medicine.model_validate(fields)


class TestMultiReadConsensus:
    def test_unanimous_agreement_on_all_fields(self):
        """Unanimous 3-of-3 agreement yields agreed=True, no dispute."""
        reads = [
            _make_candidate_medicine("Metformin", "500 mg", "BD", 5),
            _make_candidate_medicine("Metformin", "500 mg", "BD", 5),
            _make_candidate_medicine("Metformin", "500 mg", "BD", 5),
        ]
        cfg = ConsensusConfig(n_reads=3, k_name_strength=3, k_freq_dur=2)
        res = reconcile_line_reads(reads, cfg)

        assert res.name_result.is_agreed is True
        assert res.name_result.winning_value == "Metformin"
        assert res.name_result.is_disputed is False

        assert res.strength_result.is_agreed is True
        assert res.strength_result.winning_value == "500 mg"

        assert res.frequency_result.is_agreed is True
        assert res.frequency_result.winning_value == "BD"

        assert res.duration_result.is_agreed is True

    def test_two_of_three_agreement_on_drug_name_is_disputed(self):
        """2-of-3 agreement on drug name violates unanimous (K=N=3) requirement and MUST be disputed."""
        reads = [
            _make_candidate_medicine("Metformin", "500 mg", "BD", 5),
            _make_candidate_medicine("Metformin", "500 mg", "BD", 5),
            _make_candidate_medicine("Metoprolol", "500 mg", "BD", 5),
        ]
        cfg = ConsensusConfig(n_reads=3, k_name_strength=3, k_freq_dur=2)
        res = reconcile_line_reads(reads, cfg)

        # Name must be disputed because K=3 is required for drug names!
        assert res.name_result.is_agreed is False
        assert res.name_result.is_disputed is True
        assert res.name_result.vote_counts == {"Metformin": 2, "Metoprolol": 1}
        assert res.medicine.needs_review is True
        assert any("disputed" in r.lower() for r in res.medicine.review_reasons)

        # But frequency (K=2) should still be agreed
        assert res.frequency_result.is_agreed is True

    def test_total_disagreement(self):
        """All reads disagree on name, strength and frequency."""
        reads = [
            _make_candidate_medicine("Amoxicillin", "250 mg", "TDS", 7),
            _make_candidate_medicine("Ampicillin", "500 mg", "BD", 5),
            _make_candidate_medicine("Aspirin", "75 mg", "OD", 30),
        ]
        cfg = ConsensusConfig(n_reads=3, k_name_strength=3, k_freq_dur=2)
        res = reconcile_line_reads(reads, cfg)

        assert res.name_result.is_disputed is True
        assert res.strength_result.is_disputed is True
        assert res.frequency_result.is_disputed is True
        assert res.medicine.needs_review is True
        assert len(res.distinct_candidates) == 3

    def test_one_read_failing_reduces_agreement_and_forces_review(self):
        """When 1 of 3 reads fails/errors, agreement cannot reach K=3 for name."""
        reads = [
            _make_candidate_medicine("Paracetamol", "650 mg", "TDS", 3),
            _make_candidate_medicine("Paracetamol", "650 mg", "TDS", 3),
            # 3rd read failed (returned unreadable / None)
            _make_candidate_medicine("unreadable_line", "None", "None", dur=None),
        ]
        cfg = ConsensusConfig(n_reads=3, k_name_strength=3, k_freq_dur=2)
        res = reconcile_line_reads(reads, cfg)

        assert res.name_result.is_disputed is True
        assert res.medicine.needs_review is True

    def test_early_exit_when_first_two_reads_agree_and_exact_db_match(self):
        """Early exit triggers when first 2 reads agree and match exact known drug in database."""
        call_count = 0

        def mock_read(crop, scale, pad, temp):
            nonlocal call_count
            call_count += 1
            return _make_candidate_medicine("paracetamol", "500 mg", "TDS", 5)

        cfg = ConsensusConfig(n_reads=3, early_exit_enabled=True)
        res = run_line_multiread_consensus(
            crop_bytes=b"dummy_crop",
            read_fn=mock_read,
            known_exact_drugs={"paracetamol", "metformin", "amoxicillin"},
            config=cfg,
        )

        assert call_count == 2  # Exited early after 2 matching reads!
        assert res.name_result.is_agreed is True
        assert res.early_exit_triggered is True

    def test_budget_exhaustion_falls_back_to_degraded_single_read_needs_review(self):
        """When budget is exhausted, degrade to single read + force needs_review on every handwritten field."""
        call_count = 0

        def mock_read(crop, scale, pad, temp):
            nonlocal call_count
            call_count += 1
            return _make_candidate_medicine("Metformin", "500 mg", "BD", 5)

        # Budget allows only 1 call
        cfg = ConsensusConfig(n_reads=3, max_reads_budget=1)
        res = run_line_multiread_consensus(
            crop_bytes=b"dummy_crop",
            read_fn=mock_read,
            known_exact_drugs={"metformin"},
            config=cfg,
        )

        assert call_count == 1
        assert res.is_degraded is True
        assert res.medicine.needs_review is True
        assert any("budget" in r.lower() or "degraded" in r.lower() for r in res.medicine.review_reasons)
