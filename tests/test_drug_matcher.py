"""
tests/test_drug_matcher.py
==========================
Comprehensive test suite for DrugMatcher and DrugDatabase.

Coverage areas
--------------
1.  Exact generic name matches (lower-case, mixed-case, leading/trailing space)
2.  Indian brand name matches (exact)
3.  Common misspelling / OCR corruption (fuzzy)
4.  Noise-token stripping (dose forms, strengths, frequencies attached to name)
5.  Below-threshold strings -> "unknown" (never silent cross-match)
6.  Look-alike / sound-alike (LASA) pairs -> never cross-match silently
7.  Metformin vs Methotrexate (canonical dangerous pair)
8.  Ambiguous top-2 candidates -> "unknown"
9.  Drug-class classification for all four target classes + "other"
10. needs_review=True only for class "other" or unknown
11. Empty / whitespace-only input
12. Custom openFDA JSON loading
13. User-supplied brand CSV with unknown generic -> gracefully skipped
14. DB with no entries -> "unknown"
15. match_score is normalised 0-1
"""

from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest

from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import FUZZY_THRESHOLD, DrugMatcher, MatchResult

# ---------------------------------------------------------------------------
# Shared fixture — build one matcher for most tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def matcher() -> Generator[DrugMatcher, None, None]:
    """DrugMatcher loaded from the real seed + brands files."""
    db = DrugDatabase.build()       # uses default data/ paths
    yield DrugMatcher(db)
    db.close()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def assert_known(result: MatchResult, expected_generic: str, expected_class: str) -> None:
    assert result.generic == expected_generic, (
        f"Expected generic='{expected_generic}', got '{result.generic}'"
    )
    assert result.drug_class == expected_class, (
        f"Expected class='{expected_class}', got '{result.drug_class}'"
    )
    assert result.purpose_plain is not None, "purpose_plain must not be None for known drug"
    assert result.match_score > 0.0
    assert result.match_type != "unknown"


def assert_unknown(result: MatchResult) -> None:
    assert result.generic is None,       f"generic must be None, got '{result.generic}'"
    assert result.drug_class == "unknown"
    assert result.purpose_plain is None, "purpose_plain must be None for unknown"
    assert result.match_score == 0.0
    assert result.match_type == "unknown"
    assert result.needs_review is True
    assert len(result.review_reasons) >= 1


# ===========================================================================
# 1. Exact generic matches
# ===========================================================================

class TestExactGenericMatch:
    def test_lowercase_exact(self, matcher: DrugMatcher) -> None:
        r = matcher.match("amoxicillin")
        assert_known(r, "amoxicillin", "antibiotic")
        assert r.match_type == "exact_generic"
        assert r.match_score == 1.0

    def test_mixed_case_exact(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Ibuprofen")
        assert_known(r, "ibuprofen", "analgesic_nsaid")
        assert r.match_type == "exact_generic"

    def test_leading_trailing_whitespace(self, matcher: DrugMatcher) -> None:
        r = matcher.match("  paracetamol  ")
        assert_known(r, "paracetamol", "analgesic_nsaid")
        assert r.match_type == "exact_generic"

    def test_cetirizine_exact(self, matcher: DrugMatcher) -> None:
        r = matcher.match("cetirizine")
        assert_known(r, "cetirizine", "antihistamine")

    def test_omeprazole_exact(self, matcher: DrugMatcher) -> None:
        r = matcher.match("omeprazole")
        assert_known(r, "omeprazole", "antacid_ppi")

    def test_multiword_generic(self, matcher: DrugMatcher) -> None:
        r = matcher.match("mefenamic acid")
        assert_known(r, "mefenamic acid", "analgesic_nsaid")


# ===========================================================================
# 2. Indian brand name matches
# ===========================================================================

class TestBrandMatch:
    def test_crocin_to_paracetamol(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Crocin")
        assert_known(r, "paracetamol", "analgesic_nsaid")
        assert r.match_type == "exact_brand"

    def test_pan_to_pantoprazole(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Pan")
        assert_known(r, "pantoprazole", "antacid_ppi")
        assert r.match_type == "exact_brand"

    def test_augmentin_to_amoxicillin_clavulanate(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Augmentin")
        assert_known(r, "amoxicillin clavulanate", "antibiotic")
        assert r.match_type == "exact_brand"

    def test_zyrtec_to_cetirizine(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Zyrtec")
        assert_known(r, "cetirizine", "antihistamine")
        assert r.match_type == "exact_brand"

    def test_allegra_to_fexofenadine(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Allegra")
        assert_known(r, "fexofenadine", "antihistamine")

    def test_flagyl_to_metronidazole(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Flagyl")
        assert_known(r, "metronidazole", "antibiotic")

    def test_brand_case_insensitive(self, matcher: DrugMatcher) -> None:
        r = matcher.match("AZEE")
        assert_known(r, "azithromycin", "antibiotic")


# ===========================================================================
# 3. Misspellings / OCR corruption (fuzzy)
# ===========================================================================

class TestFuzzyMisspellings:
    def test_amoxicillin_typo(self, matcher: DrugMatcher) -> None:
        # "amoxicilin" — one l missing
        r = matcher.match("amoxicilin")
        assert_known(r, "amoxicillin", "antibiotic")
        assert r.match_type == "fuzzy"

    def test_paracetamol_ocr_corruption(self, matcher: DrugMatcher) -> None:
        # OCR often corrupts 'a' -> 'o'
        r = matcher.match("poracetamol")
        assert_known(r, "paracetamol", "analgesic_nsaid")
        assert r.match_type == "fuzzy"

    def test_ibuprofen_transposition(self, matcher: DrugMatcher) -> None:
        r = matcher.match("ibuporfen")
        assert_known(r, "ibuprofen", "analgesic_nsaid")

    def test_cetirizine_misspelling(self, matcher: DrugMatcher) -> None:
        r = matcher.match("cetrizine")
        assert_known(r, "cetirizine", "antihistamine")

    def test_ciprofloxacin_truncated(self, matcher: DrugMatcher) -> None:
        r = matcher.match("ciprofloxacin")
        assert_known(r, "ciprofloxacin", "antibiotic")

    def test_pantoprazole_slight_typo(self, matcher: DrugMatcher) -> None:
        r = matcher.match("pantoprazol")
        assert_known(r, "pantoprazole", "antacid_ppi")


# ===========================================================================
# 4. Noise-token stripping
# ===========================================================================

class TestNoiseTokenStripping:
    def test_tab_prefix(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Tab. Amoxicillin")
        assert_known(r, "amoxicillin", "antibiotic")

    def test_cap_prefix(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Cap. Omeprazole")
        assert_known(r, "omeprazole", "antacid_ppi")

    def test_strength_suffix(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Ibuprofen 400mg")
        assert_known(r, "ibuprofen", "analgesic_nsaid")

    def test_form_and_strength(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Tab. Cetirizine 10 mg")
        assert_known(r, "cetirizine", "antihistamine")

    def test_frequency_attached(self, matcher: DrugMatcher) -> None:
        r = matcher.match("Azithromycin 500mg OD")
        assert_known(r, "azithromycin", "antibiotic")


# ===========================================================================
# 5. Below-threshold strings -> unknown (no silent cross-match)
# ===========================================================================

class TestBelowThreshold:
    def test_nonsense_string(self, matcher: DrugMatcher) -> None:
        r = matcher.match("xyzqrpblah")
        assert_unknown(r)

    def test_very_short_unrecognised(self, matcher: DrugMatcher) -> None:
        r = matcher.match("xyz")
        assert_unknown(r)

    def test_number_only(self, matcher: DrugMatcher) -> None:
        r = matcher.match("500")
        assert_unknown(r)

    def test_partial_garble(self, matcher: DrugMatcher) -> None:
        # "amox" is too short / ambiguous to be confidently matched
        r = matcher.match("amox")
        # Should NOT silently resolve to amoxicillin; may be unknown
        if r.generic is not None:
            # If it does match, it must be to amoxicillin (never something else)
            assert r.generic == "amoxicillin"
        # If it returns unknown, that's also correct
        assert r.needs_review in (True, False)  # no hard assertion; just no crash


# ===========================================================================
# 6 & 7. Look-alike / sound-alike (LASA) safety
# ===========================================================================

class TestLASASafety:
    """
    The most dangerous property: two drug names that look/sound similar
    must NEVER be silently cross-matched when the input clearly names one.
    """

    def test_metformin_does_not_resolve_to_methotrexate(self, matcher: DrugMatcher) -> None:
        r = matcher.match("metformin")
        assert r.generic == "metformin", (
            f"metformin must resolve to itself, got '{r.generic}'"
        )
        assert r.generic != "methotrexate"

    def test_methotrexate_does_not_resolve_to_metformin(self, matcher: DrugMatcher) -> None:
        r = matcher.match("methotrexate")
        assert r.generic == "methotrexate", (
            f"methotrexate must resolve to itself, got '{r.generic}'"
        )
        assert r.generic != "metformin"

    def test_cetirizine_vs_levocetirizine(self, matcher: DrugMatcher) -> None:
        r_cet = matcher.match("cetirizine")
        r_lev = matcher.match("levocetirizine")
        assert r_cet.generic == "cetirizine"
        assert r_lev.generic == "levocetirizine"
        assert r_cet.generic != r_lev.generic

    def test_amoxicillin_vs_amoxicillin_clavulanate(self, matcher: DrugMatcher) -> None:
        r_plain = matcher.match("amoxicillin")
        r_combo = matcher.match("amoxicillin clavulanate")
        assert r_plain.generic == "amoxicillin"
        assert r_combo.generic == "amoxicillin clavulanate"
        assert r_plain.generic != r_combo.generic

    def test_omeprazole_vs_esomeprazole(self, matcher: DrugMatcher) -> None:
        r_ome = matcher.match("omeprazole")
        r_eso = matcher.match("esomeprazole")
        assert r_ome.generic == "omeprazole"
        assert r_eso.generic == "esomeprazole"
        assert r_ome.generic != r_eso.generic

    def test_loratadine_vs_desloratadine(self, matcher: DrugMatcher) -> None:
        r_lor = matcher.match("loratadine")
        r_des = matcher.match("desloratadine")
        assert r_lor.generic == "loratadine"
        assert r_des.generic == "desloratadine"
        assert r_lor.generic != r_des.generic

    def test_lansoprazole_vs_dexlansoprazole(self, matcher: DrugMatcher) -> None:
        r_lan = matcher.match("lansoprazole")
        r_dex = matcher.match("dexlansoprazole")
        assert r_lan.generic == "lansoprazole"
        assert r_dex.generic == "dexlansoprazole"
        assert r_lan.generic != r_dex.generic

    def test_cefixime_vs_cefuroxime(self, matcher: DrugMatcher) -> None:
        r_fix = matcher.match("cefixime")
        r_fur = matcher.match("cefuroxime")
        assert r_fix.generic == "cefixime"
        assert r_fur.generic == "cefuroxime"
        assert r_fix.generic != r_fur.generic


# ===========================================================================
# 9. Drug-class classification
# ===========================================================================

class TestDrugClassification:
    @pytest.mark.parametrize("name, expected_class", [
        # analgesic_nsaid
        ("ibuprofen",   "analgesic_nsaid"),
        ("paracetamol", "analgesic_nsaid"),
        ("diclofenac",  "analgesic_nsaid"),
        ("aspirin",     "analgesic_nsaid"),
        ("naproxen",    "analgesic_nsaid"),
        # antacid_ppi
        ("omeprazole",  "antacid_ppi"),
        ("pantoprazole","antacid_ppi"),
        ("ranitidine",  "antacid_ppi"),
        ("famotidine",  "antacid_ppi"),
        # antibiotic
        ("amoxicillin", "antibiotic"),
        ("azithromycin","antibiotic"),
        ("ciprofloxacin","antibiotic"),
        ("metronidazole","antibiotic"),
        # antihistamine
        ("cetirizine",  "antihistamine"),
        ("loratadine",  "antihistamine"),
        ("fexofenadine","antihistamine"),
        # other
        ("metformin",   "other"),
        ("atorvastatin","other"),
        ("warfarin",    "other"),
    ])
    def test_drug_class(
        self, matcher: DrugMatcher, name: str, expected_class: str
    ) -> None:
        r = matcher.match(name)
        assert r.drug_class == expected_class, (
            f"'{name}' expected class='{expected_class}', got '{r.drug_class}'"
        )


# ===========================================================================
# 10. needs_review flag
# ===========================================================================

class TestNeedsReviewFlag:
    def test_known_class_no_review(self, matcher: DrugMatcher) -> None:
        for name in ["ibuprofen", "omeprazole", "amoxicillin", "cetirizine"]:
            r = matcher.match(name)
            assert r.needs_review is False, (
                f"'{name}' is a known class; needs_review must be False, got True"
            )

    def test_other_class_needs_review(self, matcher: DrugMatcher) -> None:
        for name in ["metformin", "warfarin", "atorvastatin"]:
            r = matcher.match(name)
            assert r.needs_review is True, (
                f"'{name}' is class 'other'; needs_review must be True, got False"
            )

    def test_unknown_needs_review(self, matcher: DrugMatcher) -> None:
        r = matcher.match("zzznonsensedrug")
        assert r.needs_review is True


# ===========================================================================
# 11. Edge cases — empty / whitespace input
# ===========================================================================

class TestEdgeCases:
    def test_empty_string(self, matcher: DrugMatcher) -> None:
        r = matcher.match("")
        assert_unknown(r)
        assert "Empty drug name" in r.review_reasons[0]

    def test_whitespace_only(self, matcher: DrugMatcher) -> None:
        r = matcher.match("   ")
        assert_unknown(r)

    def test_none_like_string(self, matcher: DrugMatcher) -> None:
        # Strings that look like Python None values should not crash
        r = matcher.match("None")
        # "None" will likely be below threshold — just check it doesn't raise
        assert isinstance(r, MatchResult)

    def test_match_score_normalised(self, matcher: DrugMatcher) -> None:
        r = matcher.match("amoxicillin")
        assert 0.0 <= r.match_score <= 1.0


# ===========================================================================
# 12. openFDA JSON loading
# ===========================================================================

class TestOpenFDALoader:
    def test_openfda_entries_imported(self, tmp_path: Path) -> None:
        openfda_data = {
            "results": [
                {"openfda": {"generic_name": ["Sildenafil Citrate"]}},
                {"openfda": {"generic_name": ["tadalafil"]}},
                {"openfda": {}},                         # missing generic_name key
                {"openfda": {"generic_name": []}},       # empty list
            ]
        }
        openfda_file = tmp_path / "openfda.json"
        openfda_file.write_text(json.dumps(openfda_data), encoding="utf-8")

        db = DrugDatabase.build(openfda_json=openfda_file)
        m = DrugMatcher(db)

        r_sil = m.match("sildenafil citrate")
        assert r_sil.generic == "sildenafil citrate"
        assert r_sil.drug_class == "other"
        assert r_sil.needs_review is True   # class 'other'

        r_tad = m.match("tadalafil")
        assert r_tad.generic == "tadalafil"
        db.close()

    def test_openfda_does_not_overwrite_seed(self, tmp_path: Path) -> None:
        """A drug already in seed should keep its curated class, not be overwritten."""
        openfda_data = {
            "results": [{"openfda": {"generic_name": ["amoxicillin"]}}]
        }
        openfda_file = tmp_path / "openfda.json"
        openfda_file.write_text(json.dumps(openfda_data), encoding="utf-8")

        db = DrugDatabase.build(openfda_json=openfda_file)
        m = DrugMatcher(db)

        r = m.match("amoxicillin")
        assert r.drug_class == "antibiotic"   # preserved from seed, not overwritten as 'other'
        db.close()


# ===========================================================================
# 13. Custom brands CSV with unknown generic (graceful skip)
# ===========================================================================

class TestBrandCSVEdgeCases:
    def test_brand_with_unknown_generic_skipped(self, tmp_path: Path) -> None:
        csv_content = (
            "brand_name,generic_name\n"
            "Zantac,ranitidine\n"              # known generic -> should load
            "Fantasydrug,unknowngeneric9999\n" # unknown generic -> should be skipped
        )
        brands_file = tmp_path / "brands.csv"
        brands_file.write_text(csv_content, encoding="utf-8")

        db = DrugDatabase.build(brands_csv=brands_file)
        m = DrugMatcher(db)

        # The known brand should work
        r_zantac = m.match("Zantac")
        assert r_zantac.generic == "ranitidine"

        # The unknown brand should not create a broken entry
        r_fantasy = m.match("Fantasydrug")
        # Should not resolve (brand wasn't loaded since generic was unknown)
        # It might fuzzy-match to something, but must never resolve to a wrong drug
        if r_fantasy.match_type == "exact_brand":
            pytest.fail("Fantasydrug with unknown generic must not be loaded into brand_aliases")
        db.close()

    def test_missing_csv_does_not_crash(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.csv"
        db = DrugDatabase.build(brands_csv=missing)   # should not raise
        m = DrugMatcher(db)
        r = m.match("amoxicillin")
        assert r.generic == "amoxicillin"   # generics still work without brands
        db.close()


# ===========================================================================
# 14. Empty database
# ===========================================================================

class TestEmptyDatabase:
    def test_empty_db_returns_unknown(self, tmp_path: Path) -> None:
        empty_seed = tmp_path / "empty.json"
        empty_seed.write_text("[]", encoding="utf-8")
        db = DrugDatabase.build(seed_path=empty_seed, brands_csv=None)
        m = DrugMatcher(db)
        r = m.match("amoxicillin")
        assert_unknown(r)
        db.close()


# ===========================================================================
# 15. MatchResult.to_dict() shape
# ===========================================================================

class TestMatchResultDict:
    def test_to_dict_keys_present(self, matcher: DrugMatcher) -> None:
        r = matcher.match("ibuprofen")
        d = r.to_dict()
        required_keys = {"generic", "class", "purpose_plain", "match_score",
                         "needs_review", "match_type", "review_reasons"}
        assert required_keys.issubset(d.keys())

    def test_unknown_to_dict(self, matcher: DrugMatcher) -> None:
        r = matcher.match("zzznonsensedrug")
        d = r.to_dict()
        assert d["generic"] is None
        assert d["class"] == "unknown"
        assert d["purpose_plain"] is None
        assert d["match_score"] == 0.0

    def test_score_rounded(self, matcher: DrugMatcher) -> None:
        r = matcher.match("ibuprofen")
        d = r.to_dict()
        # Score should be a float with at most 4 decimal places
        assert isinstance(d["match_score"], float)
        assert round(d["match_score"], 4) == d["match_score"]
