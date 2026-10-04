"""
Prescription Extraction — Evaluation Script
============================================
Runs all test cases in data/test_cases.json, compares extracted medicine
fields to ground-truth, and prints per-field accuracy.

Usage
-----
  # Against ground-truth JSON only (no real API call):
  python -m eval.evaluate --mode offline

  # Against the live API (requires ANTHROPIC_API_KEY and real images):
  python -m eval.evaluate --mode online --image-dir path/to/images/

Offline mode
------------
In offline mode the script validates that each test case in test_cases.json
can be loaded by the Pydantic model (schema compliance check) and computes
field-level coverage statistics directly from the ground-truth data.
This is useful for CI without an API key or real prescription images.

Online mode
-----------
In online mode the script reads image files from --image-dir (files must be
named <prescription_id>.<ext>, e.g. "rx-tc-001.jpg") and calls the extractor.
It then compares extracted field values to the ground-truth in test_cases.json.

Field comparison strategy
--------------------------
Each field uses the most appropriate comparator:
  name_raw           — normalised string equality (lower + strip)
  name_generic       — normalised string equality
  strength           — normalised string equality
  form               — enum value equality
  frequency_raw      — normalised string equality
  frequency_normalized — normalised string equality
  duration_days      — integer equality
  food_instruction   — enum value equality
  drug_class         — enum value equality
  confidence         — within ±0.15 of ground-truth
  needs_review       — exact bool equality
  image_quality      — exact string equality
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import ValidationError

# Add project root to path so dosely is importable when run as a script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dosely.models import Medicine, Prescription  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Field comparators
# ---------------------------------------------------------------------------

def _norm_str(v: Any) -> str | None:
    if v is None:
        return None
    return str(v).lower().strip()


def _compare_field(field: str, gt: Any, pred: Any) -> bool:
    """
    Return True if predicted value matches ground truth for this field.
    Both None values count as a match (field was correctly absent).
    """
    if field == "confidence":
        if gt is None and pred is None:
            return True
        if gt is None or pred is None:
            return False
        return abs(float(gt) - float(pred)) <= 0.15

    if field in ("name_raw", "name_generic", "strength",
                 "frequency_raw", "frequency_normalized"):
        return _norm_str(gt) == _norm_str(pred)

    if field in ("form", "food_instruction", "drug_class"):
        return _norm_str(gt) == _norm_str(pred)

    if field == "duration_days":
        if gt is None and pred is None:
            return True
        if gt is None or pred is None:
            return False
        return int(gt) == int(pred)

    if field in ("needs_review", "image_quality"):
        return str(gt).lower() == str(pred).lower()

    # Default: string equality
    return _norm_str(gt) == _norm_str(pred)


# ---------------------------------------------------------------------------
# Medicine-level fields to evaluate
# ---------------------------------------------------------------------------

MEDICINE_FIELDS = [
    "name_raw",
    "name_generic",
    "strength",
    "form",
    "frequency_raw",
    "frequency_normalized",
    "duration_days",
    "food_instruction",
    "drug_class",
    "confidence",
    "needs_review",
]

PRESCRIPTION_FIELDS = [
    "image_quality",
]


# ---------------------------------------------------------------------------
# Offline evaluation
# ---------------------------------------------------------------------------

def run_offline(test_cases: list[dict]) -> None:
    """
    Schema compliance check + ground-truth field coverage statistics.
    No API calls are made.
    """
    print("\n" + "=" * 70)
    print("OFFLINE MODE — Schema compliance + ground-truth field coverage")
    print("=" * 70)

    schema_errors: list[str] = []
    field_present: dict[str, int] = defaultdict(int)
    field_total: dict[str, int] = defaultdict(int)
    total_prescriptions = 0
    total_medicines = 0

    for tc in test_cases:
        tc_id = tc.get("id", "unknown")
        raw_rx = tc.get("prescription", {})

        # --- Schema validation ---
        try:
            rx = Prescription.model_validate(raw_rx)
        except ValidationError as exc:
            schema_errors.append(f"[{tc_id}] {exc}")
            logger.error("[%s] Schema error: %s", tc_id, exc)
            continue

        total_prescriptions += 1

        # --- Prescription-level field coverage ---
        for f in PRESCRIPTION_FIELDS:
            field_total[f"prescription.{f}"] += 1
            val = getattr(rx, f, None)
            if val is not None:
                field_present[f"prescription.{f}"] += 1

        # --- Medicine-level field coverage ---
        for med in rx.medicines:
            total_medicines += 1
            for f in MEDICINE_FIELDS:
                field_total[f"medicine.{f}"] += 1
                val = getattr(med, f, None)
                # For bool, None is still a value
                if f == "needs_review" or val is not None:
                    field_present[f"medicine.{f}"] += 1

    # --- Schema summary ---
    print(f"\nTest cases parsed : {len(test_cases)}")
    print(f"  Schema valid    : {total_prescriptions}")
    print(f"  Schema errors   : {len(schema_errors)}")
    if schema_errors:
        for e in schema_errors:
            print(f"    ERROR: {e}")

    print(f"\nTotal medicines   : {total_medicines}")

    # --- Field coverage table ---
    print("\n" + "-" * 70)
    print(f"{'Field':<45} {'Present':>8} {'Total':>8} {'Coverage':>9}")
    print("-" * 70)

    all_fields = sorted(set(field_total.keys()))
    for f in all_fields:
        total = field_total[f]
        present = field_present.get(f, 0)
        pct = (present / total * 100) if total else 0.0
        print(f"{f:<45} {present:>8} {total:>8} {pct:>8.1f}%")

    print("-" * 70)
    print("\nOffline evaluation complete.")


# ---------------------------------------------------------------------------
# Online evaluation
# ---------------------------------------------------------------------------

def run_online(test_cases: list[dict], image_dir: Path) -> None:
    """
    Call the extractor on real images and compare to ground truth.
    Images must be named <prescription_id>.<ext>.
    """
    # Import here to avoid loading extractor (and requiring API key) in offline mode
    from dosely.extractor import extract  # noqa: PLC0415

    print("\n" + "=" * 70)
    print("ONLINE MODE — Live extraction vs ground truth")
    print("=" * 70)

    field_correct: dict[str, int] = defaultdict(int)
    field_total: dict[str, int] = defaultdict(int)

    processed = 0
    skipped = 0

    for tc in test_cases:
        tc_id = tc.get("id", "unknown")
        raw_rx = tc.get("prescription", {})
        rx_id = raw_rx.get("prescription_id", tc_id)

        # --- Find image file ---
        image_path: Path | None = None
        for ext in (".jpg", ".jpeg", ".png", ".tiff", ".bmp", ".pdf"):
            candidate = image_dir / f"{rx_id}{ext}"
            if candidate.exists():
                image_path = candidate
                break

        if image_path is None:
            logger.warning("[%s] No image found in %s — skipping.", tc_id, image_dir)
            skipped += 1
            continue

        # --- Parse ground truth ---
        try:
            gt_rx = Prescription.model_validate(raw_rx)
        except ValidationError as exc:
            logger.error("[%s] Ground truth schema error — skipping: %s", tc_id, exc)
            skipped += 1
            continue

        # --- Run extractor ---
        logger.info("[%s] Extracting from %s ...", tc_id, image_path.name)
        image_bytes = image_path.read_bytes()
        try:
            pred_rx = extract(image_bytes, prescription_id=rx_id)
        except Exception as exc:
            logger.error("[%s] Extraction exception: %s", tc_id, exc)
            skipped += 1
            continue

        processed += 1

        # --- Prescription-level fields ---
        for f in PRESCRIPTION_FIELDS:
            field_total[f"prescription.{f}"] += 1
            gt_val = getattr(gt_rx, f, None)
            pred_val = getattr(pred_rx, f, None)
            if _compare_field(f, gt_val, pred_val):
                field_correct[f"prescription.{f}"] += 1

        # --- Medicine-level fields ---
        # Align predicted medicines to ground-truth by position
        gt_meds = gt_rx.medicines
        pred_meds = pred_rx.medicines
        n_pairs = min(len(gt_meds), len(pred_meds))

        for i in range(n_pairs):
            gt_med = gt_meds[i]
            pred_med = pred_meds[i]
            for f in MEDICINE_FIELDS:
                field_total[f"medicine.{f}"] += 1
                gt_val = getattr(gt_med, f, None)
                pred_val = getattr(pred_med, f, None)
                if _compare_field(f, gt_val, pred_val):
                    field_correct[f"medicine.{f}"] += 1

        # Penalise missing medicines
        for i in range(n_pairs, len(gt_meds)):
            for f in MEDICINE_FIELDS:
                field_total[f"medicine.{f}"] += 1  # correct=0 (not counted above)

        logger.info(
            "[%s] Done. GT medicines: %d, Extracted: %d",
            tc_id,
            len(gt_meds),
            len(pred_meds),
        )

    # --- Results table ---
    print(f"\nTest cases   : {len(test_cases)}")
    print(f"  Processed  : {processed}")
    print(f"  Skipped    : {skipped}")

    print("\n" + "-" * 70)
    print(f"{'Field':<45} {'Correct':>8} {'Total':>8} {'Accuracy':>9}")
    print("-" * 70)

    all_fields = sorted(set(field_total.keys()))
    for f in all_fields:
        total = field_total[f]
        correct = field_correct.get(f, 0)
        pct = (correct / total * 100) if total else 0.0
        print(f"{f:<45} {correct:>8} {total:>8} {pct:>8.1f}%")

    print("-" * 70)

    # Overall accuracy
    total_all = sum(field_total.values())
    correct_all = sum(field_correct.values())
    overall = (correct_all / total_all * 100) if total_all else 0.0
    print(f"\nOverall accuracy across all fields: {correct_all}/{total_all} = {overall:.1f}%")
    print("\nOnline evaluation complete.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_test_cases(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "test_cases" in data:
        return data["test_cases"]
    raise ValueError(f"Unexpected test_cases.json structure in {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate the Dosely prescription extractor.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mode",
        choices=["offline", "online"],
        default="offline",
        help="offline: schema check + ground-truth stats (default). "
             "online: run extractor against real images.",
    )
    parser.add_argument(
        "--test-cases",
        type=Path,
        default=PROJECT_ROOT / "data" / "test_cases.json",
        help="Path to test_cases.json (default: data/test_cases.json)",
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=None,
        help="[online mode] Directory containing prescription images. "
             "Files must be named <prescription_id>.<ext>.",
    )
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        metavar="TC_ID",
        help="Only evaluate test cases whose ID contains this string.",
    )

    args = parser.parse_args()

    if not args.test_cases.exists():
        print(f"ERROR: test cases file not found: {args.test_cases}", file=sys.stderr)
        sys.exit(1)

    test_cases = _load_test_cases(args.test_cases)
    print(f"Loaded {len(test_cases)} test cases from {args.test_cases}")

    if args.filter:
        test_cases = [tc for tc in test_cases if args.filter in tc.get("id", "")]
        print(f"After filter '{args.filter}': {len(test_cases)} test cases")

    if args.mode == "offline":
        run_offline(test_cases)
    else:
        if args.image_dir is None:
            print(
                "ERROR: --image-dir is required for online mode.", file=sys.stderr
            )
            sys.exit(1)
        if not args.image_dir.is_dir():
            print(
                f"ERROR: image directory not found: {args.image_dir}", file=sys.stderr
            )
            sys.exit(1)
        run_online(test_cases, args.image_dir)


if __name__ == "__main__":
    main()
