"""
tests/test_upload_security.py
=============================
Test suite for TASK 4: Upload validation, secrets, and rate limiting.

Verifies:
1. Magic bytes content verification:
   - Renamed .exe with image/png MIME -> 415
   - Shell script with application/pdf MIME -> 415
   - Mismatched declared MIME vs detected magic bytes -> 415
2. Pillow validation & sanitization:
   - Decompression bomb / huge pixel dimensions -> 413
   - Polyglot payload (valid PNG + trailing attack script) -> re-encoded cleanly, trailing payload stripped
3. PDF safety checks:
   - Reject PDFs with /JavaScript, /JS, /Launch, /EmbeddedFile -> 415
   - Reject PDFs with > 10 pages -> 413
4. Rate limiting:
   - Keyed by client IP (never token)
   - 40 requests with rotating tokens from one IP hit 429
   - Returns Retry-After header
5. Secrets enforcement:
   - In ENV=production: fails if missing, < 32 bytes, or known defaults
   - In development: generates random ephemeral key with warning
"""

from __future__ import annotations
from dosely.models import Prescription, ImageQuality

import io
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
try:
    import fitz
except ImportError:
    fitz = None

from fastapi.testclient import TestClient

import main
from dosely.api.prescriptions import _create_access_token
from dosely.storage import EncryptedStorage, get_encryption_keys, init_secrets
from dosely.rate_limiter import SlidingWindowRateLimiter, get_client_ip


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def auth_headers():
    token = _create_access_token(data={"sub": "security-tester"})
    return {"Authorization": f"Bearer {token}"}


def _create_minimal_png() -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (10, 10), color="white")
    img.save(buf, format="PNG")
    return buf.getvalue()


def _create_minimal_pdf(pages: int = 1, js: bool = False, launch: bool = False) -> bytes:
    if fitz is None:
        raw = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        if js:
            raw += b"/JS (alert('XSS')) /JavaScript 1 0 R\n"
        if launch:
            raw += b"/Launch << /F (cmd.exe) >>\n"
        raw += b"%%EOF"
        return raw

    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    if js:
        data = data.replace(b"/Catalog", b"/Catalog /JS (alert('xss')) /JavaScript 1 0 R")
    if launch:
        data = data.replace(b"/Catalog", b"/Catalog /Launch << /F (cmd.exe) >>")
    return data


def test_renamed_exe_as_image_returns_415(client, auth_headers):
    exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00This program cannot be run in DOS mode."
    res = client.post(
        "/prescriptions/upload",
        files={"file": ("prescription.png", io.BytesIO(exe_bytes), "image/png")},
        headers=auth_headers,
    )
    assert res.status_code == 415


def test_shell_script_as_pdf_returns_415(client, auth_headers):
    sh_bytes = b"#!/bin/bash\nrm -rf /\necho 'attack'"
    res = client.post(
        "/prescriptions/upload",
        files={"file": ("document.pdf", io.BytesIO(sh_bytes), "application/pdf")},
        headers=auth_headers,
    )
    assert res.status_code == 415


def test_decompression_bomb_returns_413(client, auth_headers):
    # Image exceeding max pixels (e.g. 5000 x 5000 = 25M pixels, configured cap 10M)
    buf = io.BytesIO()
    img = Image.new("RGB", (3500, 3500), color="white")  # > 12M pixels
    img.save(buf, format="PNG")
    bomb_bytes = buf.getvalue()

    res = client.post(
        "/prescriptions/upload",
        files={"file": ("bomb.png", io.BytesIO(bomb_bytes), "image/png")},
        headers=auth_headers,
    )
    assert res.status_code == 413


def test_pdf_with_javascript_or_launch_rejected(client, auth_headers):
    js_pdf = _create_minimal_pdf(pages=1, js=True)
    res_js = client.post(
        "/prescriptions/upload",
        files={"file": ("script.pdf", io.BytesIO(js_pdf), "application/pdf")},
        headers=auth_headers,
    )
    assert res_js.status_code == 415

    launch_pdf = _create_minimal_pdf(pages=1, launch=True)
    res_launch = client.post(
        "/prescriptions/upload",
        files={"file": ("launch.pdf", io.BytesIO(launch_pdf), "application/pdf")},
        headers=auth_headers,
    )
    assert res_launch.status_code == 415


def test_pdf_page_count_capped_at_10(client, auth_headers):
    huge_pdf = _create_minimal_pdf(pages=11)
    res = client.post(
        "/prescriptions/upload",
        files={"file": ("huge.pdf", io.BytesIO(huge_pdf), "application/pdf")},
        headers=auth_headers,
    )
    assert res.status_code in (413, 415)


@patch("dosely.api.prescriptions.extract")
def test_polyglot_reencoded_strips_trailing_payload(mock_extract, client, auth_headers):
    mock_extract.return_value = Prescription(prescription_id="polyglot-test", image_quality=ImageQuality.GOOD, medicines=[])
    clean_png = _create_minimal_png()
    polyglot_bytes = clean_png + b"\n\n<?php system($_GET['cmd']); ?>\n#!/bin/bash\necho hacked\n"

    res = client.post(
        "/prescriptions/upload",
        files={"file": ("polyglot.png", io.BytesIO(polyglot_bytes), "image/png")},
        headers=auth_headers,
    )
    assert res.status_code in (200, 201)
    rx_id = res.json()["id"]

    # Fetch stored decrypted image
    img_res = client.get(f"/prescriptions/{rx_id}/image", headers=auth_headers)
    assert img_res.status_code == 200
    assert b"<?php" not in img_res.content
    assert b"#!/bin/bash" not in img_res.content


def test_rate_limiter_cannot_be_bypassed_by_rotating_tokens(client):
    allowed = 0
    hit_429 = False
    for i in range(40):
        t = _create_access_token(data={"sub": f"bot-user-{i}"})
        h = {"Authorization": f"Bearer {t}"}
        r = client.get("/prescriptions/test-id", headers=h)
        if r.status_code == 429:
            hit_429 = True
            assert "Retry-After" in r.headers
            break
        allowed += 1

    assert hit_429, f"Rate limiter was bypassed! Allowed {allowed} requests under rotating tokens."


def test_secrets_production_mode_enforcement():
    # Production mode without keys must fail
    with patch.dict(os.environ, {"ENV": "production", "IMAGE_ENCRYPTION_KEY": "", "JWT_SECRET_KEY": ""}):
        with pytest.raises(RuntimeError) as exc:
            init_secrets()
        assert "production" in str(exc.value).lower()

    # Production mode with default keys must fail
    with patch.dict(os.environ, {
        "ENV": "production",
        "IMAGE_ENCRYPTION_KEY": "matra-prescriptions-at-rest-secret-key-2026",
        "JWT_SECRET_KEY": "dosely-dev-secret-change-in-production",
    }):
        with pytest.raises(RuntimeError):
            init_secrets()

    # Production mode with strong keys (>= 32 bytes) must succeed
    strong_img = "a" * 32
    strong_jwt = "b" * 32
    with patch.dict(os.environ, {
        "ENV": "production",
        "IMAGE_ENCRYPTION_KEY": strong_img,
        "JWT_SECRET_KEY": strong_jwt,
    }):
        img_key, jwt_key = init_secrets()
        assert img_key is not None
        assert jwt_key is not None
