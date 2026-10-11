"""
dosely/drug_db.py
=================
SQLite-backed drug knowledge base loader.

Responsibilities
----------------
* Build (or rebuild) an in-memory / on-disk SQLite database from:
    1. data/seed_drugs.json  — curated generic drug entries (RxNorm-style)
    2. Any openFDA JSON file the caller supplies
    3. data/indian_brands.csv  — brand-to-generic mapping for Indian market
       (user-supplied CSV; falls back gracefully when the file is absent)

* Expose a thin query API used by DrugMatcher:
    get_all_generics()   -> list of (generic, class, purpose_plain)
    get_by_brand(name)   -> generic str | None
    get_generic_info(generic) -> dict | None

Schema
------
    drugs(id, generic, drug_class, purpose_plain)
    brand_aliases(id, brand, generic_fk -> drugs.generic)

Public surface
--------------
    DrugDatabase            class
    DrugDatabase.build()    classmethod — factory; creates DB from data files
"""

from __future__ import annotations

import csv
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths (relative to this file's package root)
# ---------------------------------------------------------------------------

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent
_DATA_DIR = _PROJECT_ROOT / "data"

SEED_DRUGS_PATH = _DATA_DIR / "seed_drugs.json"
INDIAN_BRANDS_PATH = _DATA_DIR / "indian_brands.csv"


# ---------------------------------------------------------------------------
# DrugDatabase
# ---------------------------------------------------------------------------

class DrugDatabase:
    """Thin wrapper around a SQLite connection for drug lookups."""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def build(
        cls,
        seed_path: Path = SEED_DRUGS_PATH,
        brands_csv: Optional[Path] = INDIAN_BRANDS_PATH,
        openfda_json: Optional[Path] = None,
        db_path: str = ":memory:",
    ) -> "DrugDatabase":
        """
        Create (or recreate) the SQLite database and return a DrugDatabase.

        Parameters
        ----------
        seed_path   : JSON file of curated drug entries.
        brands_csv  : CSV file mapping brand names to generics (optional).
        openfda_json: openFDA drug JSON file (optional extra source).
        db_path     : SQLite path, defaults to ':memory:' for in-process use.
        """
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        db = cls(conn)
        db._create_schema()
        db._load_seed(seed_path)
        if openfda_json:
            db._load_openfda(openfda_json)
        if brands_csv:
            db._load_brands(brands_csv)
        return db

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS drugs (
                generic      TEXT PRIMARY KEY,
                drug_class   TEXT NOT NULL,
                purpose_plain TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS brand_aliases (
                brand    TEXT PRIMARY KEY,
                generic  TEXT NOT NULL,
                FOREIGN KEY (generic) REFERENCES drugs(generic)
            );

            CREATE INDEX IF NOT EXISTS idx_brand_lower
                ON brand_aliases (lower(brand));
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Loaders
    # ------------------------------------------------------------------

    def _normalise_generic(self, raw: str) -> str:
        """Lower-case, strip extra whitespace, collapse multiple spaces."""
        return re.sub(r"\s+", " ", raw.strip().lower())

    def _load_seed(self, path: Path) -> None:
        if not path.exists():
            logger.warning("Seed drug file not found: %s — skipping.", path)
            return
        with path.open(encoding="utf-8-sig") as fh:
            entries = json.load(fh)
        rows = [
            (self._normalise_generic(e["generic"]), e["class"], e["purpose_plain"])
            for e in entries
        ]
        self._conn.executemany(
            "INSERT OR IGNORE INTO drugs (generic, drug_class, purpose_plain) VALUES (?,?,?)",
            rows,
        )
        self._conn.commit()
        logger.info("Loaded %d generic drug entries from %s.", len(rows), path.name)

    def _load_openfda(self, path: Path) -> None:
        """
        Parse an openFDA drug JSON file.

        Expected shape (openFDA drug label export):
            {"results": [{"openfda": {"generic_name": [...], "pharm_class_epc": [...]}}, ...]}

        Only entries with a resolvable generic name are imported.
        Class and purpose are not available in openFDA labels, so they are
        inserted as class='other' with a placeholder purpose.
        """
        if not path.exists():
            logger.warning("openFDA file not found: %s — skipping.", path)
            return
        with path.open(encoding="utf-8-sig") as fh:
            data = json.load(fh)
        results = data.get("results", [])
        inserted = 0
        for entry in results:
            openfda = entry.get("openfda", {})
            generics = openfda.get("generic_name", [])
            for g in generics:
                name = self._normalise_generic(g)
                if not name:
                    continue
                cur = self._conn.execute(
                    "SELECT 1 FROM drugs WHERE generic = ?", (name,)
                )
                if cur.fetchone():
                    continue  # already in DB from seed
                self._conn.execute(
                    "INSERT OR IGNORE INTO drugs (generic, drug_class, purpose_plain) VALUES (?,?,?)",
                    (name, "other", "Used as directed by your healthcare provider."),
                )
                inserted += 1
        self._conn.commit()
        logger.info("Imported %d extra generics from openFDA file %s.", inserted, path.name)

    def _load_brands(self, path: Path) -> None:
        """
        Load brand-to-generic CSV.

        Expected columns (case-insensitive header):
            brand_name, generic_name

        The generic must already exist in the drugs table (from seed or openFDA).
        Brands whose generic is not in the DB are skipped with a warning so that
        a bad CSV row never silently pollutes the alias table.
        """
        if not path.exists():
            logger.warning(
                "Indian brands CSV not found at %s — brand lookups will be unavailable.",
                path,
            )
            return
        skipped = 0
        loaded = 0
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                # Normalise column names
                row_lower = {k.strip().lower(): v.strip() for k, v in row.items()}
                brand = row_lower.get("brand_name", "").strip()
                generic_raw = row_lower.get("generic_name", "").strip()
                if not brand or not generic_raw:
                    continue

                # A brand may map to a combination — take the first ingredient
                # to resolve drug class (the alias still stores the full string)
                primary_generic = self._normalise_generic(generic_raw.split("+")[0])
                brand_lower = brand.lower()

                cur = self._conn.execute(
                    "SELECT generic FROM drugs WHERE generic = ?", (primary_generic,)
                )
                if not cur.fetchone():
                    logger.debug(
                        "Brand '%s' -> generic '%s' not in drugs table; skipping.",
                        brand,
                        primary_generic,
                    )
                    skipped += 1
                    continue

                self._conn.execute(
                    "INSERT OR REPLACE INTO brand_aliases (brand, generic) VALUES (?,?)",
                    (brand_lower, primary_generic),
                )
                loaded += 1

        self._conn.commit()
        logger.info(
            "Loaded %d brand aliases from %s (%d skipped — generic not in DB).",
            loaded,
            path.name,
            skipped,
        )

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_all_generics(self) -> list[tuple[str, str, str]]:
        """Return all (generic, drug_class, purpose_plain) rows."""
        cur = self._conn.execute(
            "SELECT generic, drug_class, purpose_plain FROM drugs ORDER BY generic"
        )
        return [(r["generic"], r["drug_class"], r["purpose_plain"]) for r in cur.fetchall()]

    def get_by_brand(self, brand: str) -> Optional[str]:
        """Return the primary generic for a brand name, or None."""
        cur = self._conn.execute(
            "SELECT generic FROM brand_aliases WHERE brand = ?",
            (brand.strip().lower(),),
        )
        row = cur.fetchone()
        return row["generic"] if row else None

    def get_generic_info(self, generic: str) -> Optional[dict]:
        """Return {generic, drug_class, purpose_plain} dict or None."""
        cur = self._conn.execute(
            "SELECT generic, drug_class, purpose_plain FROM drugs WHERE generic = ?",
            (generic.strip().lower(),),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self._conn.close()
