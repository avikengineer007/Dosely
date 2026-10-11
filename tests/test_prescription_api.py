"""
tests/test_prescription_api.py
==============================
Integration and unit tests for:
1. Confirm-before-schedule rule:
   - GET /prescriptions/{id}/schedule fails before PUT /prescriptions/{id}/confirm
   - GET /prescriptions/{id}/schedule succeeds after PUT /prescriptions/{id}/confirm
2. JWT Authentication enforcement
3. At-rest image encryption verification
4. Zero-logging of prescription contents (privacy test)
5. Request validation and error handling
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from dosely.security.auth import create_access_token
from dosely.security.encryption import decrypt_bytes
from dosely.storage import storage_manager
from main import app


@pytest.fixture(autouse=True)
def clean_storage():
    """Ensure clean storage before and after each test."""
    storage_manager.clear()
    yield
    storage_manager.clear()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = create_access_token({"sub": "test_doctor", "user_id": "usr_test123"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def sample_image_bytes() -> bytes:
    # 1x1 transparent PNG bytes
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# ---------------------------------------------------------------------------
# 1. Tests for the Confirm-Before-Schedule Rule
# ---------------------------------------------------------------------------

class TestConfirmBeforeScheduleRule:
    """Verifies that scheduling strictly cannot run before human confirmation."""

    def test_schedule_before_confirm_returns_400(
        self, client: TestClient, auth_headers: dict[str, str], sample_image_bytes: bytes
    ):
        """Rule: GET /prescriptions/{id}/schedule BEFORE confirmation MUST return 400."""
        # 1. Upload prescription
        upload_resp = client.post(
            "/prescriptions",
            files={"file": ("rx.png", sample_image_bytes, "image/png")},
            headers=auth_headers,
        )
        assert upload_resp.status_code == 201
        rx_id = upload_resp.json()["prescription_id"]
        assert upload_resp.json()["status"] == "pending_confirmation"

        # 2. Try fetching schedule before confirmation -> must be blocked!
        sched_resp = client.get(f"/prescriptions/{rx_id}/schedule", headers=auth_headers)
        assert sched_resp.status_code == 400
        error_detail = sched_resp.json()["detail"]
        assert "not been confirmed yet" in error_detail.lower()

    def test_schedule_after_confirm_succeeds(
        self, client: TestClient, auth_headers: dict[str, str], sample_image_bytes: bytes
    ):
        """Rule: After PUT /prescriptions/{id}/confirm, schedule is available and populated."""
        # 1. Upload prescription
        upload_resp = client.post(
            "/prescriptions",
            files={"file": ("rx.png", sample_image_bytes, "image/png")},
            headers=auth_headers,
        )
        assert upload_resp.status_code == 201
        rx_data = upload_resp.json()
        rx_id = rx_data["prescription_id"]

        # 2. Review and confirm medicines
        confirm_payload = {
            "medicines": [
                {
                    "name_raw": "Tab. Amoxicillin 500 mg",
                    "name_generic": "amoxicillin",
                    "drug_class": "antibiotic",
                    "strength": "500 mg",
                    "form": "tablet",
                    "frequency_raw": "TDS",
                    "frequency_normalized": "Three times daily",
                    "food_instruction": "after_food",
                    "duration_days": 5,
                    "user_reviewed": True,
                    "user_accepted": True,
                },
                {
                    "name_raw": "Cap. Omeprazole 20 mg",
                    "name_generic": "omeprazole",
                    "drug_class": "antacid_ppi",
                    "strength": "20 mg",
                    "form": "capsule",
                    "frequency_raw": "OD",
                    "frequency_normalized": "Once daily",
                    "food_instruction": "before_food",
                    "duration_days": 7,
                    "user_reviewed": True,
                    "user_accepted": True,
                },
            ],
            "patient_name": "Rahul Verma",
            "prescription_date": "2026-10-08",
        }

        confirm_resp = client.put(
            f"/prescriptions/{rx_id}/confirm",
            json=confirm_payload,
            headers=auth_headers,
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json()["status"] == "confirmed"

        # 3. GET /prescriptions/{id}/schedule now succeeds
        sched_resp = client.get(f"/prescriptions/{rx_id}/schedule", headers=auth_headers)
        assert sched_resp.status_code == 200
        data = sched_resp.json()
        assert data["prescription_id"] == rx_id
        assert data["status"] == "confirmed"
        assert len(data["timeline"]) > 0

        # Verify timeline contains expected scheduled doses
        drugs_scheduled = [item["drug"].lower() for item in data["timeline"]]
        assert any("amoxicillin" in d for d in drugs_scheduled)
        assert any("omeprazole" in d for d in drugs_scheduled)


# ---------------------------------------------------------------------------
# 2. JWT Authentication Tests
# ---------------------------------------------------------------------------

class TestJWTAuthentication:
    """Verifies that all prescription endpoints enforce JWT authentication."""

    def test_missing_auth_header_rejected(self, client: TestClient, sample_image_bytes: bytes):
        resp = client.post(
            "/prescriptions",
            files={"file": ("rx.png", sample_image_bytes, "image/png")},
        )
        assert resp.status_code == 401
        assert "not provided" in resp.json()["detail"].lower()

    def test_invalid_token_rejected(self, client: TestClient):
        resp = client.get(
            "/prescriptions/rx-123",
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        assert resp.status_code == 401

    def test_token_endpoint_generates_valid_token(self, client: TestClient):
        token_resp = client.post("/auth/token", json={"username": "dr_smith"})
        assert token_resp.status_code == 200
        token = token_resp.json()["access_token"]

        # Use new token to access health or an endpoint
        resp = client.get(
            "/prescriptions/non-existent",
            headers={"Authorization": f"Bearer {token}"},
        )
        # Should be 404 (authenticated but not found), NOT 401 (unauthorized)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 3. Encryption at Rest Tests
# ---------------------------------------------------------------------------

class TestEncryptionAtRest:
    """Verifies that images stored on disk are strictly encrypted."""

    def test_image_stored_encrypted_on_disk(
        self, client: TestClient, auth_headers: dict[str, str], sample_image_bytes: bytes
    ):
        upload_resp = client.post(
            "/prescriptions",
            files={"file": ("rx_test.png", sample_image_bytes, "image/png")},
            headers=auth_headers,
        )
        assert upload_resp.status_code == 201
        rx_id = upload_resp.json()["prescription_id"]

        # Fetch record from storage manager
        record = storage_manager.get_record(rx_id)
        assert record is not None
        encrypted_path = Path(record.encrypted_file_path)
        assert encrypted_path.exists()

        # Read raw bytes directly from the filesystem
        raw_disk_bytes = encrypted_path.read_bytes()

        # MUST NOT start with PNG magic bytes (0x89 0x50 0x4E 0x47)
        assert not raw_disk_bytes.startswith(b"\x89PNG")
        assert raw_disk_bytes != sample_image_bytes

        # Decrypting via the security module restores original bytes
        decrypted = decrypt_bytes(raw_disk_bytes)
        assert decrypted == sample_image_bytes

    def test_image_streaming_endpoint_decrypts_successfully(
        self, client: TestClient, auth_headers: dict[str, str], sample_image_bytes: bytes
    ):
        upload_resp = client.post(
            "/prescriptions",
            files={"file": ("rx_test.png", sample_image_bytes, "image/png")},
            headers=auth_headers,
        )
        rx_id = upload_resp.json()["prescription_id"]

        img_resp = client.get(f"/prescriptions/{rx_id}/image", headers=auth_headers)
        assert img_resp.status_code == 200
        assert img_resp.content == sample_image_bytes
        assert "image/png" in img_resp.headers["content-type"]


# ---------------------------------------------------------------------------
# 4. Privacy Logging Test (Never Log Prescription Contents)
# ---------------------------------------------------------------------------

class TestPrivacyLogging:
    """Verifies that prescription contents are NEVER logged."""

    def test_sensitive_prescription_contents_never_logged(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        sample_image_bytes: bytes,
        caplog: pytest.LogCaptureFixture,
    ):
        caplog.set_level(logging.INFO)

        upload_resp = client.post(
            "/prescriptions",
            files={"file": ("rx.png", sample_image_bytes, "image/png")},
            headers=auth_headers,
        )
        rx_id = upload_resp.json()["prescription_id"]

        # Confirm with explicit clinical content
        client.put(
            f"/prescriptions/{rx_id}/confirm",
            json={
                "medicines": [
                    {
                        "name_raw": "Tab. Amoxicillin 500 mg",
                        "name_generic": "amoxicillin",
                        "drug_class": "antibiotic",
                        "frequency_raw": "TDS",
                    }
                ],
                "patient_name": "SensitivePatientName123",
            },
            headers=auth_headers,
        )

        # Inspect all captured logs
        captured_text = caplog.text.lower()

        # The patient's name MUST NOT be in the logs
        assert "sensitivepatientname123" not in captured_text
        # Specific drug names and raw dosages must not appear unredacted
        assert "amoxicillin" not in captured_text or "[redacted_medicine]" in captured_text


# ---------------------------------------------------------------------------
# 5. Request Validation Tests
# ---------------------------------------------------------------------------

class TestRequestValidation:
    """Verifies HTTP status codes and error responses for invalid inputs."""

    def test_unsupported_file_type_rejected(self, client: TestClient, auth_headers: dict[str, str]):
        resp = client.post(
            "/prescriptions",
            files={"file": ("malicious.exe", b"binary content", "application/x-msdownload")},
            headers=auth_headers,
        )
        assert resp.status_code == 415

    def test_empty_confirmation_payload_rejected(
        self, client: TestClient, auth_headers: dict[str, str], sample_image_bytes: bytes
    ):
        upload_resp = client.post(
            "/prescriptions",
            files={"file": ("rx.png", sample_image_bytes, "image/png")},
            headers=auth_headers,
        )
        rx_id = upload_resp.json()["prescription_id"]

        resp = client.put(
            f"/prescriptions/{rx_id}/confirm",
            json={"medicines": []},  # empty list
            headers=auth_headers,
        )
        assert resp.status_code == 422  # Pydantic validation error (min_length=1)

    def test_nonexistent_prescription_returns_404(
        self, client: TestClient, auth_headers: dict[str, str]
    ):
        resp = client.get("/prescriptions/rx-does-not-exist", headers=auth_headers)
        assert resp.status_code == 404

        sched_resp = client.get("/prescriptions/rx-does-not-exist/schedule", headers=auth_headers)
        assert sched_resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. Rate Limiting & UI Static Mount Tests
# ---------------------------------------------------------------------------

class TestRateLimitingAndUI:
    """Verifies that the rate limiter is wired and static app is accessible."""

    def test_rate_limiter_is_configured_on_app(self):
        from dosely.api.prescriptions import limiter
        assert app.state.limiter is limiter

    def test_frontend_ui_static_served(self, client: TestClient):
        resp = client.get("/app/")
        assert resp.status_code == 200
        assert "Dosely" in resp.text
        assert "root" in resp.text

