"""
audit/run_check2.py
===================
Independent QA Audit: CHECK 2 - Combination Products
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import DrugMatcher
from dosely.scheduler import MedicationScheduler
from dosely.scheduler.models import DrugInput

db = DrugDatabase.build()
matcher = DrugMatcher(db)

# 1. Inspect data/indian_brands.csv for combination brands
csv_path = ROOT / "data" / "indian_brands.csv"
multi_in_csv = []
with open(csv_path, encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    for row in reader:
        b = row.get("brand_name", "").strip()
        g = row.get("generic_name", "").strip()
        if "+" in g:
            multi_in_csv.append((b, g))

print(f"Total multi-ingredient rows explicitly in indian_brands.csv: {len(multi_in_csv)}")
for b, g in multi_in_csv:
    res = matcher.match(b)
    print(f"  CSV Brand: '{b}' (raw generic: '{g}') -> Matched: '{res.generic}', Class: '{res.drug_class}', Needs Review: {res.needs_review}")

# 2. Sample 20 well-known combination brands from clinical knowledge
combination_sample = [
    ("Combiflam", "Ibuprofen (400mg) + Paracetamol (325mg)"),
    ("Pan D", "Pantoprazole (40mg) + Domperidone (30mg)"),
    ("Omez D", "Omeprazole (20mg) + Domperidone (10mg)"),
    ("Augmentin", "Amoxicillin (500mg) + Clavulanic Acid (125mg)"),
    ("Clavam", "Amoxicillin (500mg) + Clavulanic Acid (125mg)"),
    ("Ultracet", "Tramadol (37.5mg) + Paracetamol (325mg)"),
    ("Zerodol-P", "Aceclofenac (100mg) + Paracetamol (325mg)"),
    ("Zerodol-SP", "Aceclofenac (100mg) + Paracetamol (325mg) + Serratiopeptidase (15mg)"),
    ("Voveran Plus", "Diclofenac (50mg) + Paracetamol (325mg)"),
    ("Flexon", "Ibuprofen (400mg) + Paracetamol (500mg)"),
    ("Cheston Cold", "Cetirizine (5mg) + Paracetamol (325mg) + Phenylephrine (10mg)"),
    ("Sinarest", "Paracetamol (500mg) + Chlorpheniramine (2mg) + Phenylephrine (10mg)"),
    ("Wikoryl", "Paracetamol (500mg) + Phenylephrine (10mg) + Chlorpheniramine (2mg)"),
    ("Ascoril D", "Dextromethorphan (10mg) + Phenylephrine (5mg) + Chlorpheniramine (2mg)"),
    ("Gelusil", "Aluminium Hydroxide + Magnesium Hydroxide"),
    ("Digene", "Aluminium Hydroxide + Magnesium Hydroxide + Simethicone"),
    ("Duolin", "Levosalbutamol + Ipratropium"),
    ("Seroflo", "Salmeterol + Fluticasone"),
    ("Foracort", "Formoterol + Budesonide"),
    ("Telma-AM", "Telmisartan (40mg) + Amlodipine (5mg)"),
]

print("\n--- Testing 20 Combination Brands ---")
silent_drops = 0
for brand, actives in combination_sample:
    res = matcher.match(brand)
    # Check if flagged for review or dropped
    is_dropped = (res.generic is not None and not res.needs_review and res.match_type in ("exact_brand", "exact_generic"))
    if is_dropped:
        silent_drops += 1
    print(f"Brand: {brand:15} | Actives: {actives:55} | Match: {str(res.generic):20} | Class: {str(res.drug_class):15} | Review: {res.needs_review}")

print(f"\nSilent single-ingredient drops with needs_review=False: {silent_drops} / {len(combination_sample)}")

# 3. Test scheduler with a multi-class combination
# For example, Vimovo (Naproxen + Esomeprazole) or an NSAID + Antacid/PPI
print("\n--- Scheduler with Combination (NSAID + PPI / Antacid) ---")
scheduler = MedicationScheduler()
# If Combiflam was matched as ibuprofen only:
drug_combiflam = DrugInput(name="Combiflam", drug_class="analgesic_nsaid", frequency_raw=None)
timeline = scheduler.schedule([drug_combiflam])
print("Combiflam (Ibuprofen + Paracetamol) scheduled as analgesic_nsaid default:")
for entry in timeline:
    print(f"  Time: {entry.time} | Instruction: {entry.instruction} | Class: {entry.drug_class}")

# What if a drug has NSAID + PPI?
# If mapped to single class, say antacid_ppi vs analgesic_nsaid:
print("\nIf mapped to antacid_ppi:")
timeline_ppi = scheduler.schedule([DrugInput(name="Naproxen-Esomeprazole", drug_class="antacid_ppi", frequency_raw=None)])
for entry in timeline_ppi:
    print(f"  Time: {entry.time} | Instruction: {entry.instruction} | Class: {entry.drug_class}")

print("If mapped to analgesic_nsaid:")
timeline_nsaid = scheduler.schedule([DrugInput(name="Naproxen-Esomeprazole", drug_class="analgesic_nsaid", frequency_raw=None)])
for entry in timeline_nsaid:
    print(f"  Time: {entry.time} | Instruction: {entry.instruction} | Class: {entry.drug_class}")
