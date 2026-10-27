"""
Test Task 3: Automated purge background job.

Verifies:
1. Periodic purge job is registered at startup and stopped at shutdown.
2. Old prescriptions (rows, dependent tables, .enc files) are purged by one tick.
3. Fresh prescriptions are untouched.
4. POST /admin/purge returns 403 for normal users (no admin role).
5. PURGE_DAYS and PURGE_INTERVAL_HOURS are config-driven with sensible defaults.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dosely.auth import create_access_token
from dosely.repository import PrescriptionRepository
from dosely.storage import EncryptedStorage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_prescription(repo: PrescriptionRepository, storage: EncryptedStorage,
                        user_id: str = "test-user", offset_days: int = 0) -> dict:
    """Plant a prescription record with a real encrypted image file."""
    pid = str(uuid.uuid4())
    img_path = storage.save(pid, b"fake_image_content", extension="jpg")

    conn = repo._get_connection()
    now = datetime.now(timezone.utc) - timedelta(days=offset_days)
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
        # Plant related rows
        conn.execute(
            """INSERT INTO dose_logs (id, prescription_id, user_id, drug_name,
               scheduled_time, action, action_timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), pid, user_id, "TestDrug", "08:00", "taken",
             now.isoformat()),
        )
        conn.execute(
            """INSERT INTO feedback_reports (id, prescription_id, drug_name,
               issue_type, created_at) VALUES (?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), pid, "TestDrug", "incorrect_info", now.isoformat()),
        )
    repo._close_conn(conn)
    return {"id": pid, "image_path": img_path}


# ---------------------------------------------------------------------------
# Test 1: purge_older_than() removes old, keeps fresh
# ---------------------------------------------------------------------------

class TestPurgeOlderThan:
    @pytest.fixture
    def env(self, tmp_path: Path):
        storage_dir = tmp_path / "enc"
        storage_dir.mkdir(parents=True)
        repo = PrescriptionRepository(db_path=":memory:")
        storage = EncryptedStorage(storage_dir=storage_dir)
        return repo, storage

    def test_old_prescription_purged(self, env):
        repo, storage = env
        old = _make_prescription(repo, storage, offset_days=31)
        fresh = _make_prescription(repo, storage, offset_days=0)

        purged = repo.purge_older_than(days=30, storage=storage)

        assert purged == 1
        assert repo.get(old["id"]) is None
        assert repo.get(fresh["id"]) is not None

    def test_old_image_file_deleted(self, env):
        repo, storage = env
        old = _make_prescription(repo, storage, offset_days=45)
        img = Path(old["image_path"])
        assert img.exists()

        repo.purge_older_than(days=30, storage=storage)

        assert not img.exists()

    def test_dependent_tables_purged(self, env):
        repo, storage = env
        old = _make_prescription(repo, storage, offset_days=35)
        pid = old["id"]

        repo.purge_older_than(days=30, storage=storage)

        conn = repo._get_connection()
        dl = conn.execute("SELECT id FROM dose_logs WHERE prescription_id = ?", (pid,)).fetchall()
        fb = conn.execute("SELECT id FROM feedback_reports WHERE prescription_id = ?", (pid,)).fetchall()
        repo._close_conn(conn)

        assert len(dl) == 0, "dose_logs must be purged"
        assert len(fb) == 0, "feedback_reports must be purged"

    def test_fresh_prescription_not_purged(self, env):
        repo, storage = env
        fresh = _make_prescription(repo, storage, offset_days=5)

        purged = repo.purge_older_than(days=30, storage=storage)

        assert purged == 0
        assert repo.get(fresh["id"]) is not None

    def test_purge_logs_only_counts_not_ids(self, env, caplog):
        """Purge must log counts, never prescription IDs or content."""
        import logging
        repo, storage = env
        _make_prescription(repo, storage, offset_days=60)

        with caplog.at_level(logging.DEBUG):
            repo.purge_older_than(days=30, storage=storage)

        # Logs must not contain any UUID-like strings (prescription IDs)
        # IDs look like: 8-4-4-4-12 hex chars
        import re
        uuid_pattern = re.compile(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
        )
        for record in caplog.records:
            assert not uuid_pattern.search(record.getMessage()), (
                f"Purge log contained a prescription ID: {record.getMessage()}"
            )


# ---------------------------------------------------------------------------
# Test 2: Periodic purge background job registered at startup / stopped at shutdown
# ---------------------------------------------------------------------------

class TestPurgeLifespanJob:
    def test_purge_config_has_defaults(self):
        """PURGE_DAYS and PURGE_INTERVAL_HOURS must exist with sane defaults."""
        from dosely.config import PURGE_DAYS, PURGE_INTERVAL_HOURS
        assert isinstance(PURGE_DAYS, int)
        assert PURGE_DAYS == 30
        assert isinstance(PURGE_INTERVAL_HOURS, (int, float))
        assert PURGE_INTERVAL_HOURS == 24

    def test_purge_background_task_module_exists(self):
        """dosely.background.purge_job must exist and be importable."""
        from dosely.background import run_purge_job
        assert callable(run_purge_job)

    def test_purge_job_survives_exception(self, tmp_path):
        """run_purge_job must not raise even if purge_older_than fails."""
        from dosely.background import run_purge_job

        broken_repo = MagicMock()
        broken_repo.purge_older_than.side_effect = RuntimeError("DB locked")
        broken_storage = MagicMock()

        # Must not raise
        run_purge_job(repo=broken_repo, storage=broken_storage, days=30)

    def test_one_scheduler_tick_purges_old(self, tmp_path):
        """Simulate one scheduler tick: old prescription gone, fresh one remains."""
        from dosely.background import run_purge_job

        storage_dir = tmp_path / "enc"
        storage_dir.mkdir(parents=True)
        repo = PrescriptionRepository(db_path=":memory:")
        storage = EncryptedStorage(storage_dir=storage_dir)

        old = _make_prescription(repo, storage, offset_days=35)
        fresh = _make_prescription(repo, storage, offset_days=2)

        run_purge_job(repo=repo, storage=storage, days=30)

        assert repo.get(old["id"]) is None, "Old prescription must be purged"
        assert repo.get(fresh["id"]) is not None, "Fresh prescription must survive"

    def test_app_lifespan_registers_and_stops_purge_job(self):
        """The lifespan context manager must start and cleanly stop the purge job."""
        from dosely.background import purge_scheduler_context
        import contextlib

        repo_mock = MagicMock()
        repo_mock.purge_older_than.return_value = 0
        storage_mock = MagicMock()

        # Using the context manager directly
        @contextlib.asynccontextmanager
        async def _run():
            async with purge_scheduler_context(
                repo=repo_mock, storage=storage_mock,
                days=30, interval_hours=999  # very large interval so tick never fires
            ):
                yield

        async def _test():
            async with purge_scheduler_context(
                repo=repo_mock, storage=storage_mock,
                days=30, interval_hours=999
            ):
                pass  # yield and let it shut down cleanly

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Test 3: POST /admin/purge — normal user gets 403
# ---------------------------------------------------------------------------

class TestAdminPurgeEndpoint:
    @pytest.fixture
    def client(self):
        import main
        return TestClient(main.app)

    @pytest.fixture
    def normal_user_headers(self):
        token = create_access_token(data={"sub": "normal-user-task3"})
        return {"Authorization": f"Bearer {token}"}

    @pytest.fixture
    def admin_user_headers(self):
        token = create_access_token(data={"sub": "admin-user-task3", "role": "admin"})
        return {"Authorization": f"Bearer {token}"}

    def test_normal_user_gets_403(self, client, normal_user_headers):
        """A request without admin role must be rejected with 403."""
        resp = client.post(
            "/admin/purge",
            json={"retention_days": 30},
            headers=normal_user_headers,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for non-admin user, got {resp.status_code}: {resp.text}"
        )

    def test_unauthenticated_gets_401(self, client):
        """Unauthenticated request must be rejected with 401."""
        resp = client.post("/admin/purge", json={"retention_days": 30})
        assert resp.status_code == 401, (
            f"Expected 401, got {resp.status_code}: {resp.text}"
        )

    def test_admin_user_gets_200(self, client, admin_user_headers, tmp_path):
        """Admin user can call the purge endpoint."""
        from dosely.api.prescriptions import get_repo, get_storage
        import main

        repo = PrescriptionRepository(db_path=":memory:")
        storage = EncryptedStorage(storage_dir=tmp_path / "enc")
        (tmp_path / "enc").mkdir(parents=True, exist_ok=True)
        main.app.dependency_overrides[get_repo] = lambda: repo
        main.app.dependency_overrides[get_storage] = lambda: storage

        try:
            resp = client.post(
                "/admin/purge",
                json={"retention_days": 30},
                headers=admin_user_headers,
            )
            assert resp.status_code == 200, f"Expected 200 for admin, got {resp.status_code}: {resp.text}"
            assert "purged_count" in resp.json()
        finally:
            main.app.dependency_overrides.pop(get_repo, None)
            main.app.dependency_overrides.pop(get_storage, None)
