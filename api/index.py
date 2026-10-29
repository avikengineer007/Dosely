"""
Vercel Serverless Entrypoint (api/index.py)
Re-exports the FastAPI `app` from `main.py`.
"""

from main import app

__all__ = ["app"]
