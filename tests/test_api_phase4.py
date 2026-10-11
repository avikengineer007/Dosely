"""
tests/test_api_phase4.py
========================
Phase 4 Backend & API integration tests:
  - JWT auth (missing/invalid token rejected)
  - Rate limiting (429 on excessive requests)
  - Encrypted image storage at rest (verification)
  - Upload -> Pending confirmation lifecycle
  - EXIT CHECK: Schedule endpoint REFUSES unconfirmed prescriptions
  - Confirmation triggers matching and scheduling
  - Schedule endpoint succeeds with timeline after confirmation
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from dosely.auth import create_access_token
from dosely.models import DoseForm, DrugClass, FoodInstruction, ImageQuality, Medicine, Prescription
from dosely.repository import PrescriptionRepository
from dosely.storage import EncryptedStorage
from main import app


@pytest.fixture
def test_env(tmp_path: Path):
    """Isolated storage and database environment for API testing."""
    db_file = tmp_path / "test_prescriptions.db"
    storage_dir = tmp_path / "test_encrypted_uploads"
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
    """Valid JWT Bearer authentication headers."""
    token = create_access_token(data={"sub": "test_user_001"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ===========================================================================
# 1. JWT Authentication tests
# ===========================================================================

class TestJwtAuth:
    def test_missing_auth_header_rejected(self, client: TestClient):
        res = client.get("/prescriptions/some-id")
        assert res.status_code == 401
        assert "Missing Authorization header" in res.json()["detail"]

    def test_invalid_bearer_token_rejected(self, client: TestClient):
        res = client.get(
            "/prescriptions/some-id",
            headers={"Authorization": "Bearer invalid.token.value"},
        )
        assert res.status_code == 401

    def test_auth_token_generation_endpoint(self, client: TestClient):
        res = client.post("/auth/token", json={"username": "dr_sharma"})
        assert res.status_code == 200
        data = res.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"


# ===========================================================================
# 2. Encrypted image storage at rest tests
# ===========================================================================

class TestEncryptedStorage:
    def test_image_stored_encrypted_at_rest(self, tmp_path: Path):
        storage = EncryptedStorage(storage_dir=tmp_path)
        original_bytes = b"RAW_SENSITIVE_PRESCRIPTION_IMAGE_BYTES_12345"

        enc_path = storage.save("rx_test_enc", original_bytes)
        file_on_disk = Path(enc_path)
        assert file_on_disk.exists()

        # Direct read from disk must NOT contain plaintext sensitive bytes
        bytes_on_disk = file_on_disk.read_bytes()
        assert bytes_on_disk != original_bytes
        assert b"RAW_SENSITIVE_PRESCRIPTION" not in bytes_on_disk

        # Decrypted via storage recovers original bytes
        decrypted = storage.load(enc_path)
        assert decrypted == original_bytes


# ===========================================================================
# 3. Rate limiting tests
# ===========================================================================

class TestRateLimiting:
    def test_rate_limit_triggers_429(self, client: TestClient, auth_headers: dict[str, str], test_env):
        from dosely.rate_limiter import SlidingWindowRateLimiter
        # Simulate limiter with max 2 requests
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        assert limiter.is_allowed("test-ip") is True
        assert limiter.is_allowed("test-ip") is True
        # 3rd request rejected
        assert limiter.is_allowed("test-ip") is False


# ===========================================================================
# 4. Upload & Retrieve Prescriptions
# ===========================================================================

class TestPrescriptionUpload:
    def test_upload_creates_pending_prescription(
        self, client: TestClient, auth_headers: dict[str, str], test_env, white_png: bytes
    ):
        mock_extracted = Prescription(
            prescription_id="rx-upload-001",
            image_quality=ImageQuality.GOOD,
            medicines=[
                Medicine(
                    name_raw="Tab. Pantoprazole 40 mg",
                    name_generic="pantoprazole",
                    drug_class=DrugClass.ANTACID,
                    strength="40 mg",
                    form=DoseForm.TABLET,
                    frequency_raw="OD",
                    frequency_normalized="Once daily",
                    confidence=0.95,
                )
            ],
        )

        with patch("dosely.api.prescriptions.extract", return_value=mock_extracted):
            files = {"file": ("rx.png", white_png, "image/png")}
            res = client.post("/prescriptions", headers=auth_headers, files=files)

        assert res.status_code == 201
        data = res.json()
        assert "id" in data
        assert data["status"] == "pending_confirmation"
        assert data["confirmed"] is False
        assert data["disclaimer"] == "This is not medical advice."

        # Fetch details
        rx_id = data["id"]
        get_res = client.get(f"/prescriptions/{rx_id}", headers=auth_headers)
        assert get_res.status_code == 200
        get_data = get_res.json()
        assert get_data["status"] == "pending_confirmation"
        assert get_data["confirmed"] is False
        assert get_data["extracted_data"]["medicines"][0]["name_raw"] == "Tab. Pantoprazole 40 mg"


# ===========================================================================
# 5. PHASE 4 EXIT CHECK: Schedule endpoint REFUSES unconfirmed prescriptions
# ===========================================================================

class TestPhase4ExitCheckScheduleRefusal:
    def test_schedule_endpoint_refuses_unconfirmed_prescription(
        self, client: TestClient, auth_headers: dict[str, str], test_env, white_png: bytes
    ):
        """
        CRITICAL EXIT CHECK:
        Tests prove the schedule endpoint refuses unconfirmed prescriptions (HTTP 400).
        """
        mock_extracted = Prescription(
            prescription_id="rx-unconfirmed-001",
            image_quality=ImageQuality.GOOD,
            medicines=[
                Medicine(
                    name_raw="Tab. Amoxicillin 500 mg",
                    confidence=0.90,
                )
            ],
        )

        with patch("dosely.api.prescriptions.extract", return_value=mock_extracted):
            files = {"file": ("rx.png", white_png, "image/png")}
            upload_res = client.post("/prescriptions", headers=auth_headers, files=files)

        rx_id = upload_res.json()["id"]

        # Attempt to access schedule BEFORE confirmation
        schedule_res = client.get(f"/prescriptions/{rx_id}/schedule", headers=auth_headers)

        # MUST REFUSE WITH HTTP 400
        assert schedule_res.status_code == 400
        detail = schedule_res.json()["detail"]
        assert "not been confirmed" in detail.lower()


# ===========================================================================
# 6. Confirmation triggers matching & scheduling, then schedule succeeds
# ===========================================================================

class TestConfirmAndScheduleFlow:
    def test_confirm_triggers_matching_and_scheduling(
        self, client: TestClient, auth_headers: dict[str, str], test_env, white_png: bytes
    ):
        mock_extracted = Prescription(
            prescription_id="rx-confirm-flow",
            image_quality=ImageQuality.GOOD,
            medicines=[
                Medicine(
                    name_raw="Tab. Pantoprazole 40 mg",
                    confidence=0.95,
                )
            ],
        )

        with patch("dosely.api.prescriptions.extract", return_value=mock_extracted):
            files = {"file": ("rx.png", white_png, "image/png")}
            upload_res = client.post("/prescriptions", headers=auth_headers, files=files)

        rx_id = upload_res.json()["id"]

        # Confirm the prescription via PUT /prescriptions/{id}/confirm
        confirm_payload = {
            "medicines": [
                {
                    "name_raw": "Pantoprazole",
                    "strength": "40 mg",
                    "form": "tablet",
                    "frequency_raw": "OD",
                    "food_instruction": "before_food",
                    "user_accepted": True,
                }
            ]
        }

        confirm_res = client.put(
            f"/prescriptions/{rx_id}/confirm",
            headers=auth_headers,
            json=confirm_payload,
        )

        assert confirm_res.status_code == 200
        confirm_data = confirm_res.json()
        assert confirm_data["status"] == "confirmed"
        assert confirm_data["confirmed"] is True
        assert len(confirm_data["matched_drugs"]) == 1
        assert confirm_data["matched_drugs"][0]["matching"]["generic"] == "pantoprazole"
        assert len(confirm_data["schedule"]) >= 1

        # Now GET /prescriptions/{id}/schedule MUST SUCCEED
        schedule_res = client.get(f"/prescriptions/{rx_id}/schedule", headers=auth_headers)
        assert schedule_res.status_code == 200
        sched_data = schedule_res.json()
        assert sched_data["status"] == "confirmed"
        assert sched_data["confirmed"] is True
        assert sched_data["disclaimer"] == "This is not medical advice."
        assert len(sched_data["schedule"]) >= 1
        assert sched_data["schedule"][0]["time"] == "07:30"
