"""
dosely/reminders.py
===================
Reminders engine: generates, manages, and simulates firing of medication reminders
from confirmed prescription timelines with timezone awareness and snoozing.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo


class ReminderEngine:
    """Generates scheduled reminders from confirmed timelines and handles snoozing & dispatch."""

    @staticmethod
    def generate_reminders_from_timeline(
        prescription_id: str,
        user_id: str,
        timeline: list[dict[str, Any]],
        tz_name: str = "Asia/Kolkata",
        reference_date: Optional[date] = None,
    ) -> list[dict[str, Any]]:
        """
        Generate scheduled reminder records from confirmed timeline entries.
        Handles timezone conversion and creates web push / FCM notification payloads.
        """
        ref_date = reference_date or date.today()
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc

        reminders: list[dict[str, Any]] = []

        for entry in timeline:
            time_str = entry.get("time", "")
            if not time_str or time_str == "as needed":
                continue  # PRN / as-needed doses are not fixed scheduled alerts

            drug_name = entry.get("drug", "Medication")
            instruction = entry.get("instruction", "Take as directed")
            warnings = entry.get("warnings", [])

            try:
                parts = time_str.split(":")
                hour = int(parts[0])
                minute = int(parts[1])
            except Exception:
                continue

            # Construct local datetime in specified timezone
            local_dt = datetime(
                year=ref_date.year,
                month=ref_date.month,
                day=ref_date.day,
                hour=hour,
                minute=minute,
                second=0,
                tzinfo=tz,
            )

            fire_at_iso = local_dt.astimezone(timezone.utc).isoformat()
            reminder_id = str(uuid.uuid4())

            notification_payload = {
                "title": f"Dosely Reminder: {drug_name}",
                "body": f"Time to take your {drug_name} at {time_str}. {instruction}",
                "tag": f"rx-{prescription_id}-{drug_name}-{time_str}",
                "data": {
                    "prescription_id": prescription_id,
                    "drug": drug_name,
                    "scheduled_time": time_str,
                    "instruction": instruction,
                    "warnings": warnings,
                    "disclaimer": "This is not medical advice.",
                },
            }

            reminders.append({
                "id": reminder_id,
                "prescription_id": prescription_id,
                "user_id": user_id,
                "drug_name": drug_name,
                "scheduled_time": time_str,
                "timezone": tz_name,
                "fire_at_timestamp": fire_at_iso,
                "status": "scheduled",
                "notification_payload": notification_payload,
            })

        return reminders

    @staticmethod
    def calculate_snooze(
        current_fire_timestamp_iso: str,
        snooze_minutes: int = 15,
    ) -> str:
        """Add snooze minutes to an existing ISO timestamp."""
        try:
            dt = datetime.fromisoformat(current_fire_timestamp_iso)
        except Exception:
            dt = datetime.now(timezone.utc)
        snoozed_dt = dt + timedelta(minutes=snooze_minutes)
        return snoozed_dt.isoformat()
