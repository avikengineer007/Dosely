"""
Task 1 (follow-up audit): combination products must never silently lose ingredients.

Reproduces audit Check 2: Combiflam / Pan D / Ultracet / Gelusil / Digene used to
resolve to ONE ingredient with needs_review=False because the loader did
``generic_raw.split("+")[0]``.
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import DrugMatcher
from dosely.scheduler import MedicationScheduler
from dosely.scheduler.models import DrugInput

DATA = Path(__file__).resolve().parent.parent / "data"
MVP = {"analgesic_nsaid", "antacid_ppi", "antibiotic", "antihistamine"}


@pytest.fixture(scope="module")
def db() -> DrugDatabase:
    # strict=True: any unresolved ingredient in the real CSV must fail the build.
    return DrugDatabase.build(strict=True)


@pytest.fixture(scope="module")
def matcher(db) -> DrugMatcher:
    return DrugMatcher(db)


def _names(result) -> list[str]:
    return [i["generic"] for i in result.ingredients]


# (brand, ingredients in order, expected needs_review or None = only check consistency)
COMBOS = [
    ("Combiflam",        ["ibuprofen", "paracetamol"], False),
    ("Flexon",           ["ibuprofen", "paracetamol"], False),
    ("Pan D",            ["pantoprazole", "domperidone"], True),
    ("Omez D",           ["omeprazole", "domperidone"], True),
    ("Pantocid DSR",     ["pantoprazole", "domperidone"], True),
    ("Nexpro RD",        ["esomeprazole", "domperidone"], True),
    ("Razo-D",           ["rabeprazole", "domperidone"], True),
    ("Ultracet",         ["tramadol", "paracetamol"], None),
    ("Zerodol-P",        ["aceclofenac", "paracetamol"], False),
    ("Hifenac-P",        ["aceclofenac", "paracetamol"], False),
    ("Zerodol-SP",       ["aceclofenac", "paracetamol", "serratiopeptidase"], True),
    ("Augmentin",        ["amoxicillin", "clavulanic acid"], False),
    ("Clavam",           ["amoxicillin", "clavulanic acid"], False),
    ("Moxikind-CV",      ["amoxicillin", "clavulanic acid"], False),
    ("Septra",           ["trimethoprim", "sulfamethoxazole"], False),
    ("Bactrim",          ["trimethoprim", "sulfamethoxazole"], False),
    ("Cotrimoxazole",    ["trimethoprim", "sulfamethoxazole"], False),
    ("Co-trimoxazole",   ["trimethoprim", "sulfamethoxazole"], False),
    ("Gelusil",          ["aluminium hydroxide", "magnesium hydroxide"], False),
    ("Digene",           ["aluminium hydroxide", "magnesium hydroxide"], False),
    ("Sinarest",         ["paracetamol", "chlorpheniramine", "phenylephrine"], True),
    ("Cheston Cold",     ["cetirizine", "paracetamol", "phenylephrine"], True),
    ("Action 500 Extra", ["paracetamol", "caffeine"], True),
    ("Duolin",           ["levosalbutamol", "ipratropium"], True),
    ("Seroflo",          ["salmeterol", "fluticasone"], True),
    ("Foracort",         ["formoterol", "budesonide"], True),
    ("Telma-AM",         ["telmisartan", "amlodipine"], True),
    ("Telma-H",          ["telmisartan", "hydrochlorothiazide"], True),
    ("Oflox-OZ",         ["ofloxacin", "ornidazole"], True),
    ("Ciplox-TZ",        ["ciprofloxacin", "tinidazole"], False),
    ("Allegra-M",        ["fexofenadine", "montelukast"], True),
    ("Montair-LC",       ["montelukast", "levocetirizine"], True),
    ("Glycomet-GP",      ["metformin", "glimepiride"], True),
    ("Amaryl-M",         ["glimepiride", "metformin"], True),
    ("Ecosprin-AV",      ["aspirin", "atorvastatin"], True),
]


def test_at_least_30_combination_brands_under_test():
    assert len(COMBOS) >= 30
    assert sum(1 for _, ings, _ in COMBOS if len(ings) >= 3) >= 2


@pytest.mark.parametrize("brand,expected,expect_review", COMBOS)
def test_combination_keeps_all_ingredients(matcher, brand, expected, expect_review):
    r = matcher.match(brand)
    assert r.match_type == "exact_brand", f"{brand} did not resolve exactly: {r.match_type}"
    assert _names(r) == expected, f"{brand}: ingredients silently changed: {_names(r)}"
    for ing in r.ingredients:
        assert ing["drug_class"] and ing["purpose_plain"]
    # Consistency rule: review iff any ingredient is outside the four MVP classes.
    outside = [i["generic"] for i in r.ingredients if i["drug_class"] not in MVP]
    assert r.needs_review == bool(outside), (
        f"{brand}: needs_review={r.needs_review} but non-MVP ingredients={outside}"
    )
    if expect_review is not None:
        assert r.needs_review is expect_review
    if outside:
        joined = " ".join(r.review_reasons).lower()
        for name in outside:
            assert name in joined, f"{brand}: review note must name '{name}'"
    d = r.to_dict()
    assert d["ingredients"] and len(d["ingredients"]) == len(expected)
    assert d["contains"] == " + ".join(expected)


def test_audit_named_regressions_are_not_single_ingredient(matcher):
    # Exactly the five brands from the audit report.
    for brand in ("Combiflam", "Pan D", "Ultracet", "Gelusil", "Digene"):
        r = matcher.match(brand)
        assert len(r.ingredients) >= 2, f"{brand} still resolves to one ingredient"
        assert "+" in r.to_dict()["contains"]


def test_cotrimoxazole_no_longer_false_flagged(matcher):
    for name in ("cotrimoxazole", "Co-trimoxazole", "COTRIMOXAZOLE"):
        r = matcher.match(name)
        assert r.match_type == "exact_brand"
        assert r.needs_review is False
        assert _names(r) == ["trimethoprim", "sulfamethoxazole"]


def test_legacy_combined_generic_names_still_resolve_with_ingredients(matcher):
    r = matcher.match("amoxicillin clavulanate")
    assert r.generic == "amoxicillin clavulanate"
    assert _names(r) == ["amoxicillin", "clavulanic acid"]


def test_typed_combination_of_exact_generics_resolves_all(matcher):
    for text in ("ibuprofen + paracetamol", "paracetamol / ibuprofen", "ibuprofen and paracetamol"):
        r = matcher.match(text)
        assert r.match_type == "exact_generic"
        assert sorted(_names(r)) == ["ibuprofen", "paracetamol"]


def test_synonym_is_canonicalised_inside_combination(matcher):
    r = matcher.match("acetaminophen + ibuprofen")
    assert sorted(_names(r)) == ["ibuprofen", "paracetamol"]


def test_typed_combination_with_unknown_part_is_flagged_not_dropped(matcher):
    r = matcher.match("paracetamol + zzzunknownzzz")
    assert r.needs_review is True
    assert r.generic is None
    assert r.match_type in ("unknown", "fuzzy_suggestion")
    assert r.ingredients == []


def test_typed_duplicate_parts_are_not_auto_resolved(matcher):
    r = matcher.match("paracetamol + paracetamol")
    assert r.needs_review is True or len(r.ingredients) == 1


# ---------------------------------------------------------------- data / loader

def _csv_rows():
    with open(DATA / "indian_brands.csv", encoding="utf-8-sig", newline="") as fh:
        return [(r["brand_name"].strip(), r["generic_name"].strip()) for r in csv.DictReader(fh)
                if r["brand_name"].strip()]


def test_no_csv_row_loses_an_ingredient(db):
    synonyms = json.loads((DATA / "synonyms.json").read_text(encoding="utf-8"))["synonyms"]
    for brand, raw in _csv_rows():
        # Independent splitter (deliberately not the loader's).
        parts = [p.strip().lower() for p in re.split(r"\+|/|\s+and\s+|\s+with\s+", raw, flags=re.I) if p.strip()]
        expected = [synonyms.get(p, p) for p in parts]
        got = [i["generic"] for i in db.get_brand_ingredients(brand)]
        assert got == expected, f"CSV row '{brand}' lost/changed ingredients: {raw!r} -> {got}"


def test_schema_has_brand_ingredients_and_no_single_generic_alias_table(db):
    tables = {r[0] for r in db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "brand_ingredients" in tables
    assert "brand_aliases" not in tables
    cols = [r[1] for r in db._conn.execute("PRAGMA table_info(brand_ingredients)")]
    for c in ("brand", "ingredient", "position", "strength"):
        assert c in cols
    rows = db._conn.execute(
        "SELECT brand, position FROM brand_ingredients ORDER BY brand, position").fetchall()
    by_brand: dict[str, list[int]] = {}
    for b, p in rows:
        by_brand.setdefault(b, []).append(p)
    assert any(len(v) >= 3 for v in by_brand.values())
    for b, positions in by_brand.items():
        assert positions == list(range(len(positions))), f"{b}: positions not contiguous"


def _tmp_db(tmp_path, csv_text, strict):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps([
        {"generic": "paracetamol", "class": "analgesic_nsaid", "purpose_plain": "Eases pain."},
        {"generic": "ibuprofen", "class": "analgesic_nsaid", "purpose_plain": "Eases pain."},
    ]), encoding="utf-8")
    brands = tmp_path / "brands.csv"
    brands.write_text(csv_text, encoding="utf-8")
    syn = tmp_path / "syn.json"
    syn.write_text(json.dumps({"synonyms": {"acetaminophen": "paracetamol"}, "combinations": {}}),
                   encoding="utf-8")
    return DrugDatabase.build(seed_path=seed, brands_csv=brands, synonyms_path=syn, strict=strict)


def test_unknown_ingredient_fails_loudly_in_strict_build(tmp_path):
    with pytest.raises(ValueError) as exc:
        _tmp_db(tmp_path, "brand_name,generic_name\nFakeCombo,paracetamol + zzzunknownzzz\n", strict=True)
    assert "zzzunknownzzz" in str(exc.value)


def test_non_strict_build_skips_whole_row_never_truncates(tmp_path):
    db = _tmp_db(tmp_path, "brand_name,generic_name\nFakeCombo,paracetamol + zzzunknownzzz\n"
                           "GoodCombo,acetaminophen + ibuprofen\n", strict=False)
    assert db.get_brand_ingredients("FakeCombo") == []            # NOT reduced to paracetamol
    assert [i["generic"] for i in db.get_brand_ingredients("GoodCombo")] == ["paracetamol", "ibuprofen"]


def test_optional_strength_column_is_stored_per_ingredient(tmp_path):
    db = _tmp_db(tmp_path, "brand_name,generic_name,strength\nPair,ibuprofen + paracetamol,400 mg + 325 mg\n",
                 strict=True)
    ings = db.get_brand_ingredients("Pair")
    assert [i["strength"] for i in ings] == ["400 mg", "325 mg"]


def test_strength_count_mismatch_fails_loudly(tmp_path):
    with pytest.raises(ValueError):
        _tmp_db(tmp_path, "brand_name,generic_name,strength\nPair,ibuprofen + paracetamol,400 mg\n", strict=True)


def test_conflicting_duplicate_brand_rows_fail_loudly(tmp_path):
    with pytest.raises(ValueError):
        _tmp_db(tmp_path, "brand_name,generic_name\nX,ibuprofen\nX,paracetamol\n", strict=True)


# ---------------------------------------------------------------- scheduler

def _ing(matcher, brand):
    r = matcher.match(brand)
    return DrugInput(name=r.generic, drug_class=r.drug_class, frequency_raw=None,
                     ingredients=r.ingredients)


def test_combiflam_scheduled_once_with_nsaid_after_food_rule(matcher):
    entries = MedicationScheduler().schedule([_ing(matcher, "Combiflam")])
    assert len(entries) == 2                      # NSAID default = after lunch + after dinner, ONCE
    assert all("after" in e.instruction.lower() for e in entries)
    assert all(e.suggested and e.source == "default" for e in entries)
    d = entries[0].to_dict()
    assert d["ingredients"] == ["ibuprofen", "paracetamol"]


def test_nsaid_plus_ppi_product_uses_most_conservative_after_food_rule():
    drug = DrugInput(
        name="naproxen + esomeprazole", drug_class="antacid_ppi", frequency_raw=None,
        ingredients=[
            {"generic": "naproxen", "drug_class": "analgesic_nsaid", "purpose_plain": "x."},
            {"generic": "esomeprazole", "drug_class": "antacid_ppi", "purpose_plain": "y."},
        ],
    )
    entries = MedicationScheduler().schedule([drug])
    assert len(entries) == 2
    assert all("after" in e.instruction.lower() for e in entries)
    assert "before breakfast" not in " ".join(e.instruction.lower() for e in entries)


def test_pan_d_uses_ppi_rule_once(matcher):
    entries = MedicationScheduler().schedule([_ing(matcher, "Pan D")])
    assert len(entries) == 1
    assert "before breakfast" in entries[0].instruction.lower()
    assert entries[0].to_dict()["ingredients"] == ["pantoprazole", "domperidone"]


def test_combination_with_unresolved_ingredient_gets_no_default_timing():
    drug = DrugInput(
        name="mystery combo", drug_class="unknown", frequency_raw=None,
        ingredients=[
            {"generic": "paracetamol", "drug_class": "analgesic_nsaid", "purpose_plain": "x."},
            {"generic": "zzz", "drug_class": "unknown", "purpose_plain": ""},
        ],
    )
    entries = MedicationScheduler().schedule([drug])
    assert len(entries) == 1 and entries[0].time == "as needed"
    assert "no default timing" in entries[0].instruction.lower()


def test_explicit_frequency_on_combination_is_not_overridden(matcher):
    drug = _ing(matcher, "Combiflam")
    drug.frequency_raw = "TDS"
    entries = MedicationScheduler().schedule([drug])
    assert len(entries) == 3 and all(e.source == "prescription" and not e.suggested for e in entries)
    assert entries[0].to_dict()["ingredients"] == ["ibuprofen", "paracetamol"]


# ---------------------------------------------------------------- conflicts

def _warnings(entries):
    return " || ".join(w for e in entries for w in e.warnings).lower()


def test_combiflam_plus_crocin_warns_duplicate_paracetamol(matcher):
    entries = MedicationScheduler().schedule([_ing(matcher, "Combiflam"), _ing(matcher, "Crocin")])
    w = _warnings(entries)
    assert "duplicate ingredient" in w and "paracetamol" in w


def test_ultracet_plus_paracetamol_product_warns(matcher):
    entries = MedicationScheduler().schedule([_ing(matcher, "Ultracet"), _ing(matcher, "Dolo")])
    assert "duplicate ingredient" in _warnings(entries) and "paracetamol" in _warnings(entries)


def test_two_products_sharing_any_ingredient_warn(matcher):
    entries = MedicationScheduler().schedule([_ing(matcher, "Pan D"), _ing(matcher, "Omez D")])
    w = _warnings(entries)
    assert "duplicate ingredient" in w and "domperidone" in w


def test_synonym_names_are_one_ingredient_for_conflicts():
    a = DrugInput(name="Product A", drug_class="analgesic_nsaid", ingredients=[
        {"generic": "acetaminophen", "drug_class": "analgesic_nsaid", "purpose_plain": "x."}])
    b = DrugInput(name="Product B", drug_class="analgesic_nsaid", ingredients=[
        {"generic": "paracetamol", "drug_class": "analgesic_nsaid", "purpose_plain": "x."},
        {"generic": "caffeine", "drug_class": "other", "purpose_plain": "y."}])
    w = _warnings(MedicationScheduler().schedule([a, b]))
    assert "duplicate ingredient" in w and "paracetamol" in w


def test_single_combination_product_does_not_warn_about_itself(matcher):
    entries = MedicationScheduler().schedule([_ing(matcher, "Combiflam")])
    assert _warnings(entries) == ""


def test_antacid_spacing_is_checked_at_ingredient_level(matcher):
    gelusil = _ing(matcher, "Gelusil")
    combiflam = _ing(matcher, "Combiflam")
    combiflam.frequency_raw = "TDS"
    entries = MedicationScheduler().schedule([gelusil, combiflam])
    w = _warnings(entries)
    assert "antacid interaction" in w


def test_ingredient_level_class_duplicate_for_distinct_products(matcher):
    entries = MedicationScheduler().schedule([_ing(matcher, "Combiflam"), _ing(matcher, "Voveran")])
    assert "duplicate drug class" in _warnings(entries)


# ---------------------------------------------------------------- API end to end

@pytest.fixture(scope="module")
def client():
    import main
    return TestClient(main.app)


def _token(sub):
    from dosely.api.prescriptions import _create_access_token
    return {"Authorization": f"Bearer {_create_access_token(data={'sub': sub})}"}


def _upload(client, headers, names):
    from dosely.models import DoseForm, ImageQuality, Medicine, Prescription
    rx = Prescription(prescription_id="rx-combo", image_quality=ImageQuality.GOOD, medicines=[
        Medicine(name_raw=n, confidence=0.95, strength="1 tab", form=DoseForm.TABLET,
                 frequency_raw="BD", frequency_normalized="Twice daily", duration_days=3) for n in names])
    with patch("dosely.api.prescriptions.extract", return_value=rx):
        r = client.post("/prescriptions/upload",
                        files={"file": ("a.pdf", io.BytesIO(b"%PDF-1.4 x"), "application/pdf")},
                        headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_upload_response_lists_every_ingredient_of_a_combination(client):
    body = _upload(client, _token("combo-u1"), ["Combiflam"])
    med = body["prescription"]["medicines"][0]
    assert [i["generic"] for i in med["ingredients"]] == ["ibuprofen", "paracetamol"]


def test_confirm_and_schedule_carry_ingredients_and_duplicate_warning(client):
    h = _token("combo-u2")
    body = _upload(client, h, ["Combiflam", "Crocin"])
    payload = {"medicines": [
        {"name_raw": "Combiflam", "strength": "1 tab", "form": "tablet", "frequency_raw": "BD",
         "duration_days": 3, "food_instruction": "after_food", "user_accepted": True},
        {"name_raw": "Crocin", "strength": "1 tab", "form": "tablet", "frequency_raw": "BD",
         "duration_days": 3, "food_instruction": "after_food", "user_accepted": True},
    ]}
    c = client.put(f"/prescriptions/{body['id']}/confirm", json=payload, headers=h)
    assert c.status_code == 200, c.text
    matched = c.json()["matched_drugs"]
    assert [i["generic"] for i in matched[0]["matching"]["ingredients"]] == ["ibuprofen", "paracetamol"]
    s = client.get(f"/prescriptions/{body['id']}/schedule", headers=h).json()["schedule"]
    assert len(s) == 4                                    # 2 products x BD, once each
    assert any(e.get("ingredients") == ["ibuprofen", "paracetamol"] for e in s)
    assert "duplicate ingredient" in " ".join(w for e in s for w in e["warnings"]).lower()


# ---------------------------------------------------------------- UI

def test_confirm_screen_shows_contains_line_and_forces_combination_acceptance():
    src = (Path(__file__).resolve().parent.parent / "frontend" / "src" / "ConfirmScreen.jsx").read_text(encoding="utf-8")
    assert "Contains:" in src
    assert "ingredients" in src
    assert "ingredients.length > 1" in src
