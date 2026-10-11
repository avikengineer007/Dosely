"""
dosely/repository.py
====================
SQLite persistence repository for prescription workflows.
Manages storage and status tracking for prescriptions, confirmed data, and schedules.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "prescriptions.db"


class PrescriptionRepository:
    """Manages the prescription records lifecycle in SQLite."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._shared_conn: Optional[sqlite3.Connection] = None
        else:
            self._shared_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared_conn.row_factory = sqlite3.Row
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._shared_conn is not None:
            return self._shared_conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _close_conn(self, conn: sqlite3.Connection) -> None:
        if self._shared_conn is None:
            conn.close()

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS prescriptions (
                        id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        status TEXT NOT NULL,
                        confirmed INTEGER NOT NULL DEFAULT 0,
                        image_path TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        image_quality TEXT NOT NULL,
                        extracted_data TEXT NOT NULL,
                        confirmed_data TEXT,
                        matched_drugs TEXT,
                        schedule TEXT
                    );

                    CREATE TABLE IF NOT EXISTS dose_logs (
                        id TEXT PRIMARY KEY,
                        prescription_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        drug_name TEXT NOT NULL,
                        scheduled_time TEXT NOT NULL,
                        action TEXT NOT NULL,
                        action_timestamp TEXT NOT NULL,
                        snooze_minutes INTEGER,
                        notes TEXT
                    );

                    CREATE TABLE IF NOT EXISTS reminders (
                        id TEXT PRIMARY KEY,
                        prescription_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        drug_name TEXT NOT NULL,
                        scheduled_time TEXT NOT NULL,
                        timezone TEXT NOT NULL,
                        fire_at_timestamp TEXT NOT NULL,
                        status TEXT NOT NULL,
                        snoozed_until TEXT,
                        fired_at TEXT,
                        notification_payload TEXT
                    );

                    CREATE TABLE IF NOT EXISTS feedback_reports (
                        id TEXT PRIMARY KEY,
                        prescription_id TEXT NOT NULL,
                        drug_name TEXT NOT NULL,
                        field_name TEXT,
                        issue_type TEXT NOT NULL,
                        user_comment TEXT,
                        created_at TEXT NOT NULL
                    );
                    """
                )
        finally:
            self._close_conn(conn)

    def create(
        self,
        prescription_id: str,
        user_id: str,
        image_path: str,
        mime_type: str,
        image_quality: str,
        extracted_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Insert a newly uploaded prescription in pending_confirmation state."""
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO prescriptions (
                        id, user_id, created_at, status, confirmed,
                        image_path, mime_type, image_quality,
                        extracted_data, confirmed_data, matched_drugs, schedule
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                    """,
                    (
                        prescription_id,
                        user_id,
                        now_iso,
                        "pending_confirmation",
                        0,
                        image_path,
                        mime_type,
                        image_quality,
                        json.dumps(extracted_data),
                    ),
                )
            return self.get(prescription_id, user_id=user_id)  # type: ignore[return-value]
        finally:
            self._close_conn(conn)

    def get(self, prescription_id: str, user_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Retrieve prescription by ID, optionally verifying user_id."""
        conn = self._get_connection()
        try:
            if user_id:
                cursor = conn.execute(
                    "SELECT * FROM prescriptions WHERE id = ? AND user_id = ?",
                    (prescription_id, user_id),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM prescriptions WHERE id = ?",
                    (prescription_id,),
                )
            row = cursor.fetchone()
            if not row:
                return None

            return {
                "id": row["id"],
                "user_id": row["user_id"],
                "created_at": row["created_at"],
                "status": row["status"],
                "confirmed": bool(row["confirmed"]),
                "image_path": row["image_path"],
                "mime_type": row["mime_type"],
                "image_quality": row["image_quality"],
                "extracted_data": json.loads(row["extracted_data"]) if row["extracted_data"] else None,
                "confirmed_data": json.loads(row["confirmed_data"]) if row["confirmed_data"] else None,
                "matched_drugs": json.loads(row["matched_drugs"]) if row["matched_drugs"] else None,
                "schedule": json.loads(row["schedule"]) if row["schedule"] else None,
            }
        finally:
            self._close_conn(conn)

    def confirm(
        self,
        prescription_id: str,
        user_id: str,
        confirmed_data: dict[str, Any],
        matched_drugs: list[dict[str, Any]],
        schedule: list[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        """Mark prescription as confirmed and persist confirmed data, matching results, and schedule."""
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE prescriptions
                    SET status = 'confirmed',
                        confirmed = 1,
                        confirmed_data = ?,
                        matched_drugs = ?,
                        schedule = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        json.dumps(confirmed_data),
                        json.dumps(matched_drugs),
                        json.dumps(schedule),
                        prescription_id,
                        user_id,
                    ),
                )
                if cursor.rowcount == 0:
                    return None
            return self.get(prescription_id, user_id=user_id)
        finally:
            self._close_conn(conn)

    def delete(self, prescription_id: str, user_id: Optional[str] = None) -> bool:
        """Delete prescription row."""
        conn = self._get_connection()
        try:
            with conn:
                if user_id:
                    cursor = conn.execute(
                        "DELETE FROM prescriptions WHERE id = ? AND user_id = ?",
                        (prescription_id, user_id),
                    )
                else:
                    cursor = conn.execute(
                        "DELETE FROM prescriptions WHERE id = ?",
                        (prescription_id,),
                    )
                return cursor.rowcount > 0
        finally:
            self._close_conn(conn)

    # ------------------------------------------------------------------
    # Dose Logging
    # ------------------------------------------------------------------

    def log_dose(
        self,
        log_id: str,
        prescription_id: str,
        user_id: str,
        drug_name: str,
        scheduled_time: str,
        action: str,
        snooze_minutes: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> dict[str, Any]:
        """Record a dose tracking action (taken, skipped, snoozed)."""
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO dose_logs (
                        id, prescription_id, user_id, drug_name,
                        scheduled_time, action, action_timestamp, snooze_minutes, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        log_id,
                        prescription_id,
                        user_id,
                        drug_name,
                        scheduled_time,
                        action,
                        now_iso,
                        snooze_minutes,
                        notes,
                    ),
                )
            return {
                "id": log_id,
                "prescription_id": prescription_id,
                "user_id": user_id,
                "drug_name": drug_name,
                "scheduled_time": scheduled_time,
                "action": action,
                "action_timestamp": now_iso,
                "snooze_minutes": snooze_minutes,
                "notes": notes,
            }
        finally:
            self._close_conn(conn)

    def get_dose_logs(
        self,
        prescription_id: str,
        user_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Retrieve dose history for a prescription."""
        conn = self._get_connection()
        try:
            if user_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM dose_logs
                    WHERE prescription_id = ? AND user_id = ?
                    ORDER BY action_timestamp DESC
                    """,
                    (prescription_id, user_id),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM dose_logs
                    WHERE prescription_id = ?
                    ORDER BY action_timestamp DESC
                    """,
                    (prescription_id,),
                )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            self._close_conn(conn)

    # ------------------------------------------------------------------
    # Reminders
    # ------------------------------------------------------------------

    def save_reminders(
        self,
        reminders_list: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Insert a batch of scheduled reminders."""
        conn = self._get_connection()
        try:
            with conn:
                for r in reminders_list:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO reminders (
                            id, prescription_id, user_id, drug_name,
                            scheduled_time, timezone, fire_at_timestamp,
                            status, snoozed_until, fired_at, notification_payload
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            r["id"],
                            r["prescription_id"],
                            r["user_id"],
                            r["drug_name"],
                            r["scheduled_time"],
                            r["timezone"],
                            r["fire_at_timestamp"],
                            r.get("status", "scheduled"),
                            r.get("snoozed_until"),
                            r.get("fired_at"),
                            json.dumps(r.get("notification_payload", {})),
                        ),
                    )
            return reminders_list
        finally:
            self._close_conn(conn)

    def get_reminders(
        self,
        prescription_id: str,
        user_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Retrieve all scheduled reminders for a prescription."""
        conn = self._get_connection()
        try:
            if user_id:
                cursor = conn.execute(
                    """
                    SELECT * FROM reminders
                    WHERE prescription_id = ? AND user_id = ?
                    ORDER BY fire_at_timestamp ASC
                    """,
                    (prescription_id, user_id),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM reminders
                    WHERE prescription_id = ?
                    ORDER BY fire_at_timestamp ASC
                    """,
                    (prescription_id,),
                )
            rows = []
            for r in cursor.fetchall():
                d = dict(r)
                if d.get("notification_payload"):
                    d["notification_payload"] = json.loads(d["notification_payload"])
                rows.append(d)
            return rows
        finally:
            self._close_conn(conn)

    def get_reminder(self, reminder_id: str) -> Optional[dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT * FROM reminders WHERE id = ?",
                (reminder_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("notification_payload"):
                d["notification_payload"] = json.loads(d["notification_payload"])
            return d
        finally:
            self._close_conn(conn)

    def snooze_reminder(
        self,
        reminder_id: str,
        snoozed_until_iso: str,
    ) -> Optional[dict[str, Any]]:
        """Update reminder status to snoozed and update fire timestamp."""
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE reminders
                    SET status = 'snoozed',
                        snoozed_until = ?,
                        fire_at_timestamp = ?
                    WHERE id = ?
                    """,
                    (snoozed_until_iso, snoozed_until_iso, reminder_id),
                )
                if cursor.rowcount == 0:
                    return None
            return self.get_reminder(reminder_id)
        finally:
            self._close_conn(conn)

    def fire_reminder(
        self,
        reminder_id: str,
    ) -> Optional[dict[str, Any]]:
        """Mark reminder as fired and record delivery timestamp."""
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            with conn:
                cursor = conn.execute(
                    """
                    UPDATE reminders
                    SET status = 'fired',
                        fired_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, reminder_id),
                )
                if cursor.rowcount == 0:
                    return None
            return self.get_reminder(reminder_id)
        finally:
            self._close_conn(conn)

    def delete_prescription(
        self,
        prescription_id: str,
        user_id: Optional[str] = None,
        storage: Optional[Any] = None,
    ) -> bool:
        """
        Permanently delete a prescription and all related records (doses, reminders, feedback).
        Also deletes the encrypted image file on disk.
        """
        conn = self._get_connection()
        try:
            if user_id:
                cursor = conn.execute(
                    "SELECT image_path FROM prescriptions WHERE id = ? AND user_id = ?",
                    (prescription_id, user_id),
                )
            else:
                cursor = conn.execute(
                    "SELECT image_path FROM prescriptions WHERE id = ?",
                    (prescription_id,),
                )
            row = cursor.fetchone()
            if not row:
                return False

            image_path = row["image_path"]

            with conn:
                conn.execute("DELETE FROM dose_logs WHERE prescription_id = ?", (prescription_id,))
                conn.execute("DELETE FROM reminders WHERE prescription_id = ?", (prescription_id,))
                conn.execute("DELETE FROM feedback_reports WHERE prescription_id = ?", (prescription_id,))
                if user_id:
                    conn.execute("DELETE FROM prescriptions WHERE id = ? AND user_id = ?", (prescription_id, user_id))
                else:
                    conn.execute("DELETE FROM prescriptions WHERE id = ?", (prescription_id,))

            # Remove physical file
            if storage is not None:
                storage.delete(image_path)
            else:
                p = Path(image_path)
                if p.exists():
                    p.unlink()

            return True
        finally:
            self._close_conn(conn)

    def delete(
        self,
        prescription_id: str,
        user_id: Optional[str] = None,
        storage: Optional[Any] = None,
    ) -> bool:
        """Alias for delete_prescription."""
        return self.delete_prescription(prescription_id, user_id=user_id, storage=storage)

    def purge_older_than(
        self,
        days: int = 30,
        storage: Optional[Any] = None,
    ) -> int:
        """
        Permanently purge prescriptions created more than `days` ago,
        along with disk image files and associated records.
        """
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_iso = cutoff_dt.isoformat()

        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT id, image_path FROM prescriptions WHERE created_at < ?",
                (cutoff_iso,),
            )
            rows = cursor.fetchall()
            if not rows:
                return 0

            purged_count = len(rows)
            ids = [r["id"] for r in rows]
            image_paths = [r["image_path"] for r in rows]

            with conn:
                placeholders = ",".join("?" for _ in ids)
                conn.execute(f"DELETE FROM dose_logs WHERE prescription_id IN ({placeholders})", ids)
                conn.execute(f"DELETE FROM reminders WHERE prescription_id IN ({placeholders})", ids)
                conn.execute(f"DELETE FROM feedback_reports WHERE prescription_id IN ({placeholders})", ids)
                conn.execute(f"DELETE FROM prescriptions WHERE id IN ({placeholders})", ids)

            for img_p in image_paths:
                if storage is not None:
                    storage.delete(img_p)
                else:
                    p = Path(img_p)
                    if p.exists():
                        p.unlink()

            return purged_count
        finally:
            self._close_conn(conn)

    def log_feedback(
        self,
        feedback_id: str,
        prescription_id: str,
        drug_name: str,
        issue_type: str,
        field_name: Optional[str] = None,
        user_comment: Optional[str] = None,
    ) -> dict[str, Any]:
        """Save user feedback report for medicine card verification."""
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO feedback_reports (
                        id, prescription_id, drug_name, field_name, issue_type, user_comment, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        feedback_id,
                        prescription_id,
                        drug_name,
                        field_name,
                        issue_type,
                        user_comment,
                        now_iso,
                    ),
                )
            return {
                "id": feedback_id,
                "prescription_id": prescription_id,
                "drug_name": drug_name,
                "field_name": field_name,
                "issue_type": issue_type,
                "user_comment": user_comment,
                "created_at": now_iso,
            }
        finally:
            self._close_conn(conn)

    def get_feedback(self, prescription_id: Optional[str] = None) -> list[dict[str, Any]]:
        """Retrieve feedback reports, optionally filtered by prescription."""
        conn = self._get_connection()
        try:
            if prescription_id:
                cursor = conn.execute(
                    "SELECT * FROM feedback_reports WHERE prescription_id = ? ORDER BY created_at DESC",
                    (prescription_id,),
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM feedback_reports ORDER BY created_at DESC"
                )
            return [dict(r) for r in cursor.fetchall()]
        finally:
            self._close_conn(conn)

