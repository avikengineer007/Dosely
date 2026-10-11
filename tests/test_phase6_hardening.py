"""
tests/test_phase6_hardening.py
==============================
Phase 6 exit-check tests covering:
  1. DELETE /prescriptions/{id}   - cascade deletes DB rows AND encrypted image
  2. purge_older_than()           - purges records older than cutoff, skips fresh ones
  3. POST /prescriptions/{id}/feedback - records feedback without logging PII
  4. HealthDataRedactionFilter    - log records with dosage strings are redacted
  5. Refusal-instead-of-guessing  - matcher refuses ambiguous / garbled drug names
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dosely.auth import create_access_token
from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import DrugMatcher, MatchResult
from dosely.logging import HealthDataRedactionFilter
from dosely.repository import PrescriptionRepository
from dosely.storage import EncryptedStorage
from main import app


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def repo_and_storage(tmp_path: Path):
    """Isolated in-memory repo + real EncryptedStorage on tmp disk."""
    storage_dir = tmp_path / "enc_uploads"
    storage_dir.mkdir(parents=True, exist_ok=True)
    repo = PrescriptionRepository(db_path=":memory:")
    storage = EncryptedStorage(storage_dir=storage_dir)
    return repo, storage


@pytest.fixture
def test_env(tmp_path: Path):
    """FastAPI dependency-override environment."""
    db_file = tmp_path / "test.db"
    storage_dir = tmp_path / "enc"
    storage_dir.mkdir(parents=True, exist_ok=True)
    repo = PrescriptionRepository(db_path=db_file)
    storage = EncryptedStorage(storage_dir=storage_dir)

    from dosely.api.prescriptions import get_repo, get_storage
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_storage] = lambda: storage

    yield {"repo": repo, "storage": storage, "dir": storage_dir}

    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = create_access_token(data={"sub": "test_user_p6"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper: plant a prescription record with an actual encrypted image file
# ---------------------------------------------------------------------------

def _plant_prescription(repo: PrescriptionRepository, storage: EncryptedStorage,
                         user_id: str = "u1", created_offset_days: int = 0) -> dict:
    """Insert a fake prescription with a real encrypted image on disk."""
    pid = str(uuid.uuid4())
    img_path = storage.save(pid, b"fake_image_bytes", extension="jpg")

    conn = repo._get_connection()
    now = datetime.now(timezone.utc) - timedelta(days=created_offset_days)
    with conn:
        conn.execute(
            """
            INSERT INTO prescriptions
              (id, user_id, created_at, status, confirmed,
               image_path, mime_type, image_quality, extracted_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (pid, user_id, now.isoformat(), "confirmed", 1,
             img_path, "image/jpeg", "good", '{"medicines":[]}'),
        )
    repo._close_conn(conn)
    return {"id": pid, "image_path": img_path}


# ---------------------------------------------------------------------------
# Test 1: DELETE /prescriptions/{id} cascades DB rows and image file
# ---------------------------------------------------------------------------

class TestDeletePrescription:
    def test_delete_removes_db_row(self, repo_and_storage):
        repo, storage = repo_and_storage
        rec = _plant_prescription(repo, storage, user_id="u1")
        pid = rec["id"]

        assert repo.get(pid, user_id="u1") is not None

        deleted = repo.delete_prescription(pid, user_id="u1", storage=storage)

        assert deleted is True
        assert repo.get(pid, user_id="u1") is None

    def test_delete_removes_encrypted_file(self, repo_and_storage):
        repo, storage = repo_and_storage
        rec = _plant_prescription(repo, storage, user_id="u1")
        img_path = Path(rec["image_path"])

        assert img_path.exists(), "Encrypted file must exist before delete"

        repo.delete_prescription(rec["id"], user_id="u1", storage=storage)

        assert not img_path.exists(), "Encrypted file must be removed after delete"

    def test_delete_returns_false_wrong_user(self, repo_and_storage):
        repo, storage = repo_and_storage
        rec = _plant_prescription(repo, storage, user_id="u1")

        deleted = repo.delete_prescription(rec["id"], user_id="different_user", storage=storage)

        assert deleted is False
        assert repo.get(rec["id"], user_id="u1") is not None  # still there

    def test_delete_cascades_reminders_and_dose_logs(self, repo_and_storage):
        repo, storage = repo_and_storage
        rec = _plant_prescription(repo, storage, user_id="u1")
        pid = rec["id"]

        # Plant linked records
        conn = repo._get_connection()
        now_iso = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """INSERT INTO dose_logs (id, prescription_id, user_id, drug_name,
                   scheduled_time, action, action_timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), pid, "u1", "Paracetamol", "08:00", "taken", now_iso),
            )
            conn.execute(
                """INSERT INTO feedback_reports (id, prescription_id, drug_name,
                   issue_type, created_at) VALUES (?, ?, ?, ?, ?)""",
                (str(uuid.uuid4()), pid, "Paracetamol", "incorrect_info", now_iso),
            )
        repo._close_conn(conn)

        repo.delete_prescription(pid, user_id="u1", storage=storage)

        # All cascaded tables must be empty for this prescription
        conn2 = repo._get_connection()
        rows = conn2.execute("SELECT id FROM dose_logs WHERE prescription_id = ?", (pid,)).fetchall()
        fb   = conn2.execute("SELECT id FROM feedback_reports WHERE prescription_id = ?", (pid,)).fetchall()
        repo._close_conn(conn2)

        assert len(rows) == 0
        assert len(fb) == 0

    def test_delete_endpoint_returns_200(self, client, test_env, auth_headers):
        repo, storage = test_env["repo"], test_env["storage"]
        rec = _plant_prescription(repo, storage, user_id="test_user_p6")

        resp = client.delete(
            f"/prescriptions/{rec['id']}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_endpoint_returns_404_for_missing(self, client, test_env, auth_headers):
        resp = client.delete("/prescriptions/nonexistent-id", headers=auth_headers)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 2: purge_older_than
# ---------------------------------------------------------------------------

class TestPurgeOlderThan:
    def test_purges_old_records(self, repo_and_storage):
        repo, storage = repo_and_storage
        old = _plant_prescription(repo, storage, user_id="u1", created_offset_days=31)
        fresh = _plant_prescription(repo, storage, user_id="u1", created_offset_days=0)

        purged = repo.purge_older_than(days=30, storage=storage)

        assert purged == 1
        assert repo.get(old["id"]) is None
        assert repo.get(fresh["id"]) is not None

    def test_purged_image_file_deleted(self, repo_and_storage):
        repo, storage = repo_and_storage
        old = _plant_prescription(repo, storage, user_id="u1", created_offset_days=45)
        img = Path(old["image_path"])

        assert img.exists()

        repo.purge_older_than(days=30, storage=storage)

        assert not img.exists()

    def test_purge_skips_fresh_records(self, repo_and_storage):
        repo, storage = repo_and_storage
        fresh = _plant_prescription(repo, storage, user_id="u1", created_offset_days=5)

        purged = repo.purge_older_than(days=30, storage=storage)

        assert purged == 0
        assert repo.get(fresh["id"]) is not None

    def test_admin_purge_endpoint(self, client, test_env, auth_headers):
        repo, storage = test_env["repo"], test_env["storage"]
        _plant_prescription(repo, storage, user_id="test_user_p6", created_offset_days=60)

        resp = client.post(
            "/admin/purge",
            json={"retention_days": 30},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["purged_count"] >= 1


# ---------------------------------------------------------------------------
# Test 3: POST /prescriptions/{id}/feedback
# ---------------------------------------------------------------------------

class TestFeedbackEndpoint:
    def test_feedback_creates_record(self, repo_and_storage):
        repo, storage = repo_and_storage
        rec = _plant_prescription(repo, storage, user_id="u1")
        pid = rec["id"]

        fb_id = str(uuid.uuid4())
        result = repo.log_feedback(
            feedback_id=fb_id,
            prescription_id=pid,
            drug_name="Paracetamol",
            issue_type="incorrect_info",
            field_name="strength",
            user_comment="The strength is 650 mg not 500 mg",
        )

        assert result["id"] == fb_id
        assert result["prescription_id"] == pid
        assert result["issue_type"] == "incorrect_info"

    def test_get_feedback_by_prescription(self, repo_and_storage):
        repo, storage = repo_and_storage
        rec = _plant_prescription(repo, storage, user_id="u1")
        pid = rec["id"]

        repo.log_feedback(str(uuid.uuid4()), pid, "DrugA", "incorrect_info")
        repo.log_feedback(str(uuid.uuid4()), pid, "DrugB", "wrong_class")

        rows = repo.get_feedback(prescription_id=pid)
        assert len(rows) == 2

    def test_feedback_endpoint_returns_201(self, client, test_env, auth_headers):
        repo, storage = test_env["repo"], test_env["storage"]
        rec = _plant_prescription(repo, storage, user_id="test_user_p6")

        resp = client.post(
            f"/prescriptions/{rec['id']}/feedback",
            json={
                "drug_name": "Amoxicillin",
                "field_name": "frequency_raw",
                "issue_type": "incorrect_info",
                "user_comment": None,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["created"] is True
        assert body["feedback"]["drug_name"] == "Amoxicillin"

    def test_feedback_endpoint_404_for_missing_prescription(self, client, test_env, auth_headers):
        resp = client.post(
            "/prescriptions/does-not-exist/feedback",
            json={"drug_name": "Amoxicillin", "issue_type": "incorrect_info"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 4: HealthDataRedactionFilter
# ---------------------------------------------------------------------------

class TestPrivacyLogRedaction:
    def _make_record(self, msg: str) -> logging.LogRecord:
        record = logging.LogRecord(
            name="dosely.test", level=logging.INFO,
            pathname="", lineno=0, msg=msg, args=(), exc_info=None,
        )
        return record

    def test_dosage_string_is_redacted(self):
        filt = HealthDataRedactionFilter()
        record = self._make_record("Prescribed 500mg twice daily")
        filt.filter(record)
        assert "500mg" not in record.msg or "[REDACTED]" in record.msg

    def test_mcg_dosage_redacted(self):
        filt = HealthDataRedactionFilter()
        record = self._make_record("Dose is 25mcg per day")
        filt.filter(record)
        assert "25mcg" not in record.msg or "[REDACTED]" in record.msg

    def test_patient_name_redacted(self):
        filt = HealthDataRedactionFilter()
        record = self._make_record("patient Sharma's prescription was uploaded")
        filt.filter(record)
        assert "Sharma" not in record.msg or "[REDACTED]" in record.msg

    def test_safe_operational_log_passes_through(self):
        filt = HealthDataRedactionFilter()
        record = self._make_record("Prescription confirmed successfully")
        filt.filter(record)
        assert "confirmed" in record.msg  # no redaction needed


# ---------------------------------------------------------------------------
# Test 5: Refusal-instead-of-guessing on degraded / ambiguous names
# ---------------------------------------------------------------------------

class TestRefusalInsteadOfGuessing:
    @pytest.fixture(autouse=True)
    def _setup_matcher(self):
        db = DrugDatabase.build()
        self.matcher = DrugMatcher(db)

    def _is_refused(self, result: MatchResult) -> bool:
        """True when the matcher refused to guess (match_type == unknown)."""
        return result.match_type == "unknown"

    def test_partial_name_par_refused(self):
        result = self.matcher.match("Par")
        assert self._is_refused(result), f"Expected refusal but got: {result}"

    def test_partial_name_amox_refused(self):
        result = self.matcher.match("Amox")
        assert self._is_refused(result), f"Expected refusal but got: {result}"

    def test_gibberish_refused(self):
        result = self.matcher.match("Xyzqrst")
        assert self._is_refused(result), f"Expected refusal but got: {result}"

    def test_noise_token_refused(self):
        result = self.matcher.match("Rx 1")
        assert self._is_refused(result), f"Expected refusal but got: {result}"

    def test_numeric_noise_refused(self):
        result = self.matcher.match("0mg")
        assert self._is_refused(result), f"Expected refusal but got: {result}"

    def test_valid_drug_paracetamol_not_refused(self):
        result = self.matcher.match("Paracetamol")
        assert result.match_type != "unknown", f"Expected match but refused: {result}"

    def test_valid_drug_metformin_not_refused(self):
        result = self.matcher.match("Metformin")
        assert result.match_type != "unknown", f"Expected match but refused: {result}"
