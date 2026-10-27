"""
Task 2 Test Suite: Knowledge Base Clinical Quality & Safety Audit
================================================================
Reproduces audit Check 6 findings:
1. Synonyms: Acetaminophen and paracetamol as separate generics in seed_drugs.json.
2. Opioids: Codeine, tramadol, morphine classified as analgesic_nsaid.
3. Allow-lists: MVP classes must be strictly guarded by WHO ATC allow-lists.
4. Language lint: Tamoxifen and other drugs have alarming words (cancer, HIV, fatal, etc.).
5. Clinical review CSV and REQUIRE_CLINICAL_REVIEW flag.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import DrugMatcher, _KNOWN_CLASSES
from dosely.scheduler import MedicationScheduler
from dosely.scheduler.models import DrugInput
from dosely.synonyms import default_table

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SEED_PATH = DATA / "seed_drugs.json"
SYNONYMS_PATH = DATA / "synonyms.json"
ALLOWLISTS_PATH = DATA / "class_allowlists.json"
REVIEW_CSV_PATH = DATA / "review" / "pharmacist_review.csv"

MVP_CLASSES = {"analgesic_nsaid", "antacid_ppi", "antibiotic", "antihistamine"}
OPIOID_LIST = ["codeine", "tramadol", "morphine", "fentanyl", "oxycodone", "tapentadol", "buprenorphine"]


# ---------------------------------------------------------------------------
# 1. Synonyms & Deduplication
# ---------------------------------------------------------------------------

def test_synonyms_table_has_at_least_40_name_pairs():
    with open(SYNONYMS_PATH, encoding="utf-8") as f:
        data = json.load(f)
    syns = data.get("synonyms", {})
    assert len(syns) >= 40, f"Expected at least 40 synonym pairs, got {len(syns)}"
    # Verify critical pairs
    table = default_table()
    assert table.canonical("acetaminophen") == "paracetamol"
    assert table.canonical("albuterol") == "salbutamol"
    assert table.canonical("adrenaline") == "epinephrine" or table.canonical("epinephrine") == "adrenaline"


def test_no_two_seed_generics_are_synonyms_of_each_other():
    """Fails if seed_drugs.json contains two entries that are synonyms of each other."""
    with open(SEED_PATH, encoding="utf-8") as f:
        drugs = json.load(f)
    table = default_table()
    seen_canonical: dict[str, str] = {}
    for d in drugs:
        gen = d["generic"].strip().lower()
        canon = table.canonical(gen)
        if canon in seen_canonical:
            pytest.fail(
                f"Duplicate generic synonym in seed_drugs.json: '{gen}' and "
                f"'{seen_canonical[canon]}' both map to canonical '{canon}'"
            )
        seen_canonical[canon] = gen


# ---------------------------------------------------------------------------
# 2. Opioid Reclassification
# ---------------------------------------------------------------------------

def test_opioids_reclassified_to_opioid_analgesic():
    """Every opioid must be classified as 'opioid_analgesic', not 'analgesic_nsaid'."""
    with open(SEED_PATH, encoding="utf-8") as f:
        drugs = {d["generic"].strip().lower(): d for d in json.load(f)}

    for op in OPIOID_LIST:
        if op in drugs:
            cls = drugs[op]["class"]
            assert cls == "opioid_analgesic", (
                f"Opioid '{op}' has class '{cls}', expected 'opioid_analgesic'"
            )


def test_opioid_analgesic_is_outside_mvp_and_forces_review():
    """opioid_analgesic must not be in _KNOWN_CLASSES and must force needs_review=True."""
    assert "opioid_analgesic" not in _KNOWN_CLASSES

    db = DrugDatabase.build(strict=False)
    matcher = DrugMatcher(db)
    for op in ["codeine", "tramadol", "morphine"]:
        res = matcher.match(op)
        assert res.drug_class == "opioid_analgesic"
        assert res.needs_review is True
        assert any("outside the supported drug classes" in r or "opioid" in r.lower() for r in res.review_reasons)


def test_opioids_receive_no_default_timing():
    """Opioids must have no default timings applied when frequency is missing."""
    db = DrugDatabase.build(strict=False)
    matcher = DrugMatcher(db)
    res = matcher.match("tramadol")
    drug_input = DrugInput(name=res.generic or "tramadol", drug_class=res.drug_class, frequency_raw=None)
    entries = MedicationScheduler().schedule([drug_input])
    assert len(entries) == 1
    assert entries[0].time == "as needed"
    assert "no default timing applied" in entries[0].instruction.lower()


def test_opioid_plus_nsaid_does_not_warn_duplicate_nsaid_class():
    """Tramadol + Ibuprofen must NOT emit a duplicate NSAID class warning."""
    db = DrugDatabase.build(strict=False)
    matcher = DrugMatcher(db)
    tramadol = matcher.match("tramadol")
    ibuprofen = matcher.match("ibuprofen")
    entries = MedicationScheduler().schedule([
        DrugInput(name=tramadol.generic or "tramadol", drug_class=tramadol.drug_class, frequency_raw="SOS"),
        DrugInput(name=ibuprofen.generic or "ibuprofen", drug_class=ibuprofen.drug_class, frequency_raw="BD"),
    ])
    warnings = " ".join(w for e in entries for w in e.warnings).lower()
    assert "duplicate drug class 'analgesic_nsaid'" not in warnings


# ---------------------------------------------------------------------------
# 3. Class Allow-lists
# ---------------------------------------------------------------------------

def test_class_allowlists_exist_and_guard_all_mvp_entries():
    """Every drug in seed_drugs.json in an MVP class must be on the allow-list."""
    assert ALLOWLISTS_PATH.exists(), f"Missing allowlist file: {ALLOWLISTS_PATH}"
    with open(ALLOWLISTS_PATH, encoding="utf-8") as f:
        allowlists = json.load(f)

    for mvp in MVP_CLASSES:
        assert mvp in allowlists, f"Allow-list missing class '{mvp}'"
        assert len(allowlists[mvp]) > 0

    with open(SEED_PATH, encoding="utf-8") as f:
        drugs = json.load(f)

    for d in drugs:
        gen = d["generic"].strip().lower()
        cls = d["class"].strip().lower()
        if cls in MVP_CLASSES:
            allowed = {x.strip().lower() for x in allowlists[cls]}
            assert gen in allowed, (
                f"Drug '{gen}' has class '{cls}' but is not in {ALLOWLISTS_PATH} under '{cls}'"
            )


# ---------------------------------------------------------------------------
# 4. Language Linting over purpose_plain
# ---------------------------------------------------------------------------

ALARMING_PATTERN = re.compile(
    r"\b(?:cancer|tumor|tumour|malignant|fatal|death|deadly|lethal|poison|kill|hiv|overdose)\b",
    re.IGNORECASE,
)
DOSING_PATTERN = re.compile(
    r"\b\d+\s*(?:mg|ml|mcg|tablets?|pills?|times)\b",
    re.IGNORECASE,
)
MULTI_SENTENCE_PATTERN = re.compile(
    r"(?<=[a-z0-9])\.\s+[A-Z]",
)


def test_purpose_plain_language_lint():
    """Every purpose_plain in seed_drugs.json must pass clinical language linting."""
    with open(SEED_PATH, encoding="utf-8") as f:
        drugs = json.load(f)

    offenders = []
    for d in drugs:
        gen = d["generic"]
        p = d.get("purpose_plain", "").strip()
        if not p:
            continue
        # 1. Single sentence ending in period
        if not p.endswith(".") or MULTI_SENTENCE_PATTERN.search(p):
            offenders.append((gen, "multi-sentence or missing period", p))
        # 2. No dosing advice
        if DOSING_PATTERN.search(p):
            offenders.append((gen, "contains dosing advice", p))
        # 3. No alarming words
        m = ALARMING_PATTERN.search(p)
        if m:
            offenders.append((gen, f"alarming word: '{m.group(0)}'", p))

    if offenders:
        msg = "\n".join(f"{g} -> {reason}: {txt}" for g, reason, txt in offenders)
        pytest.fail(f"Found {len(offenders)} purpose_plain language lint violations:\n{msg}")


# ---------------------------------------------------------------------------
# 5. Pharmacist Review Export & REQUIRE_CLINICAL_REVIEW Config
# ---------------------------------------------------------------------------

def test_pharmacist_review_csv_exists_and_covers_all_entries():
    """pharmacist_review.csv must export all seed drugs and contain domperidone/ornidazole."""
    assert REVIEW_CSV_PATH.exists(), f"Missing {REVIEW_CSV_PATH}"
    with open(SEED_PATH, encoding="utf-8") as f:
        seed_generics = {d["generic"].strip().lower() for d in json.load(f)}

    with open(REVIEW_CSV_PATH, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required_cols = {"generic", "class", "purpose_plain", "source", "reviewed_by", "reviewed_on", "clinically_reviewed"}
        assert required_cols.issubset(set(reader.fieldnames or [])), f"CSV columns mismatch: {reader.fieldnames}"
        rows = list(reader)

    csv_generics = {r["generic"].strip().lower() for r in rows}
    assert "domperidone" in csv_generics
    assert "ornidazole" in csv_generics
    assert seed_generics == csv_generics

    # Clinically reviewed must default to false
    assert all(r["clinically_reviewed"].lower() == "false" for r in rows)


def test_require_clinical_review_forces_needs_review():
    """When REQUIRE_CLINICAL_REVIEW is True, unreviewed entries force needs_review=True."""
    with patch.dict(os.environ, {"REQUIRE_CLINICAL_REVIEW": "true"}):
        db = DrugDatabase.build(strict=False)
        matcher = DrugMatcher(db)
        res = matcher.match("amoxicillin")
        assert res.needs_review is True
        assert any("clinical review" in r.lower() for r in res.review_reasons)
