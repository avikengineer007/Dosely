"""
tests/test_vercel_cloud_deployment.py
=====================================
Tests for Vercel Option B serverless deployment:
  - Neon Postgres SQL conversion & HTTP execution
  - Turso (libSQL) HTTP execution
  - CloudBlobStore encrypted file replication & cold-start recovery
  - /api/cron/purge authentication & execution
"""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

import main
from dosely.cloud_db import (
    CloudBlobStore,
    CloudConnection,
    _convert_sqlite_to_postgres_sql,
    get_cloud_db_config,
)
from dosely.storage import EncryptedStorage


def test_convert_sqlite_to_postgres_sql():
    sql = "SELECT * FROM prescriptions WHERE id = ? AND user_id = ?"
    pg = _convert_sqlite_to_postgres_sql(sql)
    assert pg == "SELECT * FROM prescriptions WHERE id = $1 AND user_id = $2"

    upsert = (
        "INSERT INTO encrypted_blobs (blob_key, ciphertext_b64, updated_at) "
        "VALUES (?, ?, ?) ON CONFLICT(blob_key) DO UPDATE SET "
        "ciphertext_b64 = excluded.ciphertext_b64, updated_at = excluded.updated_at"
    )
    pg_upsert = _convert_sqlite_to_postgres_sql(upsert)
    assert "INSERT INTO encrypted_blobs" in pg_upsert
    assert "ON CONFLICT(blob_key) DO UPDATE SET" in pg_upsert
    assert "$1, $2, $3" in pg_upsert


def test_get_cloud_db_config_neon_and_turso(monkeypatch):
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    assert get_cloud_db_config() is None

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@ep-cool-123.us-east-2.aws.neon.tech/neondb")
    cfg = get_cloud_db_config()
    assert cfg is not None
    assert cfg["provider"] == "neon"

    monkeypatch.setenv("TURSO_DATABASE_URL", "libsql://dosely-user.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "turso-jwt-token")
    cfg_turso = get_cloud_db_config()
    assert cfg_turso is not None
    assert cfg_turso["provider"] == "turso"
    assert cfg_turso["url"] == "https://dosely-user.turso.io"


def test_encrypted_storage_cold_start_recovery_from_cloud_blobs(tmp_path: Path):
    """
    Simulate Vercel cold start:
    1. Instance A encrypts and saves a prescription image (written to tmp_a and replicated to cloud_blobs).
    2. Instance B has an empty local /tmp directory (tmp_b), loads the prescription by ID,
       fetches ciphertext from cloud_blobs, and decrypts it with the same Fernet key.
    """
    shared_key = Fernet.generate_key()
    fake_cloud_table: dict[str, bytes] = {}

    mock_blob_store = MagicMock(spec=CloudBlobStore)
    mock_blob_store.put.side_effect = lambda k, v: fake_cloud_table.__setitem__(k, v)
    mock_blob_store.get.side_effect = lambda k: fake_cloud_table.get(k)
    mock_blob_store.delete_prefix.side_effect = lambda stem: bool(
        fake_cloud_table.pop(f"{stem}.enc", None)
    )

    dir_a = tmp_path / "instance_a"
    dir_b = tmp_path / "instance_b"

    storage_a = EncryptedStorage(storage_dir=dir_a, keys=[shared_key])
    storage_a._cloud_blobs = mock_blob_store

    raw_secret_prescription = b"Rx: Metformin 500mg BD"
    storage_a.save("rx-serverless-1", raw_secret_prescription)

    # Verify ciphertext in cloud table is encrypted (not plaintext)
    assert "rx-serverless-1.enc" in fake_cloud_table
    assert b"Metformin" not in fake_cloud_table["rx-serverless-1.enc"]

    # Instance B starts with empty local /tmp directory
    storage_b = EncryptedStorage(storage_dir=dir_b, keys=[shared_key])
    storage_b._cloud_blobs = mock_blob_store

    recovered = storage_b.load("rx-serverless-1")
    assert recovered == raw_secret_prescription

    # Delete on Instance B removes from cloud table
    assert storage_b.delete("rx-serverless-1") is True
    assert "rx-serverless-1.enc" not in fake_cloud_table


def test_vercel_cron_purge_endpoint(monkeypatch):
    client = TestClient(main.app)

    # Without CRON_SECRET configured -> 401
    monkeypatch.delenv("CRON_SECRET", raising=False)
    res_unconfigured = client.get("/api/cron/purge")
    assert res_unconfigured.status_code == 401

    # With CRON_SECRET configured, wrong header -> 401
    monkeypatch.setenv("CRON_SECRET", "my-vercel-cron-secret-12345")
    res_wrong = client.get("/api/cron/purge", headers={"Authorization": "Bearer wrong-secret"})
    assert res_wrong.status_code == 401

    # With matching CRON_SECRET -> 200
    res_ok = client.get(
        "/api/cron/purge",
        headers={"Authorization": "Bearer my-vercel-cron-secret-12345"},
    )
    assert res_ok.status_code == 200
    body = res_ok.json()
    assert "purged_count" in body
    assert "retention_days" in body


def test_vercel_rewrite_path_normalization():
    """Verify Vercel rewrite URLs (/main.py?__vc_path=...) route cleanly to /, /app, and API endpoints."""
    client = TestClient(main.app)

    r_root = client.get("/main.py?__vc_path=/")
    assert r_root.status_code == 200
    assert "Dosely" in r_root.text
    assert "unsafe-inline" in r_root.headers.get("Content-Security-Policy", "")

    r_bare = client.get("/main.py")
    assert r_bare.status_code == 200
    assert "Dosely" in r_bare.text

    r_app = client.get("/main.py?__vc_path=/app")
    assert r_app.status_code == 200
    assert 'id="root"' in r_app.text
    assert "camera=(self)" in r_app.headers.get("Permissions-Policy", "")

    r_health = client.get("/main.py?__vc_path=/api/v1/health")
    assert r_health.status_code == 200
    assert r_health.json() == {"status": "ok"}

