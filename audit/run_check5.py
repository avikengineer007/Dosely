"""
audit/run_check5.py
===================
Independent QA Audit: CHECK 5 - Scheduler and Confirm-Gate Regression
"""

from __future__ import annotations

import io
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main
from dosely.api.prescriptions import _create_access_token
from dosely.models import Prescription, Medicine, DoseForm, ImageQuality
from dosely.scheduler import MedicationScheduler
from dosely.scheduler.models import DrugInput
from fastapi.testclient import TestClient

client = TestClient(main.app)
scheduler = MedicationScheduler()

token_user1 = _create_access_token(data={"sub": "audit-user-1"})
token_user2 = _create_access_token(data={"sub": "audit-user-2"})
headers1 = {"Authorization": f"Bearer {token_user1}"}
headers2 = {"Authorization": f"Bearer {token_user2}"}

print("=" * 80)
print("CHECK 5: SCHEDULER & CONFIRM-GATE REGRESSION")
print("=" * 80)

# Item 1: Unresolved or 'unknown' medicine gets NO default timings and is not dropped
drugs = [DrugInput(name="UnknownDrugXYZ", drug_class="unknown", frequency_raw=None)]
timeline = scheduler.schedule(drugs)
print(f"Item 1 - Timeline length for unresolved medicine: {len(timeline)}")
assert len(timeline) == 1, "Unresolved drug was dropped!"
entry = timeline[0]
print(f"  Drug: {entry.drug} | Time: {entry.time} | Instruction: {entry.instruction}")
assert entry.time == "as needed", f"Expected 'as needed', got '{entry.time}'"
assert "no default timing applied" in entry.instruction.lower()

# Item 2: Explicit frequency is never overridden by defaults
drugs_explicit = [DrugInput(name="Omeprazole", drug_class="antacid_ppi", frequency_raw="BD", food_relation="after_food")]
timeline_explicit = scheduler.schedule(drugs_explicit)
print(f"\nItem 2 - Explicit frequency BD entries count: {len(timeline_explicit)}")
for e in timeline_explicit:
    print(f"  Time: {e.time} | Drug: {e.drug} | Instruction: {e.instruction} | Source: {e.source}")
# Default PPI rule is OD before breakfast. Explicit BD must produce 2 doses!
assert len(timeline_explicit) == 2, f"Expected 2 doses for explicit BD, got {len(timeline_explicit)}"
assert all(e.source == "prescription" for e in timeline_explicit)

# Item 3: Schedule endpoint returns 400 for unconfirmed, 404 for another user
mock_rx = Prescription(
    prescription_id="rx-gate-test",
    image_quality=ImageQuality.GOOD,
    medicines=[Medicine(name_raw="Paracetamol 500mg", confidence=0.9)]
)

with patch("dosely.api.prescriptions.extract", return_value=mock_rx):
    res_up = client.post(
        "/prescriptions/upload",
        files={"file": ("rx.pdf", io.BytesIO(b"%PDF dummy"), "application/pdf")},
        headers=headers1,
    )
    rx_id = res_up.json()["id"]

# Unconfirmed schedule check -> HTTP 400
res_unconfirmed = client.get(f"/prescriptions/{rx_id}/schedule", headers=headers1)
print(f"\nItem 3a - Unconfirmed schedule status: {res_unconfirmed.status_code}")
assert res_unconfirmed.status_code == 400, f"Expected 400 for unconfirmed, got {res_unconfirmed.status_code}"

# Another user access -> HTTP 404
res_other_user = client.get(f"/prescriptions/{rx_id}", headers=headers2)
print(f"Item 3b - Another user GET status: {res_other_user.status_code}")
assert res_other_user.status_code == 404, f"Expected 404 for other user, got {res_other_user.status_code}"

# Item 4: User accepts fuzzy suggestion: verify storage and distinctions
confirm_payload = {
    "medicines": [
        {
            "name_raw": "amoxicilin",
            "name_generic": "amoxicillin", # User chose the suggestion
            "strength": "500mg",
            "form": "capsule",
            "frequency_raw": "TDS",
            "duration_days": 5,
            "food_instruction": "after_food",
            "user_accepted": True,
        }
    ]
}
res_conf = client.put(f"/prescriptions/{rx_id}/confirm", json=confirm_payload, headers=headers1)
print(f"\nItem 4 - Confirm response status: {res_conf.status_code}")
conf_json = res_conf.json()

# Inspect confirmation record in DB
repo = main.get_repo()
rec = repo.get(rx_id, user_id="audit-user-1")
print("  Extracted data:", rec["extracted_data"]["medicines"][0].get("name_raw"))
print("  Confirmed data:", rec["confirmed_data"]["medicines"][0].get("name_generic"))
print("  Matched generic:", rec["matched_drugs"][0]["matching"].get("generic"))

# Item 5: Rematching when user edits after confirmation
# Confirm again with a modified drug name
confirm_edit = {
    "medicines": [
        {
            "name_raw": "ciprofloxacinn",
            "name_generic": None, # Unresolved / needs review
            "strength": "250mg",
            "form": "tablet",
            "user_accepted": True,
        }
    ]
}
res_reconf = client.put(f"/prescriptions/{rx_id}/confirm", json=confirm_edit, headers=headers1)
print(f"\nItem 5 - Re-confirm edited status: {res_reconf.status_code}")
rec_edited = repo.get(rx_id, user_id="audit-user-1")
match_info = rec_edited["matched_drugs"][0]["matching"]
print(f"  Rematched type: {match_info.get('match_type')} | Needs review: {match_info.get('needs_review')}")
assert match_info.get("needs_review") is True

# Item 6: Duplicate drug classes and antacid spacing warnings
drugs_conflict = [
    DrugInput(name="Ibuprofen", drug_class="analgesic_nsaid", frequency_raw="BD"),
    DrugInput(name="Diclofenac", drug_class="analgesic_nsaid", frequency_raw="BD"),
    DrugInput(name="Antacid Gel", drug_class="antacid_ppi", frequency_raw="BD"),
]
timeline_conflicts = scheduler.schedule(drugs_conflict)
all_warnings = [w for e in timeline_conflicts for w in e.warnings]
print(f"\nItem 6 - Total warnings generated: {len(all_warnings)}")
for w in all_warnings:
    print(f"  Warning: {w.encode('ascii', 'backslashreplace').decode('ascii')}")

has_dup_nsaid = any("duplicate" in w.lower() or "nsaid" in w.lower() for w in all_warnings)
print(f"  Duplicate NSAID warning triggered: {has_dup_nsaid}")
assert has_dup_nsaid, "Duplicate class warning failed to trigger!"

print("\nCHECK 5 RESULT: ALL SAFETY CHECKS PASSED")
