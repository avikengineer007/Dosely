"""
scripts/reencrypt_data.py
=========================
Key rotation & data remediation utility for encrypted prescription files at rest.

Reads all .enc files in the storage directory, decrypts them using legacy/rotation keys
via MultiFernet, and re-encrypts them with the current primary encryption key.
"""

from __future__ import annotations

import base64
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cryptography.fernet import Fernet, MultiFernet
from dosely.storage import STORAGE_DIR, init_secrets

LEGACY_DEFAULTS = [
    "matra-prescriptions-at-rest-secret-key-2026",
]


def _derive_fernet_key(raw_str: str) -> bytes:
    digest = hashlib.sha256(raw_str.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def reencrypt_all(storage_dir: Path = STORAGE_DIR) -> int:
    storage_dir = Path(storage_dir)
    if not storage_dir.exists():
        print(f"Storage directory does not exist: {storage_dir}")
        return 0

    primary_key, _ = init_secrets()
    candidate_keys = [primary_key] + [_derive_fernet_key(k) for k in LEGACY_DEFAULTS]

    multi_fernet = MultiFernet([Fernet(k) for k in candidate_keys])
    primary_fernet = Fernet(primary_key)

    files = list(storage_dir.glob("*.enc"))
    reencrypted = 0

    for f in files:
        data = f.read_bytes()
        try:
            decrypted = multi_fernet.decrypt(data)
            new_encrypted = primary_fernet.encrypt(decrypted)
            f.write_bytes(new_encrypted)
            reencrypted += 1
        except Exception as exc:
            print(f"Warning: Failed to re-encrypt {f.name}: {exc}")

    print(f"Re-encryption complete: {reencrypted} of {len(files)} files re-encrypted.")
    return reencrypted


if __name__ == "__main__":
    reencrypt_all()
