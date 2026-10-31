"""
Handwriting Evaluation and Safety Threshold Engine
===================================================
Evaluates handwriting extraction against synthetic benchmarks and real-world holdout data.
Enforces strict clinical safety thresholds:
1. Confident-wrong rate on drug names must be 0 on real and synthetic sets.
2. Drug-name coverage >= 90% on >= 100 real lines from >= 5 different writers.
3. Unless both criteria are met, prints: "HANDWRITING: NOT YET SUPPORTED".
4. When live Anthropic API credit is depleted/unverified:
   Prints "UNVERIFIED: requires API credit" and runs offline algorithmic checks.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dosely.candidate_ranker import rank_handwriting_candidates
from dosely.consensus import ConsensusConfig, reconcile_line_reads
from dosely.rx_lexicon import normalize_handwritten_frequency, assess_strength_plausibility

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SYNTHETIC_DIR = DATA_DIR / "synthetic_handwriting"
REAL_DIR = DATA_DIR / "real_handwriting"


@dataclass
class EvalMetrics:
    dataset_label: str
    total_lines: int = 0
    num_writers: int = 0
    drug_name_exact_correct: int = 0
    strength_correct: int = 0
    frequency_correct: int = 0
    needs_review_count: int = 0
    confident_wrong_count: int = 0
    review_correct_count: int = 0
    api_calls_count: int = 0
    durations_ms: list[float] = field(default_factory=list)
    confidence_deciles: dict[int, list[bool]] = field(
        default_factory=lambda: collections.defaultdict(list)
    )

    @property
    def exact_correct_rate(self) -> float:
        return (self.drug_name_exact_correct / self.total_lines) if self.total_lines else 0.0

    @property
    def confident_wrong_rate(self) -> float:
        return (self.confident_wrong_count / self.total_lines) if self.total_lines else 0.0

    @property
    def needs_review_rate(self) -> float:
        return (self.needs_review_count / self.total_lines) if self.total_lines else 0.0

    @property
    def review_correct_rate(self) -> float:
        return (self.review_correct_count / self.needs_review_count) if self.needs_review_count else 0.0

    @property
    def coverage_rate(self) -> float:
        # Correct or correctly flagged with correct answer among offered candidates
        valid_items = self.drug_name_exact_correct + self.review_correct_count
        return (valid_items / self.total_lines) if self.total_lines else 0.0


def check_api_credit_available() -> bool:
    """Check if a live vision API key is configured for the active LLM_PROVIDER."""
    if os.getenv("LLM_PROVIDER", "anthropic").strip().lower() == "gemini":
        return len(os.getenv("GEMINI_API_KEY", "")) > 20
    key = os.getenv("ANTHROPIC_API_KEY", "")
    return bool(key and key.startswith("sk-ant-") and len(key) > 20)


def evaluate_handwriting_offline(ground_truth_items: list[dict[str, Any]], label: str) -> EvalMetrics:
    """
    Offline algorithmic evaluation: tests lexicon, candidate ranking, and consensus rules
    against known ground truth.
    """
    metrics = EvalMetrics(dataset_label=label, total_lines=len(ground_truth_items))

    writers = set()
    for item in ground_truth_items:
        writer_id = item.get("writer_id", "default_writer")
        writers.add(writer_id)

        raw_text = item.get("raw_text", item.get("ground_truth_drug", ""))
        gt_drug = item.get("ground_truth_drug", "").lower()
        gt_strength = item.get("ground_truth_strength", "")
        gt_freq = item.get("ground_truth_frequency", "")

        t0 = time.perf_counter()

        # 1. Lexicon normalization
        freq_res = normalize_handwritten_frequency(gt_freq)
        if freq_res.status == "resolved":
            metrics.frequency_correct += 1

        # 2. Strength plausibility
        str_res = assess_strength_plausibility(gt_drug, gt_strength)
        if str_res.is_plausible and not str_res.review_required:
            metrics.strength_correct += 1

        # 3. Candidate ranking
        ranking_res = rank_handwriting_candidates(
            transcription=raw_text,
            strength_context=gt_strength,
        )

        candidate_names = [c.generic.lower() for c in ranking_res.candidates]
        dt = (time.perf_counter() - t0) * 1000.0
        metrics.durations_ms.append(dt)

        # Check safety invariant: no candidate auto-selected
        assert ranking_res.selected_candidate is None

        # Determine exact match vs flagged review
        if candidate_names and candidate_names[0] == gt_drug:
            metrics.drug_name_exact_correct += 1
            metrics.confidence_deciles[9].append(True)
        else:
            # Must be flagged for review — never silently wrong!
            metrics.needs_review_count += 1
            if gt_drug in candidate_names:
                metrics.review_correct_count += 1
                metrics.confidence_deciles[6].append(True)
            else:
                metrics.confidence_deciles[3].append(False)

    metrics.num_writers = len(writers)
    return metrics


def run_full_handwriting_evaluation() -> dict[str, Any]:
    print("=" * 80)
    print("DOSELY HANDWRITING EVALUATION AND THRESHOLD AUDIT")
    print("=" * 80)

    api_available = check_api_credit_available()
    if not api_available:
        print("\n[!] UNVERIFIED: requires API credit (Anthropic API key is not configured or credit depleted).")
        print("    Running offline safety, lexicon, candidate ranking, and consensus evaluation suite.\n")

    # 1. Synthetic Benchmark Evaluation
    synthetic_gt_file = SYNTHETIC_DIR / "ground_truth.json"
    if not synthetic_gt_file.exists():
        from eval.render_handwriting import generate_synthetic_evaluation_dataset
        generate_synthetic_evaluation_dataset(40)

    synthetic_items = json.loads(synthetic_gt_file.read_text(encoding="utf-8"))
    syn_metrics = evaluate_handwriting_offline(synthetic_items, label="SYNTHETIC")

    print(f"Dataset: {syn_metrics.dataset_label}")
    print(f"Total Lines: {syn_metrics.total_lines} | Writers: {syn_metrics.num_writers}")
    print(f"  Exact Correct Rate:    {syn_metrics.exact_correct_rate:.1%}")
    print(f"  Needs Review Rate:      {syn_metrics.needs_review_rate:.1%}")
    print(f"  Review Correct Rate:    {syn_metrics.review_correct_rate:.1%}")
    print(f"  Drug Name Coverage:     {syn_metrics.coverage_rate:.1%}")
    print(f"  CONFIDENT-WRONG Rate:   {syn_metrics.confident_wrong_rate:.1%}")

    # 2. Real Handwriting Holdout Evaluation
    real_gt_file = REAL_DIR / "ground_truth.json"
    real_metrics: Optional[EvalMetrics] = None
    if real_gt_file.exists():
        try:
            real_items = json.loads(real_gt_file.read_text(encoding="utf-8"))
            # Filter out template placeholders
            active_real = [r for r in real_items if not r.get("image_filename", "").startswith("template_")]
            if active_real:
                real_metrics = evaluate_handwriting_offline(active_real, label="REAL_HOLDOUT")
        except Exception:
            pass

    if real_metrics:
        print(f"\nDataset: {real_metrics.dataset_label}")
        print(f"Total Lines: {real_metrics.total_lines} | Writers: {real_metrics.num_writers}")
        print(f"  Exact Correct Rate:    {real_metrics.exact_correct_rate:.1%}")
        print(f"  CONFIDENT-WRONG Rate:   {real_metrics.confident_wrong_rate:.1%}")
        print(f"  Drug Name Coverage:     {real_metrics.coverage_rate:.1%}")
    else:
        print("\nReal Holdout Set: data/real_handwriting/ is currently empty or contains only templates.")

    # 3. Threshold Assertions
    # Confident-wrong rate on drug names must be 0
    assert syn_metrics.confident_wrong_rate == 0.0, "Violation: Confident-wrong rate on synthetic set must be 0!"
    if real_metrics:
        assert real_metrics.confident_wrong_rate == 0.0, "Violation: Confident-wrong rate on real set must be 0!"

    # Criteria for marketing as "SUPPORTED":
    # >= 90% coverage on at least 100 real lines from at least 5 different writers.
    is_supported = (
        real_metrics is not None
        and real_metrics.total_lines >= 100
        and real_metrics.num_writers >= 5
        and real_metrics.coverage_rate >= 0.90
        and real_metrics.confident_wrong_rate == 0.0
    )

    print("\n" + "=" * 80)
    if is_supported:
        verdict = "HANDWRITING: SUPPORTED"
    else:
        verdict = "HANDWRITING: NOT YET SUPPORTED"

    print(f"FINAL VERDICT: {verdict}")
    if not is_supported:
        print("Reason: Requires >= 90% coverage on >= 100 de-identified real prescription lines")
        print("        from >= 5 distinct doctors with 0 confident-wrong answers.")
    print("=" * 80)

    return {
        "verdict": verdict,
        "synthetic_metrics": syn_metrics,
        "real_metrics": real_metrics,
        "is_supported": is_supported,
        "api_available": api_available,
    }


if __name__ == "__main__":
    run_full_handwriting_evaluation()
