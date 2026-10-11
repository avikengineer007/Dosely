"""
tests/test_api_phase5.py
========================
Phase 5 Client App, Reminders & End-to-End Tests:
  - EXIT CHECK: End-to-end flow works from upload to a reminder firing.
  - Dose tracking (taken / skipped / snoozed action logging and history).
  - Reminders generation from confirmed timeline with timezone awareness.
  - Snoozing a scheduled reminder.
  - Simulating reminder firing (Web Push / FCM delivery).
  - Regeneration of schedule upon re-confirmation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from dosely.auth import create_access_token
from dosely.models import DoseForm, DrugClass, FoodInstruction, ImageQuality, Medicine, Prescription
from dosely.repository import PrescriptionRepository
from dosely.storage import EncryptedStorage
from main import app


@pytest.fixture
def test_env(tmp_path: Path):
    """Isolated storage and database environment for Phase 5 testing."""
    db_file = tmp_path / "test_phase5.db"
    storage_dir = tmp_path / "test_phase5_uploads"
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
    token = create_access_token(data={"sub": "patient_phase5"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ===========================================================================
# 1. PHASE 5 EXIT CHECK: Full End-to-End Flow from Upload to Reminder Firing
# ===========================================================================

class TestPhase5ExitCheckEndToEndFlow:
    def test_full_end_to_end_from_upload_to_reminder_firing(
        self, client: TestClient, auth_headers: dict[str, str], test_env, white_png: bytes
    ):
        """
        CRITICAL EXIT CHECK:
        End-to-end flow works from prescription upload, confirmation,
        timeline scheduling, reminder generation, and a reminder firing.
        """
        # Step 1: Upload prescription
        mock_extracted = Prescription(
            prescription_id="rx-e2e-001",
            image_quality=ImageQuality.GOOD,
            medicines=[
                Medicine(
                    name_raw="Tab. Amoxicillin 500 mg",
                    strength="500 mg",
                    frequency_raw="TDS",
                    food_instruction=FoodInstruction.AFTER_FOOD,
                    confidence=0.96,
                ),
                Medicine(
                    name_raw="Cap. Pantoprazole 40 mg",
                    strength="40 mg",
                    confidence=0.94,
                ),
            ],
        )

        with patch("dosely.api.prescriptions.extract", return_value=mock_extracted):
            upload_res = client.post(
                "/prescriptions",
                headers=auth_headers,
                files={"file": ("rx.png", white_png, "image/png")},
            )

        assert upload_res.status_code == 201
        rx_id = upload_res.json()["id"]

        # Step 2: Confirm extracted data (edits & acceptance)
        confirm_payload = {
            "medicines": [
                {
                    "name_raw": "Amoxicillin",
                    "strength": "500 mg",
                    "frequency_raw": "TDS",
                    "food_instruction": "after_food",
                    "user_accepted": True,
                },
                {
                    "name_raw": "Pantoprazole",
                    "strength": "40 mg",
                    "frequency_raw": None,  # Will trigger default rule
                    "food_instruction": "before_food",
                    "user_accepted": True,
                },
            ]
        }

        confirm_res = client.put(
            f"/prescriptions/{rx_id}/confirm",
            headers=auth_headers,
            json=confirm_payload,
        )
        assert confirm_res.status_code == 200
        confirm_data = confirm_res.json()
        assert confirm_data["confirmed"] is True

        # Step 3: Fetch confirmed schedule
        schedule_res = client.get(f"/prescriptions/{rx_id}/schedule", headers=auth_headers)
        assert schedule_res.status_code == 200
        sched_data = schedule_res.json()
        assert sched_data["confirmed"] is True
        timeline = sched_data["schedule"]
        assert len(timeline) >= 4  # 3 Amoxicillin + 1 Pantoprazole

        # Step 4: Generate reminders for the timeline with timezone
        rem_gen_res = client.post(
            f"/prescriptions/{rx_id}/reminders/generate",
            headers=auth_headers,
            json={"timezone": "Asia/Kolkata"},
        )
        assert rem_gen_res.status_code == 201
        gen_data = rem_gen_res.json()
        assert gen_data["reminders_count"] >= 4
        reminders = gen_data["reminders"]

        # Step 5: Snooze a reminder
        first_rem = reminders[0]
        rem_id = first_rem["id"]
        original_fire_ts = first_rem["fire_at_timestamp"]

        snooze_res = client.post(
            f"/prescriptions/{rx_id}/reminders/{rem_id}/snooze",
            headers=auth_headers,
            json={"snooze_minutes": 15},
        )
        assert snooze_res.status_code == 200
        snoozed_data = snooze_res.json()
        assert snoozed_data["status"] == "snoozed"
        assert snoozed_data["reminder"]["snoozed_until"] is not None
        assert snoozed_data["reminder"]["snoozed_until"] > original_fire_ts

        # Step 6: Fire the reminder (simulated Web Push / FCM dispatch)
        fire_res = client.post(
            f"/prescriptions/{rx_id}/reminders/{rem_id}/fire",
            headers=auth_headers,
        )
        assert fire_res.status_code == 200
        fire_data = fire_res.json()
        assert fire_data["status"] == "fired"
        assert fire_data["delivered"] is True
        assert fire_data["reminder"]["fired_at"] is not None
        assert "Dosely Reminder" in fire_data["notification"]["title"]
        assert fire_data["disclaimer"] == "This is not medical advice."


# ===========================================================================
# 2. Dose Tracking: Taken, Skipped, Snoozed
# ===========================================================================

class TestDoseTrackingActions:
    def test_log_taken_and_skipped_dose_actions(
        self, client: TestClient, auth_headers: dict[str, str], test_env, white_png: bytes
    ):
        mock_extracted = Prescription(
            prescription_id="rx-dose-track",
            image_quality=ImageQuality.GOOD,
            medicines=[Medicine(name_raw="Paracetamol 650 mg", confidence=0.95)],
        )

        with patch("dosely.api.prescriptions.extract", return_value=mock_extracted):
            upload_res = client.post(
                "/prescriptions",
                headers=auth_headers,
                files={"file": ("rx.png", white_png, "image/png")},
            )
        rx_id = upload_res.json()["id"]

        # Log dose taken
        res_taken = client.post(
            f"/prescriptions/{rx_id}/doses/log",
            headers=auth_headers,
            json={
                "drug_name": "Paracetamol",
                "scheduled_time": "08:00",
                "action": "taken",
                "notes": "Taken with a glass of water",
            },
        )
        assert res_taken.status_code == 201
        assert res_taken.json()["dose_log"]["action"] == "taken"

        # Log dose skipped
        res_skipped = client.post(
            f"/prescriptions/{rx_id}/doses/log",
            headers=auth_headers,
            json={
                "drug_name": "Paracetamol",
                "scheduled_time": "14:00",
                "action": "skipped",
                "notes": "Felt fine, no pain",
            },
        )
        assert res_skipped.status_code == 201
        assert res_skipped.json()["dose_log"]["action"] == "skipped"

        # Query dose history
        history_res = client.get(f"/prescriptions/{rx_id}/doses", headers=auth_headers)
        assert history_res.status_code == 200
        logs = history_res.json()["dose_logs"]
        assert len(logs) == 2
        actions = [l["action"] for l in logs]
        assert "taken" in actions
        assert "skipped" in actions


# ===========================================================================
# 3. Timezone Support for Reminders
# ===========================================================================

class TestReminderTimezoneHandling:
    def test_timezone_conversion(self):
        from dosely.reminders import ReminderEngine
        timeline = [{"time": "08:00", "drug": "Omeprazole", "instruction": "Take before food"}]

        # IST (UTC+5:30) -> 08:00 IST is 02:30 UTC
        ist_reminders = ReminderEngine.generate_reminders_from_timeline(
            "rx-1", "user-1", timeline, tz_name="Asia/Kolkata"
        )
        assert len(ist_reminders) == 1
        fire_ist = ist_reminders[0]["fire_at_timestamp"]
        assert "02:30:00+00:00" in fire_ist

        # UTC -> 08:00 UTC
        utc_reminders = ReminderEngine.generate_reminders_from_timeline(
            "rx-1", "user-1", timeline, tz_name="UTC"
        )
        fire_utc = utc_reminders[0]["fire_at_timestamp"]
        assert "08:00:00+00:00" in fire_utc


# ===========================================================================
# 4. Schedule Regeneration Flow
# ===========================================================================

class TestScheduleRegeneration:
    def test_reconfirm_regenerates_schedule(
        self, client: TestClient, auth_headers: dict[str, str], test_env, white_png: bytes
    ):
        mock_extracted = Prescription(
            prescription_id="rx-regen-test",
            image_quality=ImageQuality.GOOD,
            medicines=[Medicine(name_raw="Ibuprofen", confidence=0.9)],
        )

        with patch("dosely.api.prescriptions.extract", return_value=mock_extracted):
            upload_res = client.post(
                "/prescriptions",
                headers=auth_headers,
                files={"file": ("rx.png", white_png, "image/png")},
            )
        rx_id = upload_res.json()["id"]

        # First confirm: BD
        client.put(
            f"/prescriptions/{rx_id}/confirm",
            headers=auth_headers,
            json={"medicines": [{"name_raw": "Ibuprofen", "frequency_raw": "BD"}]},
        )
        sched1 = client.get(f"/prescriptions/{rx_id}/schedule", headers=auth_headers).json()["schedule"]
        assert len(sched1) == 2

        # User goes back and edits frequency to TDS
        client.put(
            f"/prescriptions/{rx_id}/confirm",
            headers=auth_headers,
            json={"medicines": [{"name_raw": "Ibuprofen", "frequency_raw": "TDS"}]},
        )
        sched2 = client.get(f"/prescriptions/{rx_id}/schedule", headers=auth_headers).json()["schedule"]
        assert len(sched2) == 3
