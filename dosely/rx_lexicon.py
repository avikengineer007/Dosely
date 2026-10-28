"""
Prescription Abbreviation and Shorthand Lexicon Normalizer
==========================================================
Provides handwriting-aware shorthand normalization, confusion-pair
disambiguation, and clinical plausibility assessment for strengths.

Safety Principles:
- A plausibility check can ONLY add review flags, never silently fix a number.
- Ambiguous frequencies always yield AMBIGUOUS with candidate choices;
  the scheduler gets NO default timings.
- The normalizer never invents a frequency not present in the transcription.
- No health data or PII in logs.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

AMBIGUOUS = "AMBIGUOUS"

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_LEXICON_PATH = _DATA_DIR / "rx_lexicon.json"


@dataclass
class CanonicalFrequency:
    canonical: Optional[str] = None
    status: str = "resolved"  # "resolved" | "AMBIGUOUS" | "unrecognized"
    timing_slots: Optional[list[str]] = None
    candidates: list[str] = field(default_factory=list)


@dataclass
class StrengthAssessment:
    is_plausible: bool = True
    review_required: bool = False
    reason: str = ""


class RxLexiconManager:
    _instance: Optional[RxLexiconManager] = None

    def __init__(self, lexicon_path: Path = _LEXICON_PATH) -> None:
        self.lexicon_path = lexicon_path
        self._frequencies: dict[str, dict] = {}
        self._confusions: dict[str, dict] = {}
        self._plausible_strengths: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.lexicon_path.exists():
            return
        try:
            with open(self.lexicon_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._frequencies = data.get("frequencies", {})
            self._confusions = data.get("confusions", {})
            self._plausible_strengths = data.get("plausible_strengths", {})
        except Exception:
            logger.warning("Failed to load prescription lexicon data.")

    @classmethod
    def get_instance(cls) -> RxLexiconManager:
        if cls._instance is None:
            cls._instance = RxLexiconManager()
        return cls._instance

    def normalize_frequency(self, raw_input: str) -> CanonicalFrequency:
        if not raw_input or not raw_input.strip():
            return CanonicalFrequency(status="unrecognized")

        cleaned = raw_input.strip()
        lower_cleaned = cleaned.lower()

        # 1. Check handwriting confusion cases first
        for confusion_key, conf_data in self._confusions.items():
            if lower_cleaned == confusion_key.lower():
                return CanonicalFrequency(
                    canonical=None,
                    status=AMBIGUOUS,
                    timing_slots=None,
                    candidates=list(conf_data.get("candidates", [])),
                )

        # 2. Check direct canonical frequency codes and variants
        for code, info in self._frequencies.items():
            if cleaned.upper() == code:
                return CanonicalFrequency(
                    canonical=info["canonical"],
                    status="resolved",
                    timing_slots=list(info["timing_slots"]) if info.get("timing_slots") else None,
                )
            variants = [v.lower() for v in info.get("variants", [])]
            if lower_cleaned in variants:
                return CanonicalFrequency(
                    canonical=info["canonical"],
                    status="resolved",
                    timing_slots=list(info["timing_slots"]) if info.get("timing_slots") else None,
                )

        # 3. Normalized regex matching for hyphen / plus dose patterns
        # e.g., "1-0-1", "1+0+1"
        pattern_norm = re.sub(r"\s+", "", cleaned)
        if pattern_norm in ["1-0-1", "1+0+1"]:
            bd_info = self._frequencies.get("BD", {})
            return CanonicalFrequency(
                canonical=bd_info.get("canonical", "Twice daily"),
                status="resolved",
                timing_slots=["morning", "night"],
            )
        if pattern_norm in ["1-1-1", "1+1+1"]:
            tds_info = self._frequencies.get("TDS", {})
            return CanonicalFrequency(
                canonical=tds_info.get("canonical", "Three times daily"),
                status="resolved",
                timing_slots=["morning", "afternoon", "night"],
            )
        if pattern_norm in ["0-0-1", "0+0+1"]:
            hs_info = self._frequencies.get("HS", {})
            return CanonicalFrequency(
                canonical="Once daily at night",
                status="resolved",
                timing_slots=["night"],
            )
        if pattern_norm in ["1-0-0", "0-1-0"]:
            od_info = self._frequencies.get("OD", {})
            return CanonicalFrequency(
                canonical=od_info.get("canonical", "Once daily"),
                status="resolved",
                timing_slots=["morning"],
            )

        # Never invent a frequency that is not recognized
        return CanonicalFrequency(canonical=None, status="unrecognized", timing_slots=None)

    def assess_strength(self, drug_name: str, strength_str: str) -> StrengthAssessment:
        if not drug_name or not strength_str:
            return StrengthAssessment(is_plausible=True, review_required=False)

        # Parse numerical value and unit
        match = re.search(r"([\d\.]+)\s*([a-zA-Z%]+)", strength_str)
        if not match:
            return StrengthAssessment(is_plausible=True, review_required=False)

        try:
            val = float(match.group(1))
            unit = match.group(2).lower()
        except ValueError:
            return StrengthAssessment(is_plausible=True, review_required=False)

        norm_drug = drug_name.strip().lower()
        rule = self._plausible_strengths.get(norm_drug)
        if not rule:
            return StrengthAssessment(is_plausible=True, review_required=False)

        expected_unit = rule.get("unit", "").lower()
        allowed_values = rule.get("allowed_values", [])

        # Check unit mismatch / confusion (e.g. mcg vs mg, or g vs mg)
        if unit != expected_unit:
            # Unit confusion detection
            if expected_unit == "mcg" and unit == "mg":
                return StrengthAssessment(
                    is_plausible=False,
                    review_required=True,
                    reason=f"Unit confusion alert: Expected {expected_unit} but found {unit} (potential 1000x overdose)",
                )
            if expected_unit == "mg" and unit == "g":
                return StrengthAssessment(
                    is_plausible=False,
                    review_required=True,
                    reason=f"Unit confusion alert: Expected {expected_unit} but found {unit}",
                )
            return StrengthAssessment(
                is_plausible=False,
                review_required=True,
                reason=f"Unit mismatch for {norm_drug}: expected {expected_unit}, got {unit}",
            )

        # Check value plausibility against known allowed strengths
        if allowed_values and val not in allowed_values:
            max_dose = rule.get("max_single_dose_mg")
            if max_dose and unit == "mg" and val > max_dose:
                return StrengthAssessment(
                    is_plausible=False,
                    review_required=True,
                    reason=f"Implausible strength: {val} {unit} exceeds safe maximum dose ({max_dose} mg)",
                )
            if unit == "mcg" and val > 500:
                return StrengthAssessment(
                    is_plausible=False,
                    review_required=True,
                    reason=f"Implausible strength: {val} {unit} exceeds safe guidelines",
                )

        return StrengthAssessment(is_plausible=True, review_required=False)


def normalize_handwritten_frequency(raw_input: str) -> CanonicalFrequency:
    return RxLexiconManager.get_instance().normalize_frequency(raw_input)


def assess_strength_plausibility(drug_name: str, strength_str: str) -> StrengthAssessment:
    return RxLexiconManager.get_instance().assess_strength(drug_name, strength_str)
