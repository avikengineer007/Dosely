"""
audit/run_check3.py
===================
Independent QA Audit: CHECK 3 - Log Read-Through & Live Leak Test
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main
from dosely.api.prescriptions import _create_access_token
from dosely.logging import setup_secure_logging
from dosely.models import Prescription, Medicine, DoseForm, ImageQuality
from fastapi.testclient import TestClient

# Setup logging with stream capture
log_stream = io.StringIO()
stream_handler = logging.StreamHandler(log_stream)
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(stream_handler)

setup_secure_logging()

client = TestClient(main.app)
token = _create_access_token(data={"sub": "audit-test-user"})
headers = {"Authorization": f"Bearer {token}"}

target_strings = [
    "morphine", "10mg", "10 mg",
    "warfarin", "5mg", "5 mg",
    "john doe", "dr. smith"
]

print("=" * 80)
print("CHECK 3: LOG LEAK TEST UNDER DEBUG LOGGING")
print("=" * 80)

# 1. Drive upload with fake payload
mock_rx = Prescription(
    prescription_id="audit-rx-leak-test",
    image_quality=ImageQuality.GOOD,
    medicines=[
        Medicine(
            name_raw="Morphine 10mg",
            name_generic="morphine",
            strength="10 mg",
            form=DoseForm.TABLET,
            frequency_raw="SOS",
            frequency_normalized="As needed",
            confidence=0.98,
        ),
        Medicine(
            name_raw="Warfarin 5 mg",
            name_generic="warfarin",
            strength="5 mg",
            form=DoseForm.TABLET,
            frequency_raw="OD",
            frequency_normalized="Once daily",
            confidence=0.95,
        ),
    ],
)

with patch("dosely.api.prescriptions.extract", return_value=mock_rx):
    res_upload = client.post(
        "/prescriptions/upload",
        files={"file": ("John_Doe_Morphine_10mg.pdf", io.BytesIO(b"%PDF-1.4 dummy content"), "application/pdf")},
        headers=headers,
    )
    print(f"Upload status: {res_upload.status_code}")
    rx_id = res_upload.json()["id"]

# 2. Drive confirm
confirm_payload = {
    "medicines": [
        {
            "name_raw": "Morphine 10mg",
            "name_generic": "morphine",
            "strength": "10 mg",
            "form": "tablet",
            "frequency_raw": "SOS",
            "duration_days": 3,
            "food_instruction": "after_food",
            "user_accepted": True,
        },
        {
            "name_raw": "Warfarin 5 mg",
            "name_generic": "warfarin",
            "strength": "5 mg",
            "form": "tablet",
            "frequency_raw": "OD",
            "duration_days": 10,
            "food_instruction": "after_food",
            "user_accepted": True,
        }
    ]
}
res_confirm = client.put(f"/prescriptions/{rx_id}/confirm", json=confirm_payload, headers=headers)
print(f"Confirm status: {res_confirm.status_code}")

# 3. Drive schedule
res_sched = client.get(f"/prescriptions/{rx_id}/schedule", headers=headers)
print(f"Schedule status: {res_sched.status_code}")

# 4. Drive dose log / reminder action
dose_payload = {
    "drug_name": "Morphine 10mg",
    "scheduled_time": "08:00",
    "action": "taken",
}
res_dose = client.post(f"/prescriptions/{rx_id}/doses", json=dose_payload, headers=headers)
print(f"Dose log status: {res_dose.status_code}")

# 5. Drive feedback
feedback_payload = {
    "drug_name": "Morphine 10mg",
    "field_name": "strength",
    "issue_type": "incorrect_info",
    "user_comment": "Patient John Doe says dose should be Morphine 10mg not Warfarin 5 mg",
}
res_feed = client.post(f"/prescriptions/{rx_id}/feedback", json=feedback_payload, headers=headers)
print(f"Feedback status: {res_feed.status_code}")

# 6. Forced validation error on upload / confirm
res_val_err = client.put(
    f"/prescriptions/{rx_id}/confirm",
    json={"medicines": [{"invalid_field": "Morphine 10mg John Doe"}]},
    headers=headers,
)
print(f"Validation error status: {res_val_err.status_code}")

# 7. Forced extractor failure
with patch("dosely.api.prescriptions.extract", side_effect=ValueError("Parsing failed for patient John Doe: Morphine 10mg invalid format")):
    res_fail = client.post(
        "/prescriptions/upload",
        files={"file": ("prescription_fail.pdf", io.BytesIO(b"%PDF dummy"), "application/pdf")},
        headers=headers,
    )
    print(f"Extractor failure status: {res_fail.status_code}")

# 8. Drive delete
res_del = client.delete(f"/prescriptions/{rx_id}", headers=headers)
print(f"Delete status: {res_del.status_code}")

# Check captured logs
all_logs = log_stream.getvalue()
print("\n--- Search Captured Logs for Sensitive Strings ---")
found_leaks = []
for s in target_strings:
    if s.lower() in all_logs.lower():
        found_leaks.append(s)
        print(f"  LEAK DETECTED: Found '{s}' in log capture!")
    else:
        print(f"  CLEAN: '{s}' not found in log capture.")

print(f"\nTotal leaks detected in log stream: {len(found_leaks)}")

# 9. Check database tables
repo = main.get_repo()
conn = repo._get_connection()
with conn:
    print("\n--- Checking database schemas for unnecessary plain text storage ---")
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    print(f"Tables in DB: {tables}")
    for t in ["prescriptions", "dose_logs", "feedback_reports"]:
        if t in tables:
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info({t})").fetchall()]
            print(f"Table '{t}' columns: {cols}")
repo._close_conn(conn)
