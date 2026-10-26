"""
Test Task 1: Silent look-alike drug substitution fix & LASA guard list.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import DrugMatcher, MatchResult


@pytest.fixture(scope="module")
def matcher() -> DrugMatcher:
    db = DrugDatabase.build()
    return DrugMatcher(db)


def test_lasa_pairs_file_exists_and_has_at_least_30_pairs():
    lasa_path = Path(__file__).resolve().parent.parent / "data" / "lasa_pairs.json"
    assert lasa_path.exists(), f"File {lasa_path} does not exist"
    with open(lasa_path, "r", encoding="utf-8") as f:
        pairs = json.load(f)
    assert isinstance(pairs, list)
    assert len(pairs) >= 30, f"Expected at least 30 pairs, got {len(pairs)}"
    for item in pairs:
        assert isinstance(item, list) and len(item) == 2


def test_ampicillin_does_not_auto_resolve_to_amoxicillin(matcher: DrugMatcher):
    # Audit finding: Ampicillin fuzzy-matched to amoxicillin with needs_review=False
    r = matcher.match("Ampicillin")
    assert r.needs_review is True, "Ampicillin must require review"
    assert r.generic is None, f"generic must not be populated as confirmed, got '{r.generic}'"
    assert r.match_type in ("fuzzy_suggestion", "unknown")
    if r.match_type == "fuzzy_suggestion":
        assert r.suggested_generic == "amoxicillin"
        assert r.lasa_warning is not None, "LASA warning must be present"


def test_prednisone_does_not_auto_resolve_to_prednisolone(matcher: DrugMatcher):
    # Audit finding: Prednisone fuzzy-matched to prednisolone with confirmed generic
    r = matcher.match("Prednisone")
    assert r.needs_review is True
    assert r.generic is None
    assert r.match_type in ("fuzzy_suggestion", "unknown")
    if r.match_type == "fuzzy_suggestion":
        assert r.suggested_generic == "prednisolone"
        assert r.lasa_warning is not None


def test_fuzzy_result_sets_match_type_fuzzy_suggestion_and_no_generic(matcher: DrugMatcher):
    # Input with minor typo (e.g. 1 char typo on long generic name: amoxicilin)
    r = matcher.match("amoxicilin")
    assert r.needs_review is True
    assert r.generic is None
    assert r.drug_class == "unknown"
    assert r.match_type == "fuzzy_suggestion"
    assert r.suggested_generic == "amoxicillin"
    assert r.suggested_class == "antibiotic"
    assert r.match_score >= 0.90


def test_out_of_vocabulary_names_never_needs_review_false(matcher: DrugMatcher):
    # 7 named drugs + 20 more out-of-vocabulary drugs
    oov_names = [
        "Ampicillin",
        "Prednisone",
        "Hydroxyzine",
        "Clonidine",
        "Clonazepam",
        "Celebrex",
        "Celexa",
        # 20 more
        "Cefaclor",
        "Cephalothin",
        "Cefadroxil",
        "Trazodone",
        "Hydralazine",
        "Chlorpromazine",
        "Diazepam",
        "Diltiazem",
        "Lamotrigine",
        "Levetiracetam",
        "Alprazolam",
        "Lorazepam",
        "Olanzapine",
        "Quetiapine",
        "Paroxetine",
        "Fluoxetine",
        "Propranolol",
        "Atenolol",
        "Quinidine",
        "Quinine",
    ]
    for name in oov_names:
        r = matcher.match(name)
        # Spec: "must never return needs_review=False unless they exactly match"
        if r.match_type in ("exact_generic", "exact_brand"):
            continue
        assert r.needs_review is True, f"Non-exact drug '{name}' returned needs_review=False!"
        assert r.generic is None, f"Non-exact drug '{name}' auto-resolved to generic='{r.generic}'!"


def test_property_style_non_exact_inputs_always_needs_review_true(matcher: DrugMatcher):
    # Any input that is not an exact match for generic or brand MUST have needs_review=True
    non_exact_inputs = [
        "amox", "amoxi", "paracet", "ibuprof", "panto", "metfor",
        "randomnonexistent", "paracetamol extra", "12345", "aspirinn",
        "cetirizinee", "omeprazolee", "ciprofloxacinn"
    ]
    for inp in non_exact_inputs:
        r = matcher.match(inp)
        assert r.needs_review is True, f"Non-exact input '{inp}' returned needs_review=False"
        assert r.generic is None, f"Non-exact input '{inp}' returned confirmed generic '{r.generic}'"


def test_unresolved_item_scheduling_applies_no_default_timings():
    from dosely.scheduler.scheduler import MedicationScheduler
    from dosely.scheduler.models import DrugInput

    scheduler = MedicationScheduler()
    # Unresolved item with class="unknown" and no frequency
    drug = DrugInput(name="Ampicillin", drug_class="unknown", frequency_raw=None)
    timeline = scheduler.schedule([drug])
    assert len(timeline) == 1
    entry = timeline[0]
    # Must NOT apply morning or any other default clock timing
    assert entry.time == "as needed"
    assert "no default timing applied" in entry.instruction.lower()
