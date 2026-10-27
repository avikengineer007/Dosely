"""
dosely/synonyms.py
==================
Shared name canonicalisation + combination-product helpers.

Data file: data/synonyms.json
    {
      "synonyms":     { "<alternate name>": "<canonical generic>", ... },
      "combinations": { "<combined generic name>": ["<ingredient>", ...], ... }
    }

* ``synonyms`` collapse alternate/international spellings onto ONE canonical generic so
  duplicate-therapy detection cannot be defeated by naming (acetaminophen == paracetamol).
* ``combinations`` register legacy single-string combined generics (for example
  "amoxicillin clavulanate") together with their real ingredient list.

Nothing here is ever fuzzy: every mapping is an explicit, reviewable table row.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

SYNONYMS_PATH = Path(__file__).resolve().parent.parent / "data" / "synonyms.json"

# Separators that join the ingredients of a combination product.
_SPLIT_RE = re.compile(r"\s*\+\s*|\s*/\s*|\s+and\s+|\s+with\s+", re.IGNORECASE)


def normalise(raw: str) -> str:
    """Lower-case, trim and collapse internal whitespace."""
    return re.sub(r"\s+", " ", raw.strip().lower())


def split_ingredients(text: str) -> list[str]:
    """Split a combination string on '+', '/', ' and ', ' with '. Empty parts are dropped."""
    return [normalise(p) for p in _SPLIT_RE.split(text) if p and p.strip()]


@lru_cache(maxsize=8)
def _load(path_str: str) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    path = Path(path_str)
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    synonyms = {normalise(k): normalise(v) for k, v in data.get("synonyms", {}).items()}
    combos = {
        normalise(k): tuple(normalise(i) for i in v)
        for k, v in data.get("combinations", {}).items()
    }
    return synonyms, combos


class SynonymTable:
    """Immutable view over a synonyms.json file."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else SYNONYMS_PATH
        self.synonyms, self.combinations = _load(str(self.path))
        self._combo_by_set = {frozenset(v): k for k, v in self.combinations.items()}

    def canonical(self, name: str) -> str:
        """Return the canonical generic for ``name`` (follows at most 3 hops, loop-safe)."""
        n = normalise(name)
        for _ in range(3):
            nxt = self.synonyms.get(n)
            if nxt is None or nxt == n:
                break
            n = nxt
        return n

    def combination_ingredients(self, name: str) -> Optional[list[str]]:
        """Ingredients of a registered combined generic name, or None."""
        ings = self.combinations.get(self.canonical(name))
        return list(ings) if ings else None

    def combination_name_for(self, ingredients: list[str]) -> Optional[str]:
        """Registered combined name whose ingredient set equals ``ingredients``, or None."""
        return self._combo_by_set.get(frozenset(self.canonical(i) for i in ingredients))


_default: Optional[SynonymTable] = None


def default_table() -> SynonymTable:
    global _default
    if _default is None:
        _default = SynonymTable()
    return _default


def canonical(name: str) -> str:
    """Canonicalise ``name`` using the default data/synonyms.json."""
    return default_table().canonical(name)
