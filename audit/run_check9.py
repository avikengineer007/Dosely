"""
audit/run_check9.py
===================
Independent QA Audit: CHECK 9 - Security Sweep
"""

from __future__ import annotations

import io
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main
from dosely.api.prescriptions import _create_access_token
from fastapi.testclient import TestClient

client = TestClient(main.app)

print("=" * 80)
print("CHECK 9: SECURITY SWEEP AUDIT")
print("=" * 80)

# --- 1. Upload handling: Magic bytes verification ---
print("\n--- 1. Upload Handling & Content Verification ---")
token = _create_access_token(data={"sub": "security-tester"})
headers = {"Authorization": f"Bearer {token}"}

# 1a. Renamed Windows Executable (.exe payload sent with image/png MIME)
exe_bytes = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00This program cannot be run in DOS mode."
res_exe = client.post(
    "/prescriptions/upload",
    files={"file": ("prescription.png", io.BytesIO(exe_bytes), "image/png")},
    headers=headers,
)
print(f"Renamed .exe with image/png header upload status: {res_exe.status_code}")
print(f"  Was renamed executable accepted by upload endpoint? {res_exe.status_code == 200}")

# 1b. Polyglot / shell script sent with application/pdf MIME
sh_bytes = b"#!/bin/bash\nrm -rf /\necho 'attack'"
res_sh = client.post(
    "/prescriptions/upload",
    files={"file": ("document.pdf", io.BytesIO(sh_bytes), "application/pdf")},
    headers=headers,
)
print(f"Shell script with application/pdf header upload status: {res_sh.status_code}")
print(f"  Was shell script accepted by upload endpoint? {res_sh.status_code == 200}")

# 1c. Malformed PDF
malformed_pdf = b"%PDF-1.4\ncorrupted content without xref or trailer\x00\x00\x00"
res_bad_pdf = client.post(
    "/prescriptions/upload",
    files={"file": ("corrupt.pdf", io.BytesIO(malformed_pdf), "application/pdf")},
    headers=headers,
)
print(f"Malformed PDF upload status: {res_bad_pdf.status_code}")

# --- 2. Rate limiter bypass test ---
print("\n--- 2. Rate Limiter Bypass Test ---")
# Endpoint /prescriptions has limit 30 requests per 60s
# Can we bypass it by rotating tokens?
print("Testing rate limiter bypass via token rotation...")
allowed_count = 0
for i in range(40):
    user_token = _create_access_token(data={"sub": f"bot-user-{i}"})
    u_headers = {"Authorization": f"Bearer {user_token}"}
    r = client.get("/prescriptions/some-id", headers=u_headers)
    if r.status_code != 429:
        allowed_count += 1

print(f"Requests allowed under rotating tokens: {allowed_count} / 40 (Limit per IP was 30)")
if allowed_count > 30:
    print("  RATE LIMITER BYPASSED: Key relies on Authorization header, allowing token rotation bypass!")

# --- 3. Secrets in code and git history ---
print("\n--- 3. Secrets in Code & Git History ---")
git_cmd = subprocess.run(["git", "log", "-n", "10", "--pretty=format:%H %s"], capture_output=True, text=True, encoding="utf-8", errors="ignore", cwd=str(ROOT))
print("Recent commits:")
for line in git_cmd.stdout.splitlines()[:5]:
    print(" ", line)

env_file = ROOT / ".env"
print(f".env file exists: {env_file.exists()}")
gitignore_text = (ROOT / ".gitignore").read_text()
print(f".env git-ignored: {'.env' in gitignore_text}")

# Check hardcoded fallbacks in source
auth_src = (ROOT / "dosely" / "auth.py").read_text()
storage_src = (ROOT / "dosely" / "storage.py").read_text()
config_src = (ROOT / "dosely" / "config.py").read_text()

has_jwt_fallback = "matra-dev-secret" in auth_src or "dosely-dev-secret" in config_src
has_fernet_fallback = "matra-prescriptions-at-rest" in storage_src
print(f"Hardcoded JWT fallback present in code: {has_jwt_fallback}")
print(f"Hardcoded Fernet key fallback present in code: {has_fernet_fallback}")
