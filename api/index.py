"""
Vercel Serverless Entrypoint (api/index.py)
Re-exports the FastAPI `app` from `main.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is at the front of sys.path
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from main import app

__all__ = ["app"]
