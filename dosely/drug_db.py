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
    get_all_generics()          -> list of (generic, class, purpose_plain)
    get_brand_ingredients(name) -> list of ingredient dicts (1..N), [] when unknown
    get_by_brand(name)          -> display name of the brand's ingredients, or None
    get_generic_info(generic)   -> dict | None

Schema
------
    drugs(generic, drug_class, purpose_plain)
    brand_ingredients(brand, ingredient -> drugs.generic, position, strength)

A brand is NEVER reduced to one ingredient: a CSV row is either loaded with every one of
its ingredients or (strict=False) skipped as a whole / (strict=True) rejected loudly.

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

from dosely.synonyms import SynonymTable, split_ingredients

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

    def __init__(
        self,
        conn: sqlite3.Connection,
        synonyms: Optional[SynonymTable] = None,
        strict: bool = False,
    ) -> None:
        self._conn = conn
        self.synonyms = synonyms or SynonymTable()
        self.strict = strict

    @classmethod
    def build(
        cls,
        seed_path: Path = SEED_DRUGS_PATH,
        brands_csv: Optional[Path] = INDIAN_BRANDS_PATH,
        openfda_json: Optional[Path] = None,
        db_path: str = ":memory:",
        synonyms_path: Optional[Path] = None,
        strict: bool = False,
    ) -> "DrugDatabase":
        """
        Create (or recreate) the SQLite database and return a DrugDatabase.

        Parameters
        ----------
        seed_path     : JSON file of curated drug entries.
        brands_csv    : CSV file mapping brand names to ingredients (optional).
        openfda_json  : openFDA drug JSON file (optional extra source).
        db_path       : SQLite path, defaults to ':memory:' for in-process use.
        synonyms_path : synonyms/combinations table (default data/synonyms.json).
        strict        : when True, any brand row with an unresolved ingredient (or any
                        inconsistent row) raises ValueError instead of being skipped.
                        The application builds with strict=True.
        """
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        db = cls(conn, SynonymTable(synonyms_path), strict=strict)
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
                purpose_plain TEXT NOT NULL,
                clinically_reviewed INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS brand_ingredients (
                brand      TEXT    NOT NULL,
                ingredient TEXT    NOT NULL,
                position   INTEGER NOT NULL,
                strength   TEXT,
                PRIMARY KEY (brand, position),
                FOREIGN KEY (ingredient) REFERENCES drugs(generic)
            );

            CREATE INDEX IF NOT EXISTS idx_brand_ingredients_brand
                ON brand_ingredients (brand);
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

        reviewed_map: dict[str, bool] = {}
        review_csv = path.parent / "review" / "pharmacist_review.csv"
        if review_csv.exists():
            with review_csv.open(encoding="utf-8-sig") as rf:
                for r in csv.DictReader(rf):
                    g = self._normalise_generic(r.get("generic", ""))
                    reviewed_map[g] = r.get("clinically_reviewed", "").strip().lower() in ("true", "1", "yes")

        rows = [
            (
                self._normalise_generic(e["generic"]),
                e["class"],
                e["purpose_plain"],
                1 if reviewed_map.get(self._normalise_generic(e["generic"]), False) else 0,
            )
            for e in entries
        ]
        self._conn.executemany(
            "INSERT OR IGNORE INTO drugs (generic, drug_class, purpose_plain, clinically_reviewed) VALUES (?,?,?,?)",
            rows,
        )
        self._conn.commit()
        unreviewed = sum(1 for r in rows if r[3] == 0)
        logger.info(
            "Loaded %d generic drug entries from %s (%d unreviewed pending clinical review).",
            len(rows),
            path.name,
            unreviewed,
        )

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

    def _resolve_ingredient_token(self, token: str) -> Optional[list[str]]:
        """
        Canonicalise one CSV ingredient token to generics present in the drugs table.

        A token that names a registered combined generic (data/synonyms.json
        "combinations") expands to all of its ingredients.  Returns None if ANY
        resulting ingredient is not in the drugs table.
        """
        canon = self.synonyms.canonical(token)
        expanded = self.synonyms.combination_ingredients(canon) or [canon]
        resolved = []
        for ing in expanded:
            ing = self.synonyms.canonical(ing)
            if not self._conn.execute(
                "SELECT 1 FROM drugs WHERE generic = ?", (ing,)
            ).fetchone():
                return None
            resolved.append(ing)
        return resolved

    def _reject_row(self, brand: str, reason: str) -> None:
        """strict -> raise; otherwise the caller skips the WHOLE row (never truncates it)."""
        if self.strict:
            raise ValueError(f"indian_brands.csv: brand '{brand}': {reason}")

    def _load_brands(self, path: Path) -> None:
        """
        Load brand-to-ingredients CSV.

        Expected columns (case-insensitive header):
            brand_name, generic_name[, strength]

        ``generic_name`` may list several ingredients separated by '+', '/', ' and ' or
        ' with '.  Every ingredient is canonicalised through data/synonyms.json and must
        exist in the drugs table.  The optional ``strength`` column is split the same way
        and must have one value per ingredient.

        Unresolved ingredient / strength-count mismatch / conflicting duplicate brand:
          strict=True  -> ValueError (build fails loudly)
          strict=False -> the whole row is skipped and counted; it is never truncated.
        """
        if not path.exists():
            logger.warning(
                "Indian brands CSV not found at %s — brand lookups will be unavailable.",
                path,
            )
            return
        skipped = 0
        loaded = 0
        seen: dict[str, list[str]] = {}
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                row_lower = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
                brand = row_lower.get("brand_name", "")
                generic_raw = row_lower.get("generic_name", "")
                if not brand or not generic_raw:
                    continue
                brand_key = brand.lower()

                ingredients: list[str] = []
                unresolved: Optional[str] = None
                for token in split_ingredients(generic_raw):
                    resolved = self._resolve_ingredient_token(token)
                    if resolved is None:
                        unresolved = token
                        break
                    ingredients.extend(resolved)
                if unresolved is not None or not ingredients:
                    self._reject_row(
                        brand, f"unresolved ingredient '{unresolved}' (not in drugs table)"
                    )
                    skipped += 1
                    continue

                strengths: list[Optional[str]] = [None] * len(ingredients)
                strength_raw = row_lower.get("strength", "")
                if strength_raw:
                    parts = [p.strip() for p in strength_raw.split("+")]
                    if len(parts) != len(ingredients):
                        self._reject_row(
                            brand,
                            f"{len(parts)} strength value(s) for {len(ingredients)} ingredient(s)",
                        )
                        skipped += 1
                        continue
                    strengths = list(parts)

                if brand_key in seen and seen[brand_key] != ingredients:
                    self._reject_row(brand, "listed twice with different ingredients")
                seen[brand_key] = ingredients

                self._conn.execute("DELETE FROM brand_ingredients WHERE brand = ?", (brand_key,))
                self._conn.executemany(
                    "INSERT INTO brand_ingredients (brand, ingredient, position, strength) "
                    "VALUES (?,?,?,?)",
                    [(brand_key, ing, pos, strengths[pos]) for pos, ing in enumerate(ingredients)],
                )
                loaded += 1

        self._conn.commit()
        logger.info(
            "Loaded %d brands from %s (%d skipped — unresolved ingredient or inconsistent row).",
            loaded,
            path.name,
            skipped,
        )

    # ------------------------------------------------------------------
    # Query API
    # ------------------------------------------------------------------

    def get_all_generics(self) -> list[tuple[str, str, str, bool]]:
        """Return all (generic, drug_class, purpose_plain, clinically_reviewed) rows."""
        cur = self._conn.execute(
            "SELECT generic, drug_class, purpose_plain, clinically_reviewed FROM drugs ORDER BY generic"
        )
        return [(r["generic"], r["drug_class"], r["purpose_plain"], bool(r["clinically_reviewed"])) for r in cur.fetchall()]

    def get_brand_ingredients(self, brand: str) -> list[dict]:
        """
        Return ALL ingredients of a brand in label order, each as
        {generic, drug_class, purpose_plain, strength, clinically_reviewed}.  Empty list when unknown.
        """
        cur = self._conn.execute(
            "SELECT d.generic AS generic, d.drug_class AS drug_class, "
            "       d.purpose_plain AS purpose_plain, d.clinically_reviewed AS clinically_reviewed, "
            "       b.strength AS strength "
            "FROM brand_ingredients b JOIN drugs d ON d.generic = b.ingredient "
            "WHERE b.brand = ? ORDER BY b.position",
            (brand.strip().lower(),),
        )
        res = []
        for r in cur.fetchall():
            d = dict(r)
            d["clinically_reviewed"] = bool(d.get("clinically_reviewed", 0))
            res.append(d)
        return res

    def get_ingredient_info(self, generic: str) -> Optional[dict]:
        """One ingredient as {generic, drug_class, purpose_plain, strength=None, clinically_reviewed} or None."""
        info = self.get_generic_info(self.synonyms.canonical(generic))
        if not info:
            return None
        return {
            "generic": info["generic"],
            "drug_class": info["drug_class"],
            "purpose_plain": info["purpose_plain"],
            "strength": None,
            "clinically_reviewed": info.get("clinically_reviewed", False),
        }

    def display_name(self, ingredients: list[str]) -> str:
        """Registered combined generic name for this ingredient set, else 'a + b'."""
        if len(ingredients) == 1:
            return ingredients[0]
        return self.synonyms.combination_name_for(ingredients) or " + ".join(ingredients)

    def get_by_brand(self, brand: str) -> Optional[str]:
        """Display name covering ALL ingredients of a brand (never just the first), or None."""
        ings = self.get_brand_ingredients(brand)
        if not ings:
            return None
        return self.display_name([i["generic"] for i in ings])

    def get_generic_info(self, generic: str) -> Optional[dict]:
        """Return {generic, drug_class, purpose_plain, clinically_reviewed} dict or None."""
        cur = self._conn.execute(
            "SELECT generic, drug_class, purpose_plain, clinically_reviewed FROM drugs WHERE generic = ?",
            (generic.strip().lower(),),
        )
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["clinically_reviewed"] = bool(d.get("clinically_reviewed", 0))
        return d

    def close(self) -> None:
        self._conn.close()
