"""
audit/run_check8.py
===================
Independent QA Audit: CHECK 8 - Landing Page and Headers
"""

from __future__ import annotations

import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main
from fastapi.testclient import TestClient

client = TestClient(main.app)

print("=" * 80)
print("CHECK 8: LANDING PAGE AND HEADERS AUDIT")
print("=" * 80)

# 1. Fetch / and /app and compare response headers
r_root = client.get("/")
r_app = client.get("/app")
r_api = client.get("/docs")

print(f"GET / status: {r_root.status_code}")
print(f"GET /app status: {r_app.status_code}")

print("\n--- CSP Headers ---")
csp_root = r_root.headers.get("Content-Security-Policy", "")
csp_app = r_app.headers.get("Content-Security-Policy", "")
csp_api = r_api.headers.get("Content-Security-Policy", "")
print(f"CSP for '/':\n  {csp_root}")
print(f"CSP for '/app':\n  {csp_app}")
print(f"CSP for '/docs':\n  {csp_api}")

# Verify CDN allowances
print(f"  / allows cdnjs: {'https://cdnjs.cloudflare.com' in csp_root}")
print(f"  / allows googleapis: {'https://fonts.googleapis.com' in csp_root}")
print(f"  / allows gstatic: {'https://fonts.gstatic.com' in csp_root}")
print(f"  /app blocks cdnjs: {'https://cdnjs.cloudflare.com' not in csp_app}")
print(f"  /api blocks cdnjs: {'https://cdnjs.cloudflare.com' not in csp_api}")

# 2. Check dosely.html source code for SRI, remote images, scripts, fetch/XHR
html_path = ROOT / "dosely.html"
html_content = html_path.read_text(encoding="utf-8")

print("\n--- Landing Page HTML Checks ---")
# Remote images: <img src="http...">
remote_images = re.findall(r'<img[^>]+src=["\'](https?://[^"\']+)["\']', html_content, re.IGNORECASE)
print(f"Remote images count: {len(remote_images)}")

# Third party scripts
script_tags = re.findall(r'<script[^>]*src=["\']([^"\']+)["\'][^>]*>', html_content, re.IGNORECASE)
print(f"Script tags with src: {script_tags}")

# Check SRI and crossorigin on script tags
has_sri = bool(re.search(r'integrity=["\'][^"\']+["\']', html_content, re.IGNORECASE))
has_crossorigin = bool(re.search(r'crossorigin=["\'][^"\']+["\']', html_content, re.IGNORECASE))
print(f"Script tag has Subresource Integrity (SRI) hash: {has_sri}")
print(f"Script tag has crossorigin attribute: {has_crossorigin}")

# Check for fetch / XMLHttpRequest in landing page
has_fetch = "fetch(" in html_content
has_xhr = "XMLHttpRequest" in html_content
print(f"Contains fetch(): {has_fetch}")
print(f"Contains XMLHttpRequest: {has_xhr}")

# 3. Check security headers on both routes:
# X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy
print("\n--- Baseline Security Headers ---")
for route_name, resp in [("/", r_root), ("/app", r_app)]:
    print(f"Route: {route_name}")
    print(f"  X-Content-Type-Options: {resp.headers.get('X-Content-Type-Options')}")
    print(f"  X-Frame-Options: {resp.headers.get('X-Frame-Options')}")
    print(f"  Referrer-Policy: {resp.headers.get('Referrer-Policy')}")
    print(f"  Permissions-Policy: {resp.headers.get('Permissions-Policy')}")

# 4. Routing verification
print("\n--- Routing Verification ---")
r_deep = client.get("/app/schedule/daily-view")
r_unknown = client.get("/unknown-nonexistent-path-12345")
print(f"GET /app/schedule/daily-view status: {r_deep.status_code} | contains root: {'id=\"root\"' in r_deep.text}")
print(f"GET /unknown-nonexistent-path-12345 status: {r_unknown.status_code}")

# 5. Medical disclaimer presence
print("\n--- Medical Disclaimer Check ---")
landing_disclaimer = "Dosely is not medical advice" in html_content
print(f"Landing page footer contains medical disclaimer: {landing_disclaimer}")

app_html = (ROOT / "frontend" / "app.html").read_text(encoding="utf-8")
confirm_jsx = (ROOT / "frontend" / "src" / "ConfirmScreen.jsx").read_text(encoding="utf-8")
print(f"ConfirmScreen.jsx contains medical disclaimer: {'This is not medical advice' in confirm_jsx or 'Dosely is not medical advice' in confirm_jsx}")

# 6. WebGL / Reduced motion check in dosely.html
has_reduced_motion = "prefers-reduced-motion" in html_content
has_webgl_fallback = "webgl" in html_content.lower() or "canvas" in html_content.lower()
print(f"Contains prefers-reduced-motion CSS: {has_reduced_motion}")
print(f"Contains WebGL handling / canvas: {has_webgl_fallback}")
