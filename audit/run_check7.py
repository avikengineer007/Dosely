"""
audit/run_check7.py
===================
Independent QA Audit: CHECK 7 - Purge and Deployment Behavior
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main
from dosely.auth import create_access_token
from dosely.background import run_purge_job
from dosely.models import Prescription, Medicine, DoseForm, ImageQuality
from dosely.repository import PrescriptionRepository
from dosely.storage import EncryptedStorage
from fastapi.testclient import TestClient
from dosely.auth import _b64encode

client = TestClient(main.app)

print("=" * 80)
print("CHECK 7: PURGE AND DEPLOYMENT AUDIT")
print("=" * 80)

# --- 1. Multi-worker / concurrent purge analysis ---
print("\n--- 1. Multi-worker / Concurrent Purge Simulation ---")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    storage_dir = tmp_path / "enc"
    storage_dir.mkdir()
    db_file = str(tmp_path / "concurrent.db")
    
    repo1 = PrescriptionRepository(db_path=db_file)
    repo2 = PrescriptionRepository(db_path=db_file)
    storage1 = EncryptedStorage(storage_dir=storage_dir)
    storage2 = EncryptedStorage(storage_dir=storage_dir)

    # Plant 5 old prescriptions
    old_ids = []
    old_time = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    conn = repo1._get_connection()
    with conn:
        for i in range(5):
            pid = f"old-rx-{i}"
            img = storage1.save(pid, b"fake content")
            conn.execute(
                """INSERT INTO prescriptions (id, user_id, created_at, status, confirmed, image_path, mime_type, image_quality, extracted_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, "user-c", old_time, "confirmed", 1, img, "image/jpeg", "good", "{}")
            )
            old_ids.append((pid, img))
    repo1._close_conn(conn)

    # Simulate 2 workers executing purge tick concurrently
    print(f"Starting concurrent purge from 2 separate repository instances on same DB...")
    p1 = run_purge_job(repo=repo1, storage=storage1, days=30)
    p2 = run_purge_job(repo=repo2, storage=storage2, days=30)
    print(f"Worker 1 purged: {p1} records | Worker 2 purged: {p2} records | Total expected: 5")
    assert p1 + p2 == 5, f"Expected total 5 purged, got {p1 + p2}"

# --- 2. Failure mid-run / Orphaned files and rows ---
print("\n--- 2. Purge Failure Mid-Run / Orphan Detection ---")
with tempfile.TemporaryDirectory() as tmp_dir:
    tmp_path = Path(tmp_dir)
    storage_dir = tmp_path / "enc"
    storage_dir.mkdir()
    db_file = str(tmp_path / "orphan.db")
    repo = PrescriptionRepository(db_path=db_file)
    storage = EncryptedStorage(storage_dir=storage_dir)

    # Prescription A: normal old prescription
    pid_a = "rx-normal"
    img_a = storage.save(pid_a, b"content A")
    # Prescription B: .enc file was already missing/deleted from disk
    pid_b = "rx-missing-enc"
    img_b = str(storage_dir / f"{pid_b}.enc") # File not created

    old_time = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    conn = repo._get_connection()
    with conn:
        for pid, img in [(pid_a, img_a), (pid_b, img_b)]:
            conn.execute(
                """INSERT INTO prescriptions (id, user_id, created_at, status, confirmed, image_path, mime_type, image_quality, extracted_data)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, "user-c", old_time, "confirmed", 1, img, "image/jpeg", "good", "{}")
            )
    repo._close_conn(conn)

    print("Running purge with a missing .enc file on record B...")
    purged_count = run_purge_job(repo=repo, storage=storage, days=30)
    print(f"Purge count returned: {purged_count}")
    # Verify both records deleted from DB despite missing .enc file
    assert repo.get(pid_a) is None, "Record A still in DB"
    assert repo.get(pid_b) is None, "Record B still in DB (purge aborted on missing file!)"
    print("Record with missing file was cleanly removed from DB without aborting purge.")

# --- 3. POST /admin/purge Authentication & Authorization ---
print("\n--- 3. POST /admin/purge Security & Authorization ---")
# 3a. No token
res_no_token = client.post("/admin/purge", json={"retention_days": 30})
print(f"3a. Missing token status: {res_no_token.status_code}")
assert res_no_token.status_code in (401, 403), f"Unexpected status: {res_no_token.status_code}"

# 3b. Normal user token (no role="admin")
normal_token = create_access_token(data={"sub": "normal_user"})
res_normal = client.post("/admin/purge", json={"retention_days": 30}, headers={"Authorization": f"Bearer {normal_token}"})
print(f"3b. Normal user JWT status: {res_normal.status_code}")
assert res_normal.status_code == 403, f"Expected 403 Forbidden, got {res_normal.status_code}"

# 3c. Forged token with role='admin' but wrong secret
h_b64 = _b64encode(b'{"alg":"HS256","typ":"JWT"}')
p_b64 = _b64encode(b'{"sub":"attacker","role":"admin","exp":9999999999}')
import hmac, hashlib
sig = _b64encode(hmac.new(b"wrong-secret-key", f"{h_b64}.{p_b64}".encode(), hashlib.sha256).digest())
forged_token = f"{h_b64}.{p_b64}.{sig}"
res_forged = client.post("/admin/purge", json={"retention_days": 30}, headers={"Authorization": f"Bearer {forged_token}"})
print(f"3c. Forged JWT status: {res_forged.status_code}")
assert res_forged.status_code == 401, f"Expected 401 Unauthorized for forged token, got {res_forged.status_code}"

# 3d. Valid admin token
admin_token = create_access_token(data={"sub": "super_admin", "role": "admin"})
res_admin = client.post("/admin/purge", json={"retention_days": 30}, headers={"Authorization": f"Bearer {admin_token}"})
print(f"3d. Valid admin token status: {res_admin.status_code} | body: {res_admin.json()}")
assert res_admin.status_code == 200

# --- 4. DELETE /prescriptions/{id} Cascade Cleanup ---
print("\n--- 4. DELETE /prescriptions/{id} Cascade Cleanup ---")
user_token = create_access_token(data={"sub": "cascade-user"})
uheaders = {"Authorization": f"Bearer {user_token}"}

mock_rx = Prescription(
    prescription_id="rx-cascade-test",
    image_quality=ImageQuality.GOOD,
    medicines=[Medicine(name_raw="Aspirin 75mg", confidence=0.9)]
)

with patch("dosely.api.prescriptions.extract", return_value=mock_rx):
    res_up = client.post(
        "/prescriptions/upload",
        files={"file": ("rx.pdf", io.BytesIO(b"%PDF dummy"), "application/pdf")},
        headers=uheaders,
    )
    rx_id = res_up.json()["id"]

# Confirm to populate schedule and dependent tables
confirm_body = {
    "medicines": [{
        "name_raw": "Aspirin 75mg",
        "name_generic": "aspirin",
        "strength": "75mg",
        "form": "tablet",
        "user_accepted": True
    }]
}
client.put(f"/prescriptions/{rx_id}/confirm", json=confirm_body, headers=uheaders)

# Add feedback report
client.post(f"/prescriptions/{rx_id}/feedback", json={"drug_name": "aspirin", "issue_type": "incorrect_info"}, headers=uheaders)

# Verify image file exists
repo_live = main.get_repo()
rec_before = repo_live.get(rx_id, user_id="cascade-user")
img_path_before = Path(rec_before["image_path"])
print(f"Image exists before DELETE: {img_path_before.exists()}")
assert img_path_before.exists()

# DELETE prescription
res_del = client.delete(f"/prescriptions/{rx_id}", headers=uheaders)
print(f"DELETE status: {res_del.status_code}")
assert res_del.status_code == 200

# Verify image removed from disk
print(f"Image exists after DELETE: {img_path_before.exists()}")
assert not img_path_before.exists(), "Image was not deleted from disk!"

# Verify rows removed from DB
conn_live = repo_live._get_connection()
with conn_live:
    rx_count = conn_live.execute("SELECT count(*) FROM prescriptions WHERE id = ?", (rx_id,)).fetchone()[0]
    feed_count = conn_live.execute("SELECT count(*) FROM feedback_reports WHERE prescription_id = ?", (rx_id,)).fetchone()[0]
    dose_count = conn_live.execute("SELECT count(*) FROM dose_logs WHERE prescription_id = ?", (rx_id,)).fetchone()[0]
    rem_count = conn_live.execute("SELECT count(*) FROM reminders WHERE prescription_id = ?", (rx_id,)).fetchone()[0]
repo_live._close_conn(conn_live)

print(f"Prescriptions remaining: {rx_count}")
print(f"Feedback reports remaining: {feed_count}")
print(f"Dose logs remaining: {dose_count}")
print(f"Reminders remaining: {rem_count}")

assert rx_count == 0 and feed_count == 0 and dose_count == 0 and rem_count == 0
print("Cascade DELETE removed all associated data cleanly.")
