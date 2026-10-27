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
    generic           : str | None   — resolved generic name (exact match ONLY)
    drug_class        : str          — confirmed class or "unknown"
    purpose_plain     : str | None   — plain-language one-sentence purpose
    match_score       : float        — 0.0–1.0 (1.0 = exact, <FUZZY_THRESHOLD = unknown)
    needs_review      : bool         — True for fuzzy suggestion, class "other", or unknown
    match_type        : str          — "exact_generic" | "exact_brand" | "fuzzy_suggestion" | "unknown"
    suggested_generic : str | None   — fuzzy suggestion generic
    suggested_class   : str | None   — fuzzy suggestion class
    review_reasons    : list[str]    — machine-generated review notes
    lasa_warning      : str | None   — look-alike / sound-alike warning
    ingredients       : list[dict]   — EVERY active ingredient ({generic, drug_class,
                                       purpose_plain, strength}); 1 entry for single drugs,
                                       N for combinations; [] when unresolved/fuzzy

Combination products (Combiflam, Pan D, Augmentin, ...) resolve to ALL of their ingredients;
the first ingredient is never used as a stand-in for the product. needs_review is True when
any ingredient is outside the four MVP classes, with a note naming that ingredient.
--------------
    1. Normalise input (strip, lower-case, collapse whitespace, remove noise tokens).
    2. Exact match against generics table (auto-resolves with needs_review=False if known class).
    3. Exact match against brand aliases table (auto-resolves with needs_review=False if known class).
    4. Fuzzy match (rapidfuzz token_sort_ratio) against all generics:
       - Threshold: score >= 90.0 AND margin >= 10.0 points over second-best candidate.
       - Returns match_type="fuzzy_suggestion", generic=None, drug_class="unknown", needs_review=True.
       - Does NOT populate generic or drug_class as confirmed.
    5. LASA Guard:
       - Uses data/lasa_pairs.json.
       - If input is within edit distance 3 of a pair member, never produce a suggestion for the
         other member without needs_review=True and a visible LASA warning.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process as rf_process

from dosely.drug_db import DrugDatabase
from dosely.synonyms import split_ingredients

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

FUZZY_THRESHOLD: float = 90.0        # Raised to 90.0 per Task 1
AMBIGUITY_GAP:  float = 10.0        # Margin of at least 10 points required

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

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LASA_PAIRS_PATH = _DATA_DIR / "lasa_pairs.json"


# ---------------------------------------------------------------------------
# Levenshtein distance helper
# ---------------------------------------------------------------------------

def levenshtein_distance(s1: str, s2: str) -> int:
    """Standard dynamic-programming Levenshtein distance."""
    if s1 == s2:
        return 0
    if len(s1) < len(s2):
        s1, s2 = s2, s1
    if len(s2) == 0:
        return len(s1)
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class MatchResult:
    """Structured result returned by DrugMatcher.match()."""

    generic:           Optional[str]
    drug_class:        str                    # "analgesic_nsaid" | "antacid_ppi" |
                                              # "antibiotic" | "antihistamine" | "other" | "unknown"
    purpose_plain:     Optional[str]
    match_score:       float                  # 0.0 – 1.0
    needs_review:      bool
    match_type:        str                    # "exact_generic" | "exact_brand" | "fuzzy_suggestion" | "unknown"
    suggested_generic: Optional[str] = None
    suggested_class:   Optional[str] = None
    suggested_purpose: Optional[str] = None
    review_reasons:    list[str] = field(default_factory=list)
    lasa_warning:      Optional[str] = None
    ingredients:       list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ingredients":       [dict(i) for i in self.ingredients],
            "contains":          " + ".join(i["generic"] for i in self.ingredients),
            "generic":           self.generic,
            "class":             self.drug_class,
            "purpose_plain":     self.purpose_plain,
            "match_score":       round(self.match_score, 4),
            "score":             round(self.match_score, 4),
            "needs_review":      self.needs_review,
            "match_type":        self.match_type,
            "suggested_generic": self.suggested_generic,
            "suggested_class":   self.suggested_class,
            "suggested_purpose": self.suggested_purpose,
            "review_reasons":    list(self.review_reasons),
            "lasa_warning":      self.lasa_warning,
        }


# ---------------------------------------------------------------------------
# DrugMatcher
# ---------------------------------------------------------------------------

class DrugMatcher:
    """
    Matches a raw drug name string to a database record.

    Only exact generic and exact brand matches auto-resolve with needs_review=False.
    Any fuzzy result produces a fuzzy suggestion with needs_review=True.
    """

    def __init__(
        self,
        db: DrugDatabase,
        fuzzy_threshold: float = FUZZY_THRESHOLD,
        ambiguity_gap:   float = AMBIGUITY_GAP,
        lasa_path:       Path  = LASA_PAIRS_PATH,
    ) -> None:
        self._db              = db
        self._threshold       = fuzzy_threshold
        self._ambiguity_gap   = ambiguity_gap

        # Pre-load all generics for fast lookup
        self._all_generics = db.get_all_generics()
        self._generic_names:  list[str] = [g[0] for g in self._all_generics]
        self._generic_lookup: dict[str, tuple[str, str, bool]] = {
            g[0]: (g[1], g[2], bool(g[3]) if len(g) > 3 else False) for g in self._all_generics
        }  # generic -> (drug_class, purpose_plain, clinically_reviewed)

        # Pre-load LASA pairs
        self._lasa_pairs: list[tuple[str, str]] = []
        if lasa_path.exists():
            with open(lasa_path, "r", encoding="utf-8") as f:
                raw_pairs = json.load(f)
                self._lasa_pairs = [(p[0].lower().strip(), p[1].lower().strip()) for p in raw_pairs]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(self, drug_name: str) -> MatchResult:
        """
        Match a raw drug name to a database record.

        Only exact generic or known brand alias matches auto-resolve.
        Fuzzy matches return match_type='fuzzy_suggestion', generic=None,
        and needs_review=True.
        """
        if not drug_name or not drug_name.strip():
            return self._unknown_result(["Empty drug name provided."])

        cleaned = self._clean(drug_name)

        syn = self._db.synonyms
        canon = syn.canonical(cleaned)

        # ---- Step 1: exact generic match (after synonym canonicalisation) -
        if canon in self._generic_lookup:
            combo = syn.combination_ingredients(canon)
            if combo:
                ings = [self._db.get_ingredient_info(c) for c in combo]
                if all(ings):
                    return self._build_combo_result(
                        ings, "exact_generic", display=canon, seed=self._generic_lookup[canon]
                    )
            return self._build_exact_result(canon, "exact_generic", 100.0)

        # ---- Step 2: exact brand match (ALL ingredients) ------------------
        brand_ings = self._db.get_brand_ingredients(cleaned)
        if brand_ings:
            return self._build_combo_result(brand_ings, "exact_brand")

        # ---- Step 2b: explicit combination written out ("a + b", "a/b") --
        parts = split_ingredients(cleaned)
        if len(parts) > 1:
            ings = [self._db.get_ingredient_info(p) for p in parts]
            names = [i["generic"] for i in ings if i]
            if all(ings) and len(set(names)) == len(names):
                return self._build_combo_result(ings, "exact_generic")

        # ---- Step 3: fuzzy match (suggestion only) ----------------------
        return self._fuzzy_match(cleaned, drug_name)

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

    def _check_lasa(self, cleaned: str, target_drug: str) -> Optional[str]:
        """
        Check if cleaned input is within edit distance 3 of a LASA pair member
        where the other member is target_drug.
        """
        for drug_a, drug_b in self._lasa_pairs:
            if target_drug == drug_b and levenshtein_distance(cleaned, drug_a) <= 3:
                return (
                    f"LASA Warning: Input is similar to '{drug_a}', which is a known "
                    f"look-alike/sound-alike drug to suggested '{drug_b}'. Verify intended drug."
                )
            if target_drug == drug_a and levenshtein_distance(cleaned, drug_b) <= 3:
                return (
                    f"LASA Warning: Input is similar to '{drug_b}', which is a known "
                    f"look-alike/sound-alike drug to suggested '{drug_a}'. Verify intended drug."
                )
        return None

    def _fuzzy_match(self, cleaned: str, input_text: str) -> MatchResult:
        """Run rapidfuzz and produce fuzzy suggestions or unknown."""
        if not self._generic_names:
            return self._unknown_result(["Drug database is empty."])

        hits = rf_process.extract(
            cleaned,
            self._generic_names,
            scorer=fuzz.token_sort_ratio,
            limit=3,
            score_cutoff=0,
        )

        if not hits:
            return self._unknown_result([f"No candidates found for input."])

        best_name,  best_score,  _ = hits[0]
        second_name, second_score, _ = hits[1] if len(hits) > 1 else (None, 0.0, None)

        # Check for LASA alert regardless of score
        lasa_alert = self._check_lasa(cleaned, best_name)

        # --- Below threshold (< 90.0) ---
        if best_score < self._threshold:
            reasons = [
                f"Best fuzzy match scored {best_score:.1f} (threshold {self._threshold:.0f})."
            ]
            if lasa_alert:
                reasons.append(lasa_alert)
            return self._unknown_result(reasons, lasa_warning=lasa_alert)

        # --- Ambiguity guard (margin < 10.0 points over second candidate) ---
        if second_name and (best_score - second_score) < self._ambiguity_gap:
            reasons = [
                f"Ambiguous: top candidates scored too close ({best_score:.1f} vs {second_score:.1f}) to choose safely.",
                "Please select the correct drug manually.",
            ]
            if lasa_alert:
                reasons.append(lasa_alert)
            return self._unknown_result(reasons, lasa_warning=lasa_alert)

        # --- Accepted fuzzy suggestion ---
        lookup = self._generic_lookup[best_name]
        suggested_class, suggested_purpose = lookup[0], lookup[1]
        reasons = [f"Fuzzy match suggestion: did you mean '{best_name}'?"]
        if lasa_alert:
            reasons.append(lasa_alert)

        normalised_score = best_score / 100.0

        return MatchResult(
            generic           = None,               # NOT populated as confirmed
            drug_class        = "unknown",          # NOT populated as confirmed
            purpose_plain     = None,               # NOT populated as confirmed
            match_score       = normalised_score,
            needs_review      = True,               # Always True for fuzzy suggestion
            match_type        = "fuzzy_suggestion",
            suggested_generic = best_name,
            suggested_class   = suggested_class,
            suggested_purpose = suggested_purpose,
            review_reasons    = reasons,
            lasa_warning      = lasa_alert,
        )

    def _build_exact_result(
        self, generic: str, match_type: str, raw_score: float
    ) -> MatchResult:
        """Build a MatchResult for an exact single-ingredient generic match."""
        lookup_entry = self._generic_lookup[generic]
        drug_class, purpose_plain = lookup_entry[0], lookup_entry[1]
        clinically_reviewed = lookup_entry[2] if len(lookup_entry) > 2 else False

        normalised_score = raw_score / 100.0 if raw_score > 1.0 else raw_score

        is_known_class = drug_class in _KNOWN_CLASSES
        needs_review   = not is_known_class    # non-MVP always needs review

        reasons: list[str] = []
        if not is_known_class:
            if drug_class == "opioid_analgesic":
                reasons.append(
                    f"Ingredient '{generic}' is an opioid analgesic and requires clinical review."
                )
            else:
                reasons.append(
                    f"Ingredient '{generic}' (class '{drug_class}') is outside the supported "
                    f"drug classes and requires confirmation."
                )

        require_review = os.getenv("REQUIRE_CLINICAL_REVIEW", "").strip().lower() in ("true", "1", "yes")
        if require_review and not clinically_reviewed:
            needs_review = True
            reasons.append("Requires clinical review by a licensed pharmacist before use.")

        return MatchResult(
            generic           = generic,
            drug_class        = drug_class,
            purpose_plain     = purpose_plain,
            match_score       = normalised_score,
            needs_review      = needs_review,
            match_type        = match_type,
            suggested_generic = None,
            suggested_class   = None,
            suggested_purpose = None,
            review_reasons    = reasons,
            lasa_warning      = None,
            ingredients       = [{
                "generic": generic, "drug_class": drug_class,
                "purpose_plain": purpose_plain, "strength": None,
                "clinically_reviewed": clinically_reviewed,
            }],
        )

    def _build_combo_result(
        self,
        ingredients: list[dict],
        match_type: str,
        display: Optional[str] = None,
        seed: Optional[tuple[str, str, bool]] = None,
    ) -> MatchResult:
        """
        Build a MatchResult carrying EVERY ingredient (1..N).

        needs_review is True when any ingredient is outside the four MVP classes or
        unreviewed when REQUIRE_CLINICAL_REVIEW is active.
        """
        ings = [dict(i) for i in ingredients]
        names = [i["generic"] for i in ings]
        generic = display or self._db.display_name(names)

        require_review = os.getenv("REQUIRE_CLINICAL_REVIEW", "").strip().lower() in ("true", "1", "yes")

        reasons: list[str] = []
        for i in ings:
            if i["drug_class"] not in _KNOWN_CLASSES:
                if i["drug_class"] == "opioid_analgesic":
                    reasons.append(
                        f"Ingredient '{i['generic']}' is an opioid analgesic and requires clinical review."
                    )
                else:
                    reasons.append(
                        f"Ingredient '{i['generic']}' (class '{i['drug_class']}') is outside the "
                        f"supported drug classes and requires confirmation."
                    )
            if require_review and not i.get("clinically_reviewed", False):
                reasons.append(
                    f"Ingredient '{i['generic']}' requires clinical review by a licensed pharmacist before use."
                )

        if seed is not None:
            drug_class, purpose = seed[0], seed[1]
        elif len(ings) == 1:
            drug_class, purpose = ings[0]["drug_class"], ings[0]["purpose_plain"]
        else:
            drug_class = ings[0]["drug_class"]
            purpose = "; ".join(i["purpose_plain"] for i in ings if i["purpose_plain"]) or None

        return MatchResult(
            generic           = generic,
            drug_class        = drug_class,
            purpose_plain     = purpose,
            match_score       = 1.0,
            needs_review      = bool(reasons),
            match_type        = match_type,
            review_reasons    = reasons,
            ingredients       = ings,
        )

    @staticmethod
    def _unknown_result(reasons: list[str], lasa_warning: Optional[str] = None) -> MatchResult:
        """Return a safe 'unknown' result with no guessed generic."""
        all_reasons = list(reasons)
        if lasa_warning and lasa_warning not in all_reasons:
            all_reasons.append(lasa_warning)
        return MatchResult(
            generic           = None,
            drug_class        = "unknown",
            purpose_plain     = None,
            match_score       = 0.0,
            needs_review      = True,
            match_type        = "unknown",
            suggested_generic = None,
            suggested_class   = None,
            suggested_purpose = None,
            review_reasons    = all_reasons,
            lasa_warning      = lasa_warning,
        )
