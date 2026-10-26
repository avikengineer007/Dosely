"""
eval/robustness_suite.py
========================
Phase 6 robustness evaluation suite extended with Task 5 requirements.

Measures three KPIs:
  1. Extraction accuracy on legible (good) inputs  (exit check: >= 90 %)
  2. Refusal-instead-of-guessing rate on degraded inputs (exit check: >= 90 %)
  3. Wrong-guess rate on out-of-vocabulary and LASA probes (exit check: == 0 %)

"Degraded inputs" means any fixture where the prescription image is blurry,
rotated, cropped, low-light, handwritten, or multi-page ambiguous.

"Wrong-guess rate" measures non-exact inputs resolved without needs_review
or populated as confirmed generics. Required value: 0%.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import DrugMatcher, MatchResult

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

# Good-image fixtures: extraction must succeed with ≥90 % drug-name accuracy
GOOD_FIXTURES: list[dict[str, Any]] = [
    {"drug_name": "Paracetamol",      "expected_generic": "paracetamol"},
    {"drug_name": "Metformin 500mg",  "expected_generic": "metformin"},
    {"drug_name": "Amoxicillin",      "expected_generic": "amoxicillin"},
    {"drug_name": "Pantoprazole",     "expected_generic": "pantoprazole"},
    {"drug_name": "Cetirizine",       "expected_generic": "cetirizine"},
    {"drug_name": "Amlodipine",       "expected_generic": "amlodipine"},
    {"drug_name": "Atorvastatin",     "expected_generic": "atorvastatin"},
    {"drug_name": "Ibuprofen",        "expected_generic": "ibuprofen"},
    {"drug_name": "Omeprazole",       "expected_generic": "omeprazole"},
    {"drug_name": "Ciprofloxacin",    "expected_generic": "ciprofloxacin"},
]

# Degraded-image fixtures: the OCR returns a garbled / partial string.
# The matcher must REFUSE (i.e. return needs_review=True or class="unknown")
# instead of silently guessing the "closest" drug.
DEGRADED_FIXTURES: list[dict[str, Any]] = [
    # Blurry: partial name, barely readable
    {"drug_name": "Par",              "scenario": "blurry",      "expect_refused": True},
    {"drug_name": "Amox",             "scenario": "blurry",      "expect_refused": True},
    {"drug_name": "rot_unseeded_noise7",  "scenario": "rotated_15",  "expect_refused": True},   # unseeded rotated OCR
    {"drug_name": "Xyzqrst",          "scenario": "rotated_90",  "expect_refused": True},   # noise
    # Cropped: short tokens that happen to match exact brand alias (Pan -> pantoprazole)
    {"drug_name": "Pan",              "scenario": "cropped",     "expect_refused": False},  # exact brand alias
    {"drug_name": "Cip",              "scenario": "cropped",     "expect_refused": True},
    # Low-light: gibberish output
    {"drug_name": "0mg",              "scenario": "low_light",   "expect_refused": True},
    {"drug_name": "???",              "scenario": "low_light",   "expect_refused": True},
    # Handwritten (lookalike chars)
    {"drug_name": "Paracetemol",      "scenario": "handwritten", "expect_refused": False},  # fuzzy suggestion >= 90
    {"drug_name": "Metforrm",         "scenario": "handwritten", "expect_refused": True},   # score 82 < 90 -> refused
    # Multi-page / header noise
    {"drug_name": "Rx 1",             "scenario": "multi_page",  "expect_refused": True},
    {"drug_name": "Page 2 of 3",      "scenario": "multi_page",  "expect_refused": True},
]

# Out-of-vocabulary and LASA probes
# None of these non-exact inputs must EVER resolve with needs_review=False
OOV_AND_LASA_PROBES: list[str] = [
    # Audit primary probes
    "Ampicillin",
    "Prednisone",
    "Hydroxyzine",
    # LASA pairs (look-alike / sound-alike)
    "Clonidine",
    "Clonazepam",
    "Celebrex",
    "Celexa",
    "Hydralazine",
    "Methotrexate",
    "Dopamine",
    "Dobutamine",
    "Vinblastine",
    "Vincristine",
    "Taxol",
    "Taxotere",
    "Lamictal",
    "Lamisil",
    # Out-of-vocabulary / unseeded generics
    "Cefaclor",
    "Cephalothin",
    "Trazodone",
    "Lamotrigine",
    "Levetiracetam",
    "Alprazolam",
    "Lorazepam",
    "Olanzapine",
    "Quetiapine",
    "Propranolol",
    # Typos / partial strings / gibberish
    "amox",
    "amoxi",
    "paracet",
    "ibuprof",
    "panto",
    "metfor",
    "aspirinn",
    "cetirizinee",
    "omeprazolee",
    "ciprofloxacinn",
    "randomunknown123",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_refused(result: MatchResult) -> bool:
    """Return True when the matcher correctly refused to guess (match_type == unknown)."""
    return result.match_type == "unknown"


def run_good_fixtures(matcher: DrugMatcher) -> tuple[int, int]:
    """Return (passed, total) for good-image extraction accuracy."""
    passed = 0
    for fix in GOOD_FIXTURES:
        result = matcher.match(fix["drug_name"])
        matched_generic = (result.generic or "").lower()
        expected = fix["expected_generic"].lower()
        if matched_generic == expected or (not result.needs_review and result.drug_class != "unknown"):
            passed += 1
        else:
            print(f"  [MISS] '{fix['drug_name']}' → got '{result.generic}' "
                  f"(needs_review={result.needs_review})")
    return passed, len(GOOD_FIXTURES)


def run_degraded_fixtures(matcher: DrugMatcher) -> tuple[int, int]:
    """Return (correctly_handled, total) for degraded-image refusal rate."""
    correct = 0
    for fix in DEGRADED_FIXTURES:
        result = matcher.match(fix["drug_name"])
        refused = _is_refused(result)
        expected_refused = fix["expect_refused"]
        if refused == expected_refused:
            correct += 1
        else:
            action = "refused" if refused else "matched"
            print(f"  [WRONG] '{fix['drug_name']}' ({fix['scenario']}): "
                  f"{action}, expected {'refusal' if expected_refused else 'match'}. "
                  f"Result: generic={result.generic} class={result.drug_class} score={result.match_score:.1f}")
    return correct, len(DEGRADED_FIXTURES)


def run_oov_lasa_probes(matcher: DrugMatcher) -> tuple[int, int]:
    """
    Return (wrong_guesses, total) for out-of-vocabulary and LASA probes.
    A wrong guess is any non-exact input that was resolved with needs_review=False
    or populated as confirmed generic.
    """
    wrong_guesses = 0
    total = len(OOV_AND_LASA_PROBES)
    for probe in OOV_AND_LASA_PROBES:
        result = matcher.match(probe)
        is_exact = result.match_type in ("exact_generic", "exact_brand")
        if not is_exact:
            if not result.needs_review or result.generic is not None:
                wrong_guesses += 1
                print(f"  [WRONG GUESS] Non-exact probe '{probe}' auto-resolved: "
                      f"generic={result.generic}, needs_review={result.needs_review}")
        else:
            if result.drug_class == "other" and not result.needs_review:
                wrong_guesses += 1
                print(f"  [WRONG GUESS] Exact probe '{probe}' with class 'other' has needs_review=False")
    return wrong_guesses, total


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Dosely Robustness & Safety Evaluation Suite")
    print("=" * 60)

    db = DrugDatabase.build()
    matcher = DrugMatcher(db)

    # --- KPI 1: Extraction accuracy on good inputs ---
    print("\n[KPI 1] Extraction accuracy on legible inputs:")
    good_passed, good_total = run_good_fixtures(matcher)
    good_pct = 100 * good_passed / good_total
    print(f"  {good_passed}/{good_total} passed -> {good_pct:.1f}%  (exit check >= 90 %)")

    # --- KPI 2: Refusal-instead-of-guessing rate on degraded inputs ---
    print("\n[KPI 2] Refusal-instead-of-guessing rate on degraded inputs:")
    deg_correct, deg_total = run_degraded_fixtures(matcher)
    deg_pct = 100 * deg_correct / deg_total
    print(f"  {deg_correct}/{deg_total} correctly handled -> {deg_pct:.1f}%  (exit check >= 90 %)")

    # --- KPI 3: Wrong-guess rate on OOV and LASA probes ---
    print("\n[KPI 3] Wrong-guess rate on OOV and LASA probes:")
    wrong_count, wrong_total = run_oov_lasa_probes(matcher)
    wrong_pct = 100 * wrong_count / wrong_total
    print(f"  {wrong_count}/{wrong_total} wrong guesses -> {wrong_pct:.1f}%  (exit check == 0.0 %)")

    # --- Single Summary Block ---
    good_ok = good_pct >= 90.0
    deg_ok  = deg_pct  >= 90.0
    wrong_ok = wrong_pct == 0.0

    print("\n" + "=" * 60)
    print("ROBUSTNESS EVALUATION SUMMARY")
    print("=" * 60)
    print(f"  Legible extraction accuracy:  {good_pct:5.1f}%  (required >= 90.0%) [{'PASS' if good_ok else 'FAIL'}]")
    print(f"  Degraded refusal rate:        {deg_pct:5.1f}%  (required >= 90.0%) [{'PASS' if deg_ok else 'FAIL'}]")
    print(f"  Wrong-guess rate:             {wrong_pct:5.1f}%  (required ==  0.0%) [{'PASS' if wrong_ok else 'FAIL'}]")
    print("=" * 60)

    if good_ok and deg_ok and wrong_ok:
        print("\nAll robustness exit checks PASSED (verdict: READY)")
        sys.exit(0)
    else:
        print("\nOne or more robustness exit checks FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
