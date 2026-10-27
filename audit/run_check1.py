"""
audit/run_check1.py
===================
Independent QA Audit: CHECK 1
Evaluates hold-out look-alike/sound-alike names, OOV drugs, OCR corruptions,
typos, non-drug inputs, and exact match resolution.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import DrugMatcher

# Load DB and existing LASA pairs to verify exclusion
db = DrugDatabase.build()
matcher = DrugMatcher(db)

with open(ROOT / "data" / "lasa_pairs.json", "r", encoding="utf-8") as f:
    existing_lasa_raw = json.load(f)
existing_lasa_set = set()
for p in existing_lasa_raw:
    existing_lasa_set.add((p[0].lower().strip(), p[1].lower().strip()))
    existing_lasa_set.add((p[1].lower().strip(), p[0].lower().strip()))

with open(ROOT / "data" / "seed_drugs.json", "r", encoding="utf-8-sig") as f:
    seed_drugs = json.load(f)
seed_generics_set = {d["generic"].lower().strip() for d in seed_drugs}

# --- Group A: 20 LASA candidate names NOT in data/lasa_pairs.json ---
candidate_pairs = [
    ("ciprofloxacin", "ofloxacin"),
    ("carvedilol", "captopril"),
    ("losartan", "valsartan"),
    ("labetalol", "lamotrigine"),
    ("zolpidem", "zopiclone"),
    ("acyclovir", "ganciclovir"),
    ("spironolactone", "eplerenone"),
    ("torsemide", "furosemide"),
    ("clopidogrel", "ticagrelor"),
    ("atorvastatin", "rosuvastatin"),
    ("simvastatin", "pravastatin"),
    ("diltiazem", "verapamil"),
    ("levofloxacin", "moxifloxacin"),
    ("duloxetine", "fluoxetine"),
    ("venlafaxine", "desvenlafaxine"),
    ("sumatriptan", "rizatriptan"),
    ("montelukast", "zafirlukast"),
    ("finasteride", "dutasteride"),
    ("alendronate", "risedronate"),
    ("buprenorphine", "naloxone"),
]

group_a = []
for d1, d2 in candidate_pairs:
    if (d1.lower(), d2.lower()) not in existing_lasa_set:
        group_a.append(d1)
        group_a.append(d2)
# Keep first 20 distinct names
group_a = list(dict.fromkeys(group_a))[:20]

# --- Group B: 15 real drugs absent from data/seed_drugs.json ---
candidate_absent = [
    "darolutamide", "enfortumab", "tirzepatide", "somatropin", "baricitinib",
    "upadacitinib", "larotrectinib", "mobocertinib", "capivasertib", "tebentafusp",
    "adagrasib", "futibatinib", "elacestrant", "lecanemab", "donanemab",
    "mavacamten", "pegcetacoplan", "olaparib"
]
group_b = [d for d in candidate_absent if d.lower() not in seed_generics_set][:15]

# --- Group C: 10 misspellings / OCR corruptions of known drugs ---
group_c = [
    "paracetarnol",    # rn for m in paracetamol
    "arnoxicillin",    # rn for m in amoxicillin
    "ibupr0fen",       # 0 for o in ibuprofen
    "cet1rizine",      # 1 for i/l in cetirizine
    "orneprazole",     # rn for m in omeprazole
    "pantoprazolee",   # doubled letter e
    "paractamol",      # dropped letter e
    "ciprofloxcin",    # dropped letter a
    "metforrnin",      # rn for m in metformin
    "azithr0mycin",    # 0 for o in azithromycin
]

# --- Group D: 5 brand typos + 5 casing/spacing ---
group_d = [
    # 5 typos
    "Crocin Pluss",
    "Augmentinn",
    "Calpool",
    "Dolo 655",
    "Pann 40",
    # 5 casing / spacing
    "PANTOP 40",
    "pan-top",
    "C R O C I N",
    "dolo   650",
    "AUGMENTIN-625",
]

# --- Group E: 5 non-drug strings ---
group_e = [
    "Dr. Rajesh Sharma MD",
    "take with water twice daily",
    "",
    "A" * 1500,
    "SELECT * FROM prescriptions WHERE 1=1; -- DROP TABLE users;",
]

all_probes = [
    ("Group A (Hold-out LASA)", group_a),
    ("Group B (Absent Generics)", group_b),
    ("Group C (OCR & Misspellings)", group_c),
    ("Group D (Brand Typos & Format)", group_d),
    ("Group E (Non-drug strings)", group_e),
]

print("=" * 80)
print("CHECK 1: RUNNING HOLD-OUT LOOK-ALIKE & OOV PROBES")
print("=" * 80)

total_probes = 0
wrong_guesses = 0
violations = []
table_rows = []

for grp_name, probe_list in all_probes:
    print(f"\n--- {grp_name} ({len(probe_list)} inputs) ---")
    for inp in probe_list:
        total_probes += 1
        display_inp = inp if len(inp) < 30 else inp[:27] + "..."
        display_inp = repr(display_inp)
        try:
            res = matcher.match(inp)
            nr = res.needs_review
            gen = res.generic
            sugg = res.suggested_generic
            lasa = bool(res.lasa_warning)
            mtype = res.match_type

            # Safety rules verification:
            # 1. Non-exact input must NOT return needs_review=False
            is_exact = (mtype in ("exact_generic", "exact_brand"))

            if not is_exact:
                if not nr:
                    violations.append(f"{inp}: non-exact returned needs_review=False")
                    wrong_guesses += 1
                if gen is not None:
                    violations.append(f"{inp}: non-exact populated confirmed generic '{gen}'")
                    wrong_guesses += 1
                if grp_name == "Group E (Non-drug strings)" and sugg is not None:
                    violations.append(f"{inp}: non-drug string produced suggestion '{sugg}'")

            table_rows.append((inp, nr, gen, sugg, lasa, mtype))
            print(f"Input: {display_inp:30} | Review: {str(nr):5} | Generic: {str(gen):15} | Sugg: {str(sugg):15} | LASA: {str(lasa):5} | Type: {mtype}")
        except Exception as exc:
            violations.append(f"{inp}: crashed with {exc}")
            print(f"Input: {display_inp:30} | CRASHED: {exc}")

print("\n" + "=" * 80)
print(f"TOTAL AUDIT PROBES TESTED: {total_probes}")
print(f"TOTAL WRONG GUESSES: {wrong_guesses}")
print(f"WRONG-GUESS RATE: {100.0 * wrong_guesses / total_probes:.2f}%")
print(f"VIOLATIONS COUNT: {len(violations)}")
if violations:
    for v in violations:
        print(f"  VIOLATION: {v}")
else:
    print("  Zero violations on non-exact inputs.")
print("=" * 80)

# --- Exact match path test on 30 common drugs ---
print("\n--- EXACT MATCH PATH TEST (30 correctly spelled common drugs) ---")
common_drugs = [
    # MVP Analgesics
    "paracetamol", "ibuprofen", "aspirin", "diclofenac", "naproxen",
    # MVP Antacids / PPIs
    "omeprazole", "pantoprazole", "rabeprazole", "esomeprazole", "lansoprazole",
    # MVP Antibiotics
    "amoxicillin", "azithromycin", "ciprofloxacin", "metronidazole", "doxycycline",
    "cephalexin", "clarithromycin", "levofloxacin", "cotrimoxazole", "norfloxacin",
    # MVP Antihistamines
    "cetirizine", "loratadine", "fexofenadine", "desloratadine", "chlorpheniramine",
    # Common Brand Aliases
    "crocin", "dolo", "pan", "augmentin", "calpol"
]

false_flags = 0
for cd in common_drugs:
    res = matcher.match(cd)
    is_exact = res.match_type in ("exact_generic", "exact_brand")
    if not is_exact or res.needs_review or res.generic is None:
        false_flags += 1
        print(f"  FALSE FLAG on '{cd}': match_type={res.match_type}, needs_review={res.needs_review}, generic={res.generic}")
    else:
        print(f"  OK exact '{cd}' -> generic='{res.generic}', class='{res.drug_class}', needs_review=False")

false_flag_rate = 100.0 * false_flags / len(common_drugs)
print(f"\nFalse-flag count: {false_flags}/{len(common_drugs)} (False-flag rate: {false_flag_rate:.1f}%)")
