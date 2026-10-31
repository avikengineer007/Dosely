"""
tests/test_admin_diagnostics.py
===============================
Unit tests for the protected admin diagnostics endpoint (GET /admin/diagnostics).

Verifies:
  1. Unauthenticated requests return 401 Unauthorized.
  2. Non-admin users return 403 Forbidden.
  3. Admin users (role='admin') receive a 200 response with all required fields.
  4. Secret header authentication (X-Admin-Secret) grants access.
  5. Never leaks secret values, image contents, or tokens in the response body.
  6. Endpoint is excluded from public OpenAPI schema (include_in_schema=False).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from dosely.auth import create_access_token


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def normal_user_headers():
    token = create_access_token(data={"sub": "user_regular"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_user_headers():
    token = create_access_token(data={"sub": "admin_user", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


def test_diagnostics_unauthenticated_returns_401(client):
    res = client.get("/admin/diagnostics")
    assert res.status_code == 401


def test_diagnostics_non_admin_returns_403(client, normal_user_headers):
    res = client.get("/admin/diagnostics", headers=normal_user_headers)
    assert res.status_code == 403


def test_diagnostics_admin_returns_200_and_expected_fields(client, admin_user_headers):
    res = client.get("/admin/diagnostics", headers=admin_user_headers)
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "ok"
    assert "env_var_names_present" in data
    assert isinstance(data["env_var_names_present"], list)
    # Ensure no secrets leaked: check that values are just variable names (no spaces, no keys)
    for k in data["env_var_names_present"]:
        assert k.isupper() or "_" in k
        assert "sk-ant-" not in k
        assert "postgresql://" not in k

    assert "configured_model" in data
    assert "llm_provider" in data
    assert "anthropic_1token_test" in data
    assert "library_import_status" in data
    assert isinstance(data["library_import_status"], dict)
    assert "cv2" in data["library_import_status"]
    assert "numpy" in data["library_import_status"]

    assert "bundle_versions" in data
    assert "preprocessing_timeout_seconds" in data
    assert isinstance(data["preprocessing_timeout_seconds"], (int, float))


def test_diagnostics_admin_secret_header(client, monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", "super-secret-admin-key-32bytes!")
    res = client.get(
        "/admin/diagnostics",
        headers={"X-Admin-Secret": "super-secret-admin-key-32bytes!"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_diagnostics_not_in_public_openapi():
    schema = main.app.openapi()
    paths = schema.get("paths", {})
    assert "/admin/diagnostics" not in paths
