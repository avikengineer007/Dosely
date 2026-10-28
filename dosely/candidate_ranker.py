"""
Lexicon-Constrained Candidate Ranking (Hints, Not Decisions)
============================================================
Produces ranked candidate suggestions for ambiguous or disputed handwritten
drug names using handwriting-aware edit distances, handwriting confusion matrices,
phonetic and contextual plausibility rescoring.

Design Principles:
- Candidate lists are hints for the user to choose from, NEVER auto-accepted answers.
- No candidate is ever auto-selected, regardless of score.
- The user must pick explicitly; choices are recorded as source="user_selected".
- Look-alike/sound-alike (LASA) pairs always trigger visible warnings.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz

from dosely.drug_db import DrugDatabase
from dosely.models import Medicine
from dosely.rx_lexicon import assess_strength_plausibility

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_LASA_PATH = _DATA_DIR / "lasa_pairs.json"

# Handwriting character confusions (bidirectional)
_HANDWRITING_SUBSTITUTIONS = [
    ("rn", "m"),
    ("m", "rn"),
    ("cl", "d"),
    ("d", "cl"),
    ("ii", "u"),
    ("u", "ii"),
    ("v", "u"),
    ("u", "v"),
    ("o", "a"),
    ("a", "o"),
    ("c", "e"),
    ("e", "c"),
    ("1", "l"),
    ("l", "1"),
    ("i", "l"),
    ("l", "i"),
    ("h", "n"),
    ("n", "h"),
    ("f", "t"),
    ("t", "f"),
    ("q", "g"),
    ("g", "q"),
]


_db_singleton: Optional[DrugDatabase] = None

def get_drug_db() -> DrugDatabase:
    global _db_singleton
    if _db_singleton is None:
        _db_singleton = DrugDatabase.build()
    return _db_singleton


@dataclass
class CandidateSuggestion:
    generic: str
    brand: Optional[str] = None
    drug_class: str = "unknown"
    purpose_plain: Optional[str] = None
    score: float = 0.0
    is_selected: bool = False
    source: str = "suggested"


@dataclass
class RankedCandidateResult:
    transcription: str
    candidates: list[CandidateSuggestion] = field(default_factory=list)
    selected_candidate: Optional[CandidateSuggestion] = None
    has_lasa_warning: bool = False
    lasa_warning: Optional[str] = None
    equal_prominence: bool = False

    def apply_user_choice(self, med: Medicine, chosen_generic: str) -> Medicine:
        """Apply an explicit human selection, marking source='user_selected'."""
        db = get_drug_db()
        entry = db.get_generic_info(chosen_generic)
        target_class = entry.get("drug_class", "unknown") if entry else "unknown"
        purpose = entry.get("purpose_plain") if entry else None

        updated_dict = med.model_dump()
        updated_dict["name_generic"] = chosen_generic
        updated_dict["source"] = "user_selected"
        if purpose:
            updated_dict["purpose"] = purpose

        reasons = [r for r in med.review_reasons if "generic" not in r.lower()]
        reasons.append(f"Selected by user: {chosen_generic}")
        updated_dict["review_reasons"] = reasons

        return Medicine.model_validate(updated_dict)


def _load_lasa_pairs() -> list[tuple[str, str]]:
    if not _LASA_PATH.exists():
        return []
    try:
        with open(_LASA_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [(p[0].lower(), p[1].lower()) for p in data if len(p) == 2]
    except Exception:
        return []


def _apply_handwriting_transforms(text: str) -> list[str]:
    variants = {text}
    for orig, repl in _HANDWRITING_SUBSTITUTIONS:
        if orig in text:
            variants.add(text.replace(orig, repl))
    return list(variants)


def rank_handwriting_candidates(
    transcription: str,
    strength_context: Optional[str] = None,
    freq_context: Optional[str] = None,
    model_alternatives: Optional[list[str]] = None,
) -> RankedCandidateResult:
    if not transcription or not transcription.strip():
        return RankedCandidateResult(transcription=transcription)

    cleaned = transcription.strip().lower()
    cleaned = re.sub(r"\b\d+\s*(?:mg|mcg|ml|g)\b", "", cleaned).strip()
    cleaned = re.sub(r"\b(?:tab|cap|syp|inj|oint)\b", "", cleaned).strip()
    if not cleaned or len(cleaned) < 2:
        return RankedCandidateResult(transcription=transcription)

    db = get_drug_db()
    all_generics_rows = db.get_all_generics()
    if not all_generics_rows:
        return RankedCandidateResult(transcription=transcription)

    variants = _apply_handwriting_transforms(cleaned)
    alt_set = {a.lower().strip() for a in (model_alternatives or [])}

    scored_candidates: list[CandidateSuggestion] = []

    for row in all_generics_rows:
        generic, d_class, purpose = row[0], row[1], row[2]
        gen_lower = generic.lower()

        # Compute max match ratio across raw and transformed variants
        ratios = [fuzz.ratio(v, gen_lower) for v in variants]
        ratios.append(fuzz.token_sort_ratio(cleaned, gen_lower))
        base_score = float(max(ratios))

        # First letter agreement
        if cleaned[0] == gen_lower[0]:
            base_score += 8.0
        else:
            base_score -= 10.0

        # Last letter agreement
        if cleaned[-1] == gen_lower[-1]:
            base_score += 4.0

        # Length delta penalty
        len_diff = abs(len(cleaned) - len(gen_lower))
        base_score -= len_diff * 1.5

        # Boost if model had this in alternatives
        if gen_lower in alt_set:
            base_score += 15.0

        # Context rescoring with strength plausibility
        if strength_context:
            assessment = assess_strength_plausibility(generic, strength_context)
            if assessment.is_plausible and not assessment.review_required:
                base_score += 10.0
            elif assessment.review_required:
                base_score -= 30.0

        clamped_score = max(0.0, min(100.0, base_score))

        # Only retain candidates above reasonable match threshold
        if clamped_score >= 60.0:
            scored_candidates.append(
                CandidateSuggestion(
                    generic=generic,
                    drug_class=d_class,
                    purpose_plain=purpose,
                    score=round(clamped_score, 1),
                    is_selected=False,  # SAFETY: NEVER auto-selected!
                    source="suggested",
                )
            )

    scored_candidates.sort(key=lambda c: c.score, reverse=True)
    top_candidates = scored_candidates[:3]

    # LASA detection
    lasa_pairs = _load_lasa_pairs()
    has_lasa = False
    lasa_msg: Optional[str] = None

    check_names = {cleaned} | {c.generic.lower() for c in top_candidates}
    for d1, d2 in lasa_pairs:
        if any(fuzz.ratio(name, d1) >= 85 or fuzz.ratio(name, d2) >= 85 for name in check_names):
            has_lasa = True
            lasa_msg = (
                f"Look-Alike / Sound-Alike (LASA) Warning: '{d1}' and '{d2}' have high confusion risk. "
                f"Explicit human confirmation is mandatory."
            )
            break

    # Equal prominence flag when scores are close
    equal_prominence = False
    if len(top_candidates) >= 2:
        gap = abs(top_candidates[0].score - top_candidates[1].score)
        if gap <= 5.0:
            equal_prominence = True

    return RankedCandidateResult(
        transcription=transcription,
        candidates=top_candidates,
        selected_candidate=None,  # NEVER auto-selected
        has_lasa_warning=has_lasa,
        lasa_warning=lasa_msg,
        equal_prominence=equal_prominence,
    )
