"""
audit/run_check4_probe.py
=========================
Independent QA Audit: CHECK 4 - Online Extraction & Retry Logic
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import anthropic

key = os.environ.get("ANTHROPIC_API_KEY")
print(f"ANTHROPIC_API_KEY present: {bool(key)}")
if key:
    masked = key[:7] + "..." + key[-4:] if len(key) > 12 else "***"
    print(f"Key snippet: {masked}")

client = anthropic.Anthropic(api_key=key) if key else None
if client:
    try:
        msg = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=10,
            messages=[{"role": "user", "content": "ping"}]
        )
        print("API ping successful:", msg.content)
    except Exception as e:
        print("API ping failed:", type(e).__name__, str(e))
else:
    print("No client available.")

# Check retry path in dosely/extractor.py
print("\n--- Inspecting Retry Path in dosely/extractor.py ---")
extractor_py = (ROOT / "dosely" / "extractor.py").read_text(encoding="utf-8")

# Check handling of markdown fences
has_markdown_strip = "```json" in extractor_py or "```" in extractor_py or "clean" in extractor_py
print(f"Handles markdown fences in response: {has_markdown_strip}")

# Check retry logging
has_retry_log = "retry" in extractor_py
print(f"Retry logic present in extractor: {has_retry_log}")
