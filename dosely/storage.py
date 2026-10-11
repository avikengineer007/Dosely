"""
dosely/storage.py
=================
Encrypted image storage at rest for prescription documents.
Enforces Global Safety Rule 5: Encrypt stored images at rest.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

_DEFAULT_STORAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "encrypted_uploads"
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", str(_DEFAULT_STORAGE_DIR)))


def _get_encryption_key() -> bytes:
    """
    Retrieve or derive a Fernet-compatible 32-byte urlsafe base64 key.
    """
    raw_key = os.getenv("IMAGE_ENCRYPTION_KEY")
    if not raw_key:
        # Stable dev fallback key
        raw_key = "matra-prescriptions-at-rest-secret-key-2026"
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class EncryptedStorage:
    """Symmetric encryption engine for persisting image and PDF bytes at rest."""

    def __init__(self, storage_dir: Path = STORAGE_DIR, key: Optional[bytes] = None) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._key = key or _get_encryption_key()
        self._cipher = Fernet(self._key)

    def save(self, prescription_id: str, raw_bytes: bytes, extension: str = "bin") -> str:
        """
        Encrypt raw bytes and write to storage directory.
        Returns the absolute file path of the encrypted file.
        """
        encrypted_data = self._cipher.encrypt(raw_bytes)
        filename = f"{prescription_id}.enc"
        file_path = self.storage_dir / filename
        file_path.write_bytes(encrypted_data)
        return str(file_path)

    def load(self, file_path_or_id: str) -> bytes:
        """
        Read encrypted file and decrypt to original bytes.
        """
        path = Path(file_path_or_id)
        if not path.exists():
            candidate = self.storage_dir / f"{file_path_or_id}.enc"
            if candidate.exists():
                path = candidate
            else:
                raise FileNotFoundError(f"Encrypted file not found: {file_path_or_id}")

        encrypted_data = path.read_bytes()
        return self._cipher.decrypt(encrypted_data)

    def delete(self, file_path_or_id: str) -> bool:
        """Safely delete encrypted file."""
        path = Path(file_path_or_id)
        if not path.exists():
            candidate = self.storage_dir / f"{file_path_or_id}.enc"
            if candidate.exists():
                path = candidate
        if path.exists():
            path.unlink()
            return True
        return False
