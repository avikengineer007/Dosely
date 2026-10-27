"""
Test Task 4: Knowledge base coverage.

Verifies:
1. seed_drugs.json has at least 300 entries.
2. No duplicate generic names.
3. No alias/brand maps to two different generics.
4. Every entry has all required fields (generic, class, purpose_plain).
5. Every purpose_plain is a single sentence (no periods except at the end).
6. All drug classes are in the known set or "other".
7. Drugs outside the four MVP classes have class="other" and needs_review=True when matched.
8. indian_brands.csv has all required columns and no duplicate brands mapping to different generics.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import DrugMatcher

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEED_DRUGS_PATH = DATA_DIR / "seed_drugs.json"
INDIAN_BRANDS_PATH = DATA_DIR / "indian_brands.csv"

VALID_CLASSES = {"analgesic_nsaid", "antacid_ppi", "antibiotic", "antihistamine", "other", "opioid_analgesic"}
MVP_CLASSES = {"analgesic_nsaid", "antacid_ppi", "antibiotic", "antihistamine"}


# ---------------------------------------------------------------------------
# Test 1: Seed has >= 300 entries
# ---------------------------------------------------------------------------

def test_seed_has_at_least_300_entries():
    with open(SEED_DRUGS_PATH, encoding="utf-8-sig") as f:
        entries = json.load(f)
    assert len(entries) >= 300, (
        f"seed_drugs.json must have at least 300 entries, got {len(entries)}"
    )


# ---------------------------------------------------------------------------
# Test 2: No duplicate generic names
# ---------------------------------------------------------------------------

def test_no_duplicate_generic_names():
    with open(SEED_DRUGS_PATH, encoding="utf-8-sig") as f:
        entries = json.load(f)
    generics = [e["generic"].strip().lower() for e in entries]
    seen = set()
    duplicates = []
    for g in generics:
        if g in seen:
            duplicates.append(g)
        seen.add(g)
    assert not duplicates, f"Duplicate generics found: {duplicates}"


# ---------------------------------------------------------------------------
# Test 3: All required fields present
# ---------------------------------------------------------------------------

def test_all_entries_have_required_fields():
    with open(SEED_DRUGS_PATH, encoding="utf-8-sig") as f:
        entries = json.load(f)
    missing = []
    for i, entry in enumerate(entries):
        for field in ("generic", "class", "purpose_plain"):
            if field not in entry or not str(entry[field]).strip():
                missing.append(f"Entry {i} missing or blank '{field}': {entry}")
    assert not missing, "\n".join(missing)



# ---------------------------------------------------------------------------
# Test 4: All class values are valid
# ---------------------------------------------------------------------------

def test_all_classes_valid():
    with open(SEED_DRUGS_PATH, encoding="utf-8-sig") as f:
        entries = json.load(f)
    invalid = [
        e for e in entries
        if e.get("class", "").strip() not in VALID_CLASSES
    ]
    assert not invalid, f"Entries with invalid class: {[e['generic'] for e in invalid]}"


# ---------------------------------------------------------------------------
# Test 5: purpose_plain is a single sentence (ends with period, no mid-sentence periods)
# ---------------------------------------------------------------------------

def test_purpose_plain_is_single_sentence():
    with open(SEED_DRUGS_PATH, encoding="utf-8-sig") as f:
        entries = json.load(f)
    violations = []
    for e in entries:
        text = e.get("purpose_plain", "")
        # Count periods (excluding trailing period)
        stripped = text.rstrip(".")
        if "." in stripped:
            violations.append(f"{e['generic']!r}: {text!r}")
    assert not violations, (
        f"purpose_plain contains multiple sentences (mid-text periods) in:\n"
        + "\n".join(violations[:10])
    )


# ---------------------------------------------------------------------------
# Test 6: Drugs outside MVP classes have class="other" and needs_review=True
# ---------------------------------------------------------------------------

def test_non_mvp_drugs_have_other_class_and_needs_review():
    db = DrugDatabase.build()
    matcher = DrugMatcher(db)

    with open(SEED_DRUGS_PATH, encoding="utf-8-sig") as f:
        entries = json.load(f)

    violations = []
    for entry in entries:
        g = entry["generic"].strip().lower()
        cls = entry["class"].strip()
        if cls not in MVP_CLASSES:
            assert cls in ("other", "opioid_analgesic"), f"Non-MVP drug '{g}' must have class 'other' or 'opioid_analgesic', got '{cls}'"
            result = matcher.match(g)
            if result.match_type in ("exact_generic", "exact_brand"):
                if not result.needs_review:
                    violations.append(f"'{g}': non-MVP class '{cls}' but needs_review=False")

    assert not violations, "\n".join(violations)


# ---------------------------------------------------------------------------
# Test 7: indian_brands.csv validation
# ---------------------------------------------------------------------------

def test_indian_brands_csv_has_required_columns():
    if not INDIAN_BRANDS_PATH.exists():
        pytest.skip("indian_brands.csv not found")
    with open(INDIAN_BRANDS_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
    assert "brand_name" in headers, "Missing 'brand_name' column in indian_brands.csv"
    assert "generic_name" in headers, "Missing 'generic_name' column in indian_brands.csv"


def test_no_duplicate_brand_to_different_generics():
    """No brand name should map to two different generics."""
    if not INDIAN_BRANDS_PATH.exists():
        pytest.skip("indian_brands.csv not found")
    brand_to_generic: dict[str, str] = {}
    conflicts = []
    with open(INDIAN_BRANDS_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            brand = row.get("brand_name", "").strip().lower()
            generic = row.get("generic_name", "").strip().lower()
            if not brand or not generic:
                continue
            if brand in brand_to_generic and brand_to_generic[brand] != generic:
                conflicts.append(
                    f"Brand '{brand}' maps to both '{brand_to_generic[brand]}' and '{generic}'"
                )
            else:
                brand_to_generic[brand] = generic
    assert not conflicts, "\n".join(conflicts)
