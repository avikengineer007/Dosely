"""
dosely/storage.py
=================
Storage manager for encrypted prescription images and metadata.
Guarantees images are encrypted at rest on the local file system.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dosely.security.encryption import read_decrypted_file, save_encrypted_file

_STORAGE_DIR = Path(os.getenv("DOSELY_STORAGE_DIR", "data/secure_storage"))
_STORAGE_DIR.mkdir(parents=True, exist_ok=True)


class PrescriptionRecord:
    def __init__(
        self,
        prescription_id: str,
        user_id: str,
        filename: str,
        mime_type: str,
        encrypted_file_path: Path,
        extracted_data: dict[str, Any],
        status: str = "pending_confirmation",
    ) -> None:
        self.prescription_id = prescription_id
        self.user_id = user_id
        self.filename = filename
        self.mime_type = mime_type
        self.encrypted_file_path = encrypted_file_path
        self.extracted_data = extracted_data
        self.status = status  # "pending_confirmation" | "confirmed"
        self.confirmed_data: Optional[dict[str, Any]] = None
        self.schedule: Optional[list[dict[str, Any]]] = None
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.confirmed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prescription_id": self.prescription_id,
            "user_id": self.user_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "status": self.status,
            "extracted_data": self.extracted_data,
            "confirmed_data": self.confirmed_data,
            "schedule": self.schedule,
            "created_at": self.created_at,
            "confirmed_at": self.confirmed_at,
        }


class PrescriptionStorage:
    """Thread-safe storage manager for prescriptions and encrypted images."""

    def __init__(self, base_dir: Path = _STORAGE_DIR) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, PrescriptionRecord] = {}
        self._lock = threading.RLock()

    def store_prescription(
        self,
        prescription_id: str,
        user_id: str,
        filename: str,
        mime_type: str,
        image_bytes: bytes,
        extracted_data: dict[str, Any],
    ) -> PrescriptionRecord:
        with self._lock:
            # 1. Encrypt image bytes and write to secure storage at rest
            enc_filename = f"{prescription_id}.enc"
            enc_path = self.base_dir / enc_filename
            save_encrypted_file(image_bytes, enc_path)

            # 2. Save record in memory index
            record = PrescriptionRecord(
                prescription_id=prescription_id,
                user_id=user_id,
                filename=filename,
                mime_type=mime_type,
                encrypted_file_path=enc_path,
                extracted_data=extracted_data,
                status="pending_confirmation",
            )
            self._records[prescription_id] = record
            return record

    def get_record(self, prescription_id: str) -> Optional[PrescriptionRecord]:
        with self._lock:
            return self._records.get(prescription_id)

    def get_image_bytes(self, prescription_id: str) -> tuple[bytes, str]:
        """Decrypt image from disk and return (raw_bytes, mime_type)."""
        with self._lock:
            record = self._records.get(prescription_id)
            if not record:
                raise KeyError(f"Prescription {prescription_id} not found.")
            decrypted = read_decrypted_file(record.encrypted_file_path)
            return decrypted, record.mime_type

    def confirm_prescription(
        self,
        prescription_id: str,
        confirmed_data: dict[str, Any],
        schedule: list[dict[str, Any]],
    ) -> PrescriptionRecord:
        with self._lock:
            record = self._records.get(prescription_id)
            if not record:
                raise KeyError(f"Prescription {prescription_id} not found.")
            record.status = "confirmed"
            record.confirmed_data = confirmed_data
            record.schedule = schedule
            record.confirmed_at = datetime.now(timezone.utc).isoformat()
            return record

    def list_records(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            records = list(self._records.values())
            if user_id:
                records = [r for r in records if r.user_id == user_id]
            return [r.to_dict() for r in records]

    def clear(self) -> None:
        """Clear memory and test artifacts."""
        with self._lock:
            self._records.clear()


storage_manager = PrescriptionStorage()
