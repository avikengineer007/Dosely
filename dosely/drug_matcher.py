"""
dosely/drug_matcher.py
======================
Drug name matching and classification engine.

Public API
----------
    DrugMatcher
    DrugMatcher.match(name_raw: str) -> MatchResult

MatchResult fields
------------------
    generic       : str | None   — resolved generic name
    drug_class    : str          — one of the canonical class strings or "other"
    purpose_plain : str | None   — plain-language one-sentence purpose
    match_score   : float        — 0.0–1.0 (1.0 = exact, <FUZZY_THRESHOLD = unknown)
    needs_review  : bool         — True for class "other" or score < FUZZY_THRESHOLD
    match_type    : str          — "exact_generic" | "exact_brand" | "fuzzy" | "unknown"

Matching logic
--------------
    1. Normalise input (strip, lower-case, collapse whitespace, remove dose/form tokens).
    2. Exact match against generics table.
    3. Exact match against brand aliases table.
    4. Fuzzy match (rapidfuzz token_sort_ratio) against all generics.
       - Score >= FUZZY_THRESHOLD  -> use best match (unless top-2 are ambiguous)
       - Score <  FUZZY_THRESHOLD  -> return "unknown" result
    5. Ambiguity guard: if the top-2 fuzzy candidates are within AMBIGUITY_GAP
       of each other, both are in review_reasons; neither is selected.

Never-Guess alignment
---------------------
    * Below threshold  -> generic=None, class="unknown", purpose_plain=None
    * Ambiguous        -> same; both candidates listed in review_reasons
    * This directly implements Rules 1 & 2 from never_guess_rules.md.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz, process as rf_process

from dosely.drug_db import DrugDatabase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

FUZZY_THRESHOLD: float = 80.0        # score out of 100 (rapidfuzz scale)
AMBIGUITY_GAP:  float = 10.0        # if top-2 scores are within this gap -> ambiguous

# Canonical drug classes the module explicitly recognises
_KNOWN_CLASSES: frozenset[str] = frozenset({
    "analgesic_nsaid",
    "antacid_ppi",
    "antibiotic",
    "antihistamine",
})

# Tokens that commonly appear alongside drug names on prescriptions but are
# NOT part of the drug name itself (stripped before matching).
_NOISE_PATTERN = re.compile(
    r"(?:"
    r"\b(?:tab|cap|syp|inj|oint|susp|sol|drops?)\.?|"                  # dose forms
    r"\b\d+\s*(?:mg|mcg|ml|g|iu|units?)\b|"                           # strengths
    r"\b(?:od|bd|tds|qid|stat|sos|prn|mane|nocte)\b"                  # frequencies
    r")",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """Structured result returned by DrugMatcher.match()."""

    generic:       Optional[str]
    drug_class:    str                    # "analgesic_nsaid" | "antacid_ppi" |
                                          # "antibiotic" | "antihistamine" | "other" | "unknown"
    purpose_plain: Optional[str]
    match_score:   float                  # 0.0 – 1.0
    needs_review:  bool
    match_type:    str                    # "exact_generic" | "exact_brand" | "fuzzy" | "unknown"
    review_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generic":       self.generic,
            "class":         self.drug_class,
            "purpose_plain": self.purpose_plain,
            "match_score":   round(self.match_score, 4),
            "needs_review":  self.needs_review,
            "match_type":    self.match_type,
            "review_reasons": self.review_reasons,
        }


# ---------------------------------------------------------------------------
# DrugMatcher
# ---------------------------------------------------------------------------

class DrugMatcher:
    """
    Matches a raw drug name string to a structured drug record.

    Parameters
    ----------
    db : DrugDatabase
        Pre-built database instance.  Use DrugDatabase.build() to create one.
    fuzzy_threshold : float
        Minimum rapidfuzz score (0-100) to accept a fuzzy match.
    ambiguity_gap : float
        If the top-2 candidates are within this many score points, the match
        is deemed ambiguous and neither candidate is selected.
    """

    def __init__(
        self,
        db: DrugDatabase,
        fuzzy_threshold: float = FUZZY_THRESHOLD,
        ambiguity_gap:   float = AMBIGUITY_GAP,
    ) -> None:
        self._db              = db
        self._threshold       = fuzzy_threshold
        self._ambiguity_gap   = ambiguity_gap

        # Pre-load all generics for fuzzy index (small dataset; fits in RAM)
        self._all_generics: list[tuple[str, str, str]] = db.get_all_generics()
        self._generic_names:  list[str] = [g[0] for g in self._all_generics]
        self._generic_lookup: dict[str, tuple[str, str]] = {
            g[0]: (g[1], g[2]) for g in self._all_generics
        }  # generic -> (drug_class, purpose_plain)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(self, name_raw: str) -> MatchResult:
        """
        Match a raw drug name to a database record.

        Returns a MatchResult.  When the name cannot be matched with
        sufficient confidence, generic=None and match_type="unknown".
        """
        if not name_raw or not name_raw.strip():
            return self._unknown_result(["Empty drug name provided."])

        cleaned = self._clean(name_raw)
        logger.debug("match('%s') -> cleaned='%s'", name_raw, cleaned)

        # ---- Step 1: exact generic match --------------------------------
        if cleaned in self._generic_lookup:
            return self._build_result(cleaned, "exact_generic", 100.0)

        # ---- Step 2: exact brand match ----------------------------------
        brand_generic = self._db.get_by_brand(cleaned)
        if brand_generic and brand_generic in self._generic_lookup:
            return self._build_result(brand_generic, "exact_brand", 100.0)

        # ---- Step 3: fuzzy match ----------------------------------------
        return self._fuzzy_match(cleaned, name_raw)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean(raw: str) -> str:
        """
        Normalise a raw name for matching:
        - lower-case
        - strip leading/trailing whitespace
        - remove noise tokens (dose form, strength, frequency)
        - collapse internal whitespace
        """
        s = raw.strip().lower()
        s = _NOISE_PATTERN.sub(" ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _fuzzy_match(self, cleaned: str, original: str) -> MatchResult:
        """Run rapidfuzz and apply threshold + ambiguity guard."""
        if not self._generic_names:
            return self._unknown_result(["Drug database is empty."])

        # token_sort_ratio handles word-order variations (e.g. "acid mefenamic")
        hits = rf_process.extract(
            cleaned,
            self._generic_names,
            scorer=fuzz.token_sort_ratio,
            limit=3,
            score_cutoff=0,           # get all; we apply threshold ourselves
        )

        if not hits:
            return self._unknown_result([f"No candidates found for '{original}'."])

        best_name,  best_score,  _ = hits[0]
        second_name, second_score, _ = hits[1] if len(hits) > 1 else (None, 0.0, None)

        logger.debug(
            "Fuzzy top-2: ('%s', %.1f) / ('%s', %.1f)",
            best_name, best_score, second_name, second_score,
        )

        # --- Below threshold ---
        if best_score < self._threshold:
            return self._unknown_result([
                f"Best fuzzy match '{best_name}' scored {best_score:.1f} "
                f"(threshold {self._threshold:.0f})."
            ])

        # --- Ambiguity guard (top-2 within gap) ---
        if second_name and (best_score - second_score) <= self._ambiguity_gap:
            reasons = [
                f"Ambiguous: '{best_name}' ({best_score:.1f}) and "
                f"'{second_name}' ({second_score:.1f}) are too close to choose safely.",
                "Please select the correct drug manually.",
            ]
            logger.warning("Ambiguous match for '%s': %s", original, reasons[0])
            return self._unknown_result(reasons)

        # --- Accepted fuzzy match ---
        normalised_score = best_score / 100.0
        return self._build_result(best_name, "fuzzy", normalised_score)

    def _build_result(
        self, generic: str, match_type: str, raw_score: float
    ) -> MatchResult:
        """Build a MatchResult for a successfully resolved generic."""
        drug_class, purpose_plain = self._generic_lookup[generic]
        normalised_score = raw_score / 100.0 if raw_score > 1.0 else raw_score

        is_known_class = drug_class in _KNOWN_CLASSES
        needs_review   = not is_known_class    # "other" always needs review

        return MatchResult(
            generic       = generic,
            drug_class    = drug_class,
            purpose_plain = purpose_plain,
            match_score   = normalised_score,
            needs_review  = needs_review,
            match_type    = match_type,
        )

    @staticmethod
    def _unknown_result(reasons: list[str]) -> MatchResult:
        """Return a safe 'unknown' result with no guessed generic."""
        return MatchResult(
            generic        = None,
            drug_class     = "unknown",
            purpose_plain  = None,
            match_score    = 0.0,
            needs_review   = True,
            match_type     = "unknown",
            review_reasons = reasons,
        )
