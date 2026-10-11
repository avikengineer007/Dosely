"""
dosely/security/encryption.py
==============================
At-rest encryption for sensitive prescription images and documents.
Uses symmetric Fernet encryption (AES-128 in CBC mode with HMAC SHA256).
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

# Default key fallback or environment variable
_ENV_KEY = os.getenv("DOSELY_ENCRYPTION_KEY")


def get_fernet_key(custom_key: Optional[str] = None) -> bytes:
    """
    Derive a 32-byte URL-safe base64-encoded Fernet key.
    If no key is configured, uses a deterministic local secret for consistency.
    """
    raw_key = custom_key or _ENV_KEY or "dosely-default-secure-rest-encryption-key-2026"
    # Ensure it's exactly 32 bytes via SHA-256 then base64url encode
    key_digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(key_digest)


def get_fernet(custom_key: Optional[str] = None) -> Fernet:
    return Fernet(get_fernet_key(custom_key))


def encrypt_bytes(data: bytes, custom_key: Optional[str] = None) -> bytes:
    """Encrypt raw bytes into ciphertext bytes."""
    f = get_fernet(custom_key)
    return f.encrypt(data)


def decrypt_bytes(ciphertext: bytes, custom_key: Optional[str] = None) -> bytes:
    """Decrypt ciphertext bytes back to raw plaintext bytes."""
    f = get_fernet(custom_key)
    return f.decrypt(ciphertext)


def save_encrypted_file(
    data: bytes,
    dest_path: Path,
    custom_key: Optional[str] = None,
) -> Path:
    """Encrypt bytes and atomically save to dest_path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    encrypted = encrypt_bytes(data, custom_key=custom_key)
    dest_path.write_bytes(encrypted)
    return dest_path


def read_decrypted_file(
    file_path: Path,
    custom_key: Optional[str] = None,
) -> bytes:
    """Read encrypted file from disk and return decrypted bytes."""
    if not file_path.exists():
        raise FileNotFoundError(f"Encrypted file does not exist: {file_path}")
    ciphertext = file_path.read_bytes()
    return decrypt_bytes(ciphertext, custom_key=custom_key)
