"""
audit/run_check6.py
===================
Independent QA Audit: CHECK 6 - Knowledge Base Quality
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEED = 42
random.seed(SEED)

seed_path = ROOT / "data" / "seed_drugs.json"
with open(seed_path, encoding="utf-8-sig") as f:
    drugs = json.load(f)

print("=" * 80)
print(f"CHECK 6: KNOWLEDGE BASE QUALITY AUDIT (Sample 40 entries, Random Seed = {SEED})")
print("=" * 80)

# Sample 40 entries
sample_indices = random.sample(range(len(drugs)), 40)
sample = [drugs[i] for i in sorted(sample_indices)]

sample_errors = []
mvp_review_list = []

mvp_classes = {"analgesic_nsaid", "antacid_ppi", "antibiotic", "antihistamine"}

# Check each of the 40 sampled entries
for idx, entry in enumerate(sample, 1):
    gen = entry.get("generic", "").strip()
    cls = entry.get("class", "").strip()
    purpose = entry.get("purpose_plain", "").strip()

    # Rule checks:
    # 1. One sentence (ends with period, no internal periods except decimals)
    # Check if multiple sentences
    sentences = re.split(r"(?<=[a-z0-9])\.\s+[A-Z]", purpose)
    is_single_sentence = (len(sentences) == 1) and purpose.endswith(".")

    # 2. Check for dosing advice (e.g. 500mg, twice a day, take 2 tablets)
    has_dosing = bool(re.search(r"\b\d+\s*(?:mg|ml|mcg|tablets?|pills?|times)\b", purpose, re.IGNORECASE))

    # 3. Check for alarming language (fatal, death, poison, kill, severe hazard)
    has_alarming = bool(re.search(r"\b(?:fatal|death|kill|lethal|poison|deadly|cancer|malignant)\b", purpose, re.IGNORECASE))

    status = "OK"
    issues = []
    if not is_single_sentence:
        issues.append("not single sentence")
    if has_dosing:
        issues.append("contains dosing advice")
    if has_alarming:
        issues.append("alarming tone")

    if issues:
        status = "FLAG: " + ", ".join(issues)
        sample_errors.append((gen, issues))

    if cls in mvp_classes:
        mvp_review_list.append((gen, cls, purpose))

    print(f"[{idx:02d}] Generic: {gen:25} | Class: {cls:16} | Status: {status}")
    print(f"     Purpose: {purpose}")

print("\n" + "=" * 80)
print(f"SAMPLE ERROR RATE: {len(sample_errors)} / 40 ({100.0 * len(sample_errors) / 40:.1f}%)")
if sample_errors:
    for g, iss in sample_errors:
        print(f"  Error in {g}: {iss}")
else:
    print("  Zero formatting/tone errors in 40 sampled entries.")

# Check for duplicate generics / spelling aliases across ALL seed drugs
print("\n--- Checking for Cross-Spelling Synonym Generics across whole KB ---")
known_synonyms = [
    ("paracetamol", "acetaminophen"),
    ("lignocaine", "lidocaine"),
    ("frusemide", "furosemide"),
    ("salbutamol", "albuterol"),
    ("amoxicillin", "amoxycillin"),
    ("rifampicin", "rifampin"),
    ("adrenaline", "epinephrine"),
    ("noradrenaline", "norepinephrine"),
]

all_generics_lower = {d["generic"].lower().strip() for d in drugs}
found_synonyms = []
for syn1, syn2 in known_synonyms:
    in1 = syn1 in all_generics_lower
    in2 = syn2 in all_generics_lower
    if in1 and in2:
        found_synonyms.append((syn1, syn2))
        print(f"  DUPLICATE GENERICS FOUND: Both '{syn1}' and '{syn2}' exist in seed_drugs.json as separate entries!")

print(f"Total duplicate generic synonym pairs in seed_drugs.json: {len(found_synonyms)}")

# Check indian_brands for brands mapping to two different generics
print("\n--- Checking for Brands Mapping to Two Different Generics ---")
csv_path = ROOT / "data" / "indian_brands.csv"
brand_to_generics = {}
with open(csv_path, encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        b = row.get("brand_name", "").strip().lower()
        g = row.get("generic_name", "").strip().lower()
        if b not in brand_to_generics:
            brand_to_generics[b] = set()
        brand_to_generics[b].add(g)

ambig_brands = {b: gens for b, gens in brand_to_generics.items() if len(gens) > 1}
print(f"Ambiguous brands in indian_brands.csv: {len(ambig_brands)}")

# Review of SOURCES.md
print("\n--- SOURCES.md Audit ---")
sources_md_path = ROOT / "data" / "SOURCES.md"
if sources_md_path.exists():
    sources_text = sources_md_path.read_text(encoding="utf-8")
    print("SOURCES.md contents:\n" + sources_text)
else:
    print("SOURCES.md does not exist!")
