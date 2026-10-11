"""
dosely/repository.py
====================
SQLite persistence repository for prescription workflows.
Manages storage and status tracking for prescriptions, confirmed data, and schedules.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "prescriptions.db"


class PrescriptionRepository:
    """Manages the prescription records lifecycle in SQLite."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
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
                    )
                    """
                )
        finally:
            conn.close()

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
            conn.close()

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
            conn.close()

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
            conn.close()

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
            conn.close()
