"""
dosely/storage.py
=================
Encrypted image storage at rest for prescription documents using MultiFernet.
Enforces Global Safety Rule 5: Encrypt stored images at rest with strict key validation.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from pathlib import Path
from typing import Optional, Sequence

from cryptography.fernet import Fernet, MultiFernet

logger = logging.getLogger(__name__)

_DEFAULT_STORAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "encrypted_uploads"
STORAGE_DIR = Path(os.getenv("STORAGE_DIR", str(_DEFAULT_STORAGE_DIR)))

COMPROMISED_DEFAULT_HASHES = frozenset({
    "252442559fd0735e667f4f4425751372bab2beb1cc0cf47b3340a3941d45d26a",  # legacy img default
    "8bdba8190ee23a8a74d48576db798f521fecf0a42dc544f9f4a4629980bb6e04",  # legacy auth default
    "9d75d55166f5e97bf896dbb34d9a11dd911ed82668dc33d7557a267ae74f4886",  # legacy config default
})

_ephemeral_img_key: Optional[bytes] = None
_ephemeral_jwt_key: Optional[str] = None
_ephemeral_warned: bool = False


def _is_compromised(key_str: str) -> bool:
    if not key_str:
        return True
    digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()
    return digest in COMPROMISED_DEFAULT_HASHES


def init_secrets() -> tuple[bytes, str]:
    """
    Validate and return (fernet_image_key, jwt_secret_key).

    In production (ENV=production):
      Fails loudly if either key is missing, < 32 bytes, or equals any compromised default.
    In development:
      If keys are missing or compromised, generates secure random ephemeral keys
      and logs one warning that stored data will not survive a restart.
    """
    global _ephemeral_img_key, _ephemeral_jwt_key, _ephemeral_warned

    env = os.getenv("ENV", "development").lower()
    raw_img = os.getenv("IMAGE_ENCRYPTION_KEY", "").strip()
    raw_jwt = os.getenv("JWT_SECRET_KEY", "").strip()

    is_img_bad = not raw_img or len(raw_img) < 32 or _is_compromised(raw_img)
    is_jwt_bad = not raw_jwt or len(raw_jwt) < 32 or _is_compromised(raw_jwt)

    if env == "production":
        if is_img_bad:
            raise RuntimeError(
                "Production configuration error: IMAGE_ENCRYPTION_KEY is missing, "
                "shorter than 32 bytes, or equals a compromised default string."
            )
        if is_jwt_bad:
            raise RuntimeError(
                "Production configuration error: JWT_SECRET_KEY is missing, "
                "shorter than 32 bytes, or equals a compromised default string."
            )
        img_key = base64.urlsafe_b64encode(hashlib.sha256(raw_img.encode("utf-8")).digest())
        return img_key, raw_jwt

    # Development mode
    if is_img_bad:
        if _ephemeral_img_key is None:
            _ephemeral_img_key = Fernet.generate_key()
        img_key = _ephemeral_img_key
    else:
        img_key = base64.urlsafe_b64encode(hashlib.sha256(raw_img.encode("utf-8")).digest())

    if is_jwt_bad:
        if _ephemeral_jwt_key is None:
            _ephemeral_jwt_key = secrets.token_urlsafe(32)
        jwt_key = _ephemeral_jwt_key
    else:
        jwt_key = raw_jwt

    if (is_img_bad or is_jwt_bad) and not _ephemeral_warned:
        _ephemeral_warned = True
        logger.warning(
            "Using random ephemeral encryption/JWT key for development. "
            "Stored data will not survive a restart."
        )

    return img_key, jwt_key


def get_encryption_keys() -> list[bytes]:
    """
    Return all valid Fernet keys for MultiFernet rotation.
    Primary key is first; fallback keys from IMAGE_ENCRYPTION_KEYS follow.
    """
    primary, _ = init_secrets()
    keys = [primary]

    extra_keys_str = os.getenv("IMAGE_ENCRYPTION_KEYS", "").strip()
    if extra_keys_str:
        for k in extra_keys_str.split(","):
            k = k.strip()
            if k and len(k) >= 32 and k not in COMPROMISED_DEFAULTS:
                extra_fernet = base64.urlsafe_b64encode(hashlib.sha256(k.encode("utf-8")).digest())
                if extra_fernet not in keys:
                    keys.append(extra_fernet)
    return keys


class EncryptedStorage:
    """
    Symmetric encryption engine for persisting image and PDF bytes at rest.
    Uses MultiFernet to support seamless key rotation.
    """

    def __init__(
        self,
        storage_dir: Path = STORAGE_DIR,
        keys: Optional[Sequence[bytes]] = None,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        key_list = list(keys) if keys else get_encryption_keys()
        self._keys = key_list
        self._cipher = MultiFernet([Fernet(k) for k in key_list])

    def save(self, prescription_id: str, raw_bytes: bytes, extension: str = "bin") -> str:
        """
        Encrypt raw bytes using primary key and write to storage directory.
        Always uses a synthetic UUID-based filename, never client filename.
        """
        encrypted_data = self._cipher.encrypt(raw_bytes)
        filename = f"{prescription_id}.enc"
        file_path = self.storage_dir / filename
        file_path.write_bytes(encrypted_data)
        return str(file_path)

    def load(self, file_path_or_id: str) -> bytes:
        """
        Read encrypted file and decrypt to plaintext bytes.
        MultiFernet tries the primary key, then all rotation fallback keys.
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
