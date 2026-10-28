"""
Tests for Task 7: Handwriting evaluation and thresholds
"""

import json
from pathlib import Path
import pytest

from eval.handwriting_eval import (
    check_api_credit_available,
    evaluate_handwriting_offline,
    run_full_handwriting_evaluation,
)


class TestHandwritingEvaluationAndThresholds:
    def test_real_handwriting_holdout_folder_git_ignored(self):
        """data/real_handwriting/ must be git-ignored and have README with instructions."""
        gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
        assert "data/real_handwriting/" in gitignore

        readme = (Path(__file__).resolve().parent.parent / "data" / "real_handwriting" / "README.md")
        assert readme.exists()
        content = readme.read_text(encoding="utf-8")
        assert "NEVER COMMIT REAL PRESCRIPTIONS" in content
        assert "DE-IDENTIFICATION" in content

    def test_synthetic_reports_labeled_synthetic(self):
        """Synthetic benchmark datasets must strictly label entries with SYNTHETIC disclaimer."""
        from eval.render_handwriting import generate_synthetic_evaluation_dataset, SYNTHETIC_DIR

        generate_synthetic_evaluation_dataset(10)
        gt_file = SYNTHETIC_DIR / "ground_truth.json"
        assert gt_file.exists()
        items = json.loads(gt_file.read_text(encoding="utf-8"))
        assert len(items) >= 10
        for item in items:
            assert item["label"] == "SYNTHETIC"
            assert "easier proxy" in item["disclaimer"].lower()

    def test_confident_wrong_rate_is_zero(self):
        """Zero confident-wrong answers must hold across evaluated benchmarks."""
        items = [
            {"raw_text": "Metformin 500mg", "ground_truth_drug": "metformin", "ground_truth_strength": "500 mg", "ground_truth_frequency": "BD"},
            {"raw_text": "Paracetamol 650mg", "ground_truth_drug": "paracetamol", "ground_truth_strength": "650 mg", "ground_truth_frequency": "TDS"},
            {"raw_text": "Unclear Scribble", "ground_truth_drug": "amoxicillin", "ground_truth_strength": "500 mg", "ground_truth_frequency": "OD"},
        ]
        metrics = evaluate_handwriting_offline(items, label="SYNTHETIC")
        assert metrics.confident_wrong_rate == 0.0

    def test_verdict_prints_not_yet_supported_until_thresholds_met(self):
        """System must enforce 'HANDWRITING: NOT YET SUPPORTED' until 100 real lines / 5 writers tested."""
        results = run_full_handwriting_evaluation()
        assert results["verdict"] == "HANDWRITING: NOT YET SUPPORTED"
        assert results["is_supported"] is False
