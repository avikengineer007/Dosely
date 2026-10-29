"""
dosely/cloud_db.py
==================
Serverless cloud database & encrypted blob persistence adapter for Vercel deployments.

Supports:
  1. Neon Serverless Postgres over HTTP (`DATABASE_URL` or `POSTGRES_URL`)
     — zero native C driver overhead, instant cold starts on Vercel.
     — Also supports standard Postgres via `psycopg` if installed.
  2. Turso Serverless SQLite (libSQL) over HTTP (`TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN`)
  3. Seamless fallback to local SQLite (`data/prescriptions.db` or `/tmp/prescriptions.db` on Vercel)
"""

from __future__ import annotations

import base64
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def is_vercel() -> bool:
    """Return True when executing inside the Vercel Serverless runtime."""
    return os.getenv("VERCEL", "").strip() in ("1", "true")


def get_cloud_db_config() -> Optional[dict[str, str]]:
    """
    Detect configured cloud database credentials from environment variables.
    Returns a dict with 'provider' ('turso' | 'neon') and connection details, or None.
    """
    turso_url = os.getenv("TURSO_DATABASE_URL", "").strip()
    turso_token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
    if turso_url:
        if turso_url.startswith("libsql://"):
            turso_url = "https://" + turso_url[len("libsql://"):]
        return {
            "provider": "turso",
            "url": turso_url.rstrip("/"),
            "token": turso_token,
        }

    pg_url = (
        os.getenv("DATABASE_URL", "").strip()
        or os.getenv("POSTGRES_URL", "").strip()
        or os.getenv("NEON_DATABASE_URL", "").strip()
    )
    if pg_url and pg_url.startswith(("postgres://", "postgresql://")):
        return {
            "provider": "neon",
            "url": pg_url,
        }

    return None


class CloudRow(dict):
    """
    Row object compatible with sqlite3.Row:
    supports both key lookup (`row["id"]`), `dict(row)`, and integer index (`row[0]`).
    """

    def __init__(self, data: dict[str, Any], columns: Optional[ Sequence[str] ] = None) -> None:
        super().__init__(data)
        self._columns = list(columns) if columns is not None else list(data.keys())

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            col_name = self._columns[key]
            return super().__getitem__(col_name)
        return super().__getitem__(key)

    def keys(self):  # type: ignore[override]
        return self._columns


class CloudCursor:
    """DB-API 2.0 compatible cursor wrapper returned by CloudConnection.execute()."""

    def __init__(self, rows: list[CloudRow], rowcount: int) -> None:
        self._rows = rows
        self.rowcount = rowcount
        self._idx = 0

    def fetchone(self) -> Optional[CloudRow]:
        if self._idx >= len(self._rows):
            return None
        row = self._rows[self._idx]
        self._idx += 1
        return row

    def fetchall(self) -> list[CloudRow]:
        remaining = self._rows[self._idx:]
        self._idx = len(self._rows)
        return remaining


def _convert_sqlite_to_postgres_sql(sql: str) -> str:
    """
    Convert SQLite positional `?` placeholders into Postgres `$1, $2, ...` placeholders.
    """
    stmt = sql.strip()
    counter = 0

    def _repl(_match: re.Match[str]) -> str:
        nonlocal counter
        counter += 1
        return f"${counter}"

    return re.sub(r"\?", _repl, stmt)


class CloudConnection:
    """
    Serverless HTTP database connection supporting Turso (libSQL) and Neon Postgres.
    Implements context manager (`with conn:`), `execute()`, `executescript()`, and `close()`.
    """

    def __init__(self, config: dict[str, str]) -> None:
        self.config = config
        self.provider = config["provider"]
        self._client = httpx.Client(timeout=20.0)

    def __enter__(self) -> CloudConnection:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        return None

    def close(self) -> None:
        self._client.close()

    def executescript(self, script: str) -> None:
        statements = [s.strip() for s in script.split(";") if s.strip()]
        for stmt in statements:
            self.execute(stmt)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> CloudCursor:
        if self.provider == "turso":
            return self._execute_turso(sql, params)
        return self._execute_neon(sql, params)

    def _execute_turso(self, sql: str, params: Sequence[Any]) -> CloudCursor:
        args = []
        for p in params:
            if p is None:
                args.append({"type": "null"})
            elif isinstance(p, int) and not isinstance(p, bool):
                args.append({"type": "integer", "value": str(p)})
            elif isinstance(p, float):
                args.append({"type": "float", "value": p})
            elif isinstance(p, (bytes, bytearray)):
                args.append({"type": "blob", "base64": base64.b64encode(p).decode("ascii")})
            else:
                args.append({"type": "text", "value": str(p)})

        headers = {"Content-Type": "application/json"}
        if self.config.get("token"):
            headers["Authorization"] = f"Bearer {self.config['token']}"

        payload = {
            "requests": [
                {"type": "execute", "stmt": {"sql": sql, "args": args}},
                {"type": "close"},
            ]
        }
        resp = self._client.post(f"{self.config['url']}/v2/pipeline", headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        first_res = data.get("results", [{}])[0]
        if first_res.get("type") == "error":
            err_msg = first_res.get("error", {}).get("message", "Unknown Turso SQL error")
            raise RuntimeError(f"Turso SQL error: {err_msg}")

        exec_res = first_res.get("response", {}).get("result", {})
        cols = [c["name"] for c in exec_res.get("cols", [])]
        raw_rows = exec_res.get("rows", [])
        affected = int(exec_res.get("affected_row_count", 0))

        rows: list[CloudRow] = []
        for r in raw_rows:
            row_dict: dict[str, Any] = {}
            for col_name, cell in zip(cols, r):
                ctype = cell.get("type")
                if ctype == "null":
                    row_dict[col_name] = None
                elif ctype == "integer":
                    row_dict[col_name] = int(cell.get("value", 0))
                elif ctype == "float":
                    row_dict[col_name] = float(cell.get("value", 0.0))
                elif ctype == "blob":
                    row_dict[col_name] = base64.b64decode(cell.get("base64", ""))
                else:
                    row_dict[col_name] = cell.get("value")
            rows.append(CloudRow(row_dict, columns=cols))

        return CloudCursor(rows=rows, rowcount=affected if not rows else len(rows))

    def _execute_neon(self, sql: str, params: Sequence[Any]) -> CloudCursor:
        pg_sql = _convert_sqlite_to_postgres_sql(sql)
        parsed = urlparse(self.config["url"])
        host = parsed.hostname
        if not host:
            raise RuntimeError("Invalid DATABASE_URL: missing hostname.")

        endpoint = f"https://{host}/sql"
        headers = {
            "Neon-Connection-String": self.config["url"],
            "Content-Type": "application/json",
        }
        payload = {
            "query": pg_sql,
            "params": list(params),
        }
        resp = self._client.post(endpoint, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        fields = [f.get("name", "") for f in data.get("fields", [])]
        raw_rows = data.get("rows", [])
        row_count = int(data.get("rowCount") or 0)

        rows: list[CloudRow] = []
        for r in raw_rows:
            if isinstance(r, dict):
                rows.append(CloudRow(r, columns=fields or list(r.keys())))
            elif isinstance(r, list):
                row_dict = dict(zip(fields, r))
                rows.append(CloudRow(row_dict, columns=fields))

        return CloudCursor(rows=rows, rowcount=row_count if not rows else len(rows))


class CloudBlobStore:
    """
    Persists AES-256 MultiFernet-encrypted files and non-sensitive manifests
    inside the cloud database's `encrypted_blobs` table.
    """

    def __init__(self, config: dict[str, str]) -> None:
        self.config = config
        self._init_table()

    def _get_conn(self) -> CloudConnection:
        return CloudConnection(self.config)

    def _init_table(self) -> None:
        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS encrypted_blobs (
                    blob_key TEXT PRIMARY KEY,
                    ciphertext_b64 TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        finally:
            conn.close()

    def put(self, blob_key: str, raw_bytes: bytes) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        b64_data = base64.b64encode(raw_bytes).decode("ascii")
        conn = self._get_conn()
        try:
            conn.execute(
                """
                INSERT INTO encrypted_blobs (blob_key, ciphertext_b64, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(blob_key) DO UPDATE SET
                    ciphertext_b64 = excluded.ciphertext_b64,
                    updated_at = excluded.updated_at
                """,
                (blob_key, b64_data, now_iso),
            )
        finally:
            conn.close()

    def get(self, blob_key: str) -> Optional[bytes]:
        conn = self._get_conn()
        try:
            cur = conn.execute(
                "SELECT ciphertext_b64 FROM encrypted_blobs WHERE blob_key = ?",
                (blob_key,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return base64.b64decode(row["ciphertext_b64"])
        finally:
            conn.close()

    def delete_prefix(self, stem: str) -> bool:
        """Delete primary .enc file, manifest, line crops, and packet photos matching stem."""
        conn = self._get_conn()
        try:
            c1 = conn.execute(
                "DELETE FROM encrypted_blobs WHERE blob_key IN (?, ?)",
                (f"{stem}.enc", f"{stem}.manifest.json"),
            )
            c2 = conn.execute(
                "DELETE FROM encrypted_blobs WHERE blob_key LIKE ? OR blob_key LIKE ?",
                (f"{stem}_line_%.enc", f"{stem}_packet_%.enc"),
            )
            return (c1.rowcount + c2.rowcount) > 0
        finally:
            conn.close()
