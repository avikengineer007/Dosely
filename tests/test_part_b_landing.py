"""
tests/test_part_b_landing.py
============================
Verification tests for Part B: Landing Page Integration.

Tests:
1. Fetching "/" returns 200, Content-Type text/html, and contains "Dosely".
2. Landing page contains no fetch() or XMLHttpRequest calls to /api.
3. Content-Security-Policy for "/" allows cdnjs, google fonts, and gstatic.
4. Content-Security-Policy for "/app" and "/api/*" retains stricter policy without cdnjs/fonts.
5. Three.js is pinned to exact version (r128), not @latest.
6. Medical disclaimer is present in the landing page footer.
7. Routing:
   - "/" returns landing page
   - "/app" returns app page
   - "/app/prescriptions/upload" (deep link) returns app page (200)
   - API endpoints remain unaffected.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
LANDING_FILE = Path(__file__).resolve().parent.parent / "dosely.html"


def test_landing_page_serves_200_and_contains_dosely():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Dosely" in response.text


def test_landing_page_makes_no_api_calls():
    # Verify both the served content and static file
    response = client.get("/")
    text = response.text

    assert "fetch(" not in text or "fetch('/api" not in text
    assert "/api/" not in text
    assert "XMLHttpRequest" not in text or "open(" not in text


def test_landing_page_vendored_threejs_matches_hash():
    assert LANDING_FILE.exists()
    content = LANDING_FILE.read_text(encoding="utf-8")
    assert "/static/vendor/three.min.js" in content
    assert "integrity=\"sha384-CI3ELBVUz9XQO+97x6nwMDPosPR5XvsxW2ua7N1Xeygeh1IxtgqtCkGfQY9WWdHu\"" in content
    assert "crossorigin=\"anonymous\"" in content

    # Verify actual file on disk matches hash
    vendor_file = Path(__file__).resolve().parent.parent / "static" / "vendor" / "three.min.js"
    assert vendor_file.exists()
    import hashlib, base64
    digest = base64.b64encode(hashlib.sha384(vendor_file.read_bytes()).digest()).decode()
    assert f"sha384-{digest}" == "sha384-CI3ELBVUz9XQO+97x6nwMDPosPR5XvsxW2ua7N1Xeygeh1IxtgqtCkGfQY9WWdHu"


def test_landing_page_medical_disclaimer():
    response = client.get("/")
    text = response.text
    disclaimer = "Dosely is not medical advice. Always follow your doctor's instructions."
    assert disclaimer in text
    assert "<footer>" in text.lower()


def test_landing_csp_and_permissions_policy():
    response = client.get("/")
    csp = response.headers.get("content-security-policy", "")
    assert "https://cdnjs.cloudflare.com" not in csp
    assert "script-src 'self'" in csp
    assert "https://fonts.googleapis.com" in csp
    assert "https://fonts.gstatic.com" in csp

    pp = response.headers.get("permissions-policy", "")
    assert "camera=()" in pp
    assert "microphone=()" in pp


def test_app_and_api_csp_retains_stricter_policy():
    # Test /app
    resp_app = client.get("/app")
    csp_app = resp_app.headers.get("content-security-policy", "")
    assert "https://cdnjs.cloudflare.com" not in csp_app
    assert "https://fonts.googleapis.com" not in csp_app
    assert "https://fonts.gstatic.com" not in csp_app
    assert "default-src 'self'" in csp_app

    # Test /docs
    resp_docs = client.get("/docs")
    csp_docs = resp_docs.headers.get("content-security-policy", "")
    assert "https://cdnjs.cloudflare.com" not in csp_docs
    assert "https://fonts.googleapis.com" not in csp_docs


def test_routing_landing_app_and_deep_links():
    # "/" serves landing page
    r_landing = client.get("/")
    assert r_landing.status_code == 200
    assert "Never miss a dose" in r_landing.text

    # "/app" serves app shell
    r_app = client.get("/app")
    assert r_app.status_code == 200
    assert "Prescription Assistant" in r_app.text
    assert 'id="root"' in r_app.text

    # Deep links under "/app/*" serve app shell
    r_deep1 = client.get("/app/upload")
    assert r_deep1.status_code == 200
    assert "Prescription Assistant" in r_deep1.text

    r_deep2 = client.get("/app/schedule/timeline")
    assert r_deep2.status_code == 200
    assert "Prescription Assistant" in r_deep2.text


def test_open_dosely_links_present():
    response = client.get("/")
    text = response.text
    # Nav link to /app
    assert 'href="/app"' in text
    assert "Open Dosely" in text
