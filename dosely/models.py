"""
Prescription Assistant — Pydantic Models
========================================
Data contracts for structured extraction of prescription information.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class DoseForm(str, Enum):
    TABLET      = "tablet"
    CAPSULE     = "capsule"
    SYRUP       = "syrup"
    SUSPENSION  = "suspension"
    INJECTION   = "injection"
    DROPS       = "drops"
    OINTMENT    = "ointment"
    CREAM       = "cream"
    PATCH       = "patch"
    INHALER     = "inhaler"
    SUPPOSITORY = "suppository"
    POWDER      = "powder"
    OTHER       = "other"
    UNKNOWN     = "unknown"


class DrugClass(str, Enum):
    PAINKILLER       = "painkiller"
    ANTACID          = "antacid"
    ANTIBIOTIC       = "antibiotic"
    ANTIHISTAMINE    = "antihistamine"
    ANTIDIABETIC     = "antidiabetic"
    ANTIHYPERTENSIVE = "antihypertensive"
    ANTIFUNGAL       = "antifungal"
    ANTIVIRAL        = "antiviral"
    STEROID          = "steroid"
    VITAMIN_SUPPLEMENT = "vitamin_supplement"
    LAXATIVE         = "laxative"
    ANTIDEPRESSANT   = "antidepressant"
    ANTICOAGULANT    = "anticoagulant"
    BRONCHODILATOR   = "bronchodilator"
    OTHER            = "other"
    UNKNOWN          = "unknown"


class FoodInstruction(str, Enum):
    BEFORE_FOOD   = "before_food"
    AFTER_FOOD    = "after_food"
    WITH_FOOD     = "with_food"
    EMPTY_STOMACH = "empty_stomach"
    ANY_TIME      = "any_time"
    UNKNOWN       = "unknown"


class ImageQuality(str, Enum):
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


# ---------------------------------------------------------------------------
# Medicine model
# ---------------------------------------------------------------------------

class Medicine(BaseModel):
    """
    A single medicine entry extracted from a prescription.

    Raw fields preserve exactly what was read from the image.
    Normalised fields contain cleaned/mapped values.
    confidence: 0-1 aggregate OCR + NLP confidence for this entry.
    needs_review is auto-set by model_validator; do not set manually.
    """

    # Raw OCR
    name_raw: str = Field(
        ...,
        description="Exact text of the drug name as read from the prescription.",
    )

    # Normalised / enriched
    name_generic: Optional[str] = Field(
        default=None,
        description="INN of active ingredient(s). None when unresolvable.",
    )
    drug_class: Optional[DrugClass] = Field(
        default=None,
        description="Therapeutic class. None when unknown.",
    )
    purpose: Optional[str] = Field(
        default=None,
        description="Plain-language explanation (<=15 words). None when drug_class is UNKNOWN.",
    )
    strength: Optional[str] = Field(
        default=None,
        description="Dose strength as written (e.g. '500 mg'). None when absent/illegible.",
    )
    form: DoseForm = Field(
        default=DoseForm.UNKNOWN,
        description="Dosage form inferred from name or instruction.",
    )

    # Frequency
    frequency_raw: Optional[str] = Field(
        default=None,
        description="Frequency exactly as written (e.g. 'BD', '1-0-1'). None when absent.",
    )
    frequency_normalized: Optional[str] = Field(
        default=None,
        description="Human-readable normalised schedule. None when unresolvable.",
    )
    timing_slots: Optional[list[str]] = Field(
        default=None,
        description="Admin times: morning|afternoon|evening|night|as_needed.",
    )

    duration_days: Optional[int] = Field(
        default=None,
        ge=1,
        description="Course length in days. None when not specified.",
    )
    food_instruction: FoodInstruction = Field(
        default=FoodInstruction.UNKNOWN,
    )
    special_instructions: Optional[str] = Field(
        default=None,
        description="Additional verbatim instructions.",
    )

    # Quality signals
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        ...,
        description="Extraction confidence in [0, 1].",
    )
    needs_review: bool = Field(
        default=False,
        description="Auto-set True when critical fields are missing or confidence < 0.70.",
    )
    review_reasons: list[str] = Field(
        default_factory=list,
        description="Machine-generated list of review reasons.",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("name_raw")
    @classmethod
    def name_raw_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name_raw must not be empty.")
        return v

    @model_validator(mode="after")
    def auto_needs_review(self) -> "Medicine":
        reasons: list[str] = list(self.review_reasons)

        if self.name_generic is None:
            reasons.append("Generic name could not be resolved from raw name.")
        if self.strength is None:
            reasons.append("Strength is missing or illegible.")
        if self.frequency_normalized is None:
            reasons.append("Frequency is missing or could not be normalised.")
        if self.duration_days is None:
            reasons.append("Duration not specified.")
        if self.confidence < 0.70:
            reasons.append(
                f"Overall confidence ({self.confidence:.2f}) is below threshold (0.70)."
            )

        if reasons:
            self.needs_review = True
            self.review_reasons = reasons

        return self


# ---------------------------------------------------------------------------
# Prescriber sub-model
# ---------------------------------------------------------------------------

class Prescriber(BaseModel):
    name: Optional[str] = None
    registration_number: Optional[str] = None
    clinic_or_hospital: Optional[str] = None
    contact: Optional[str] = None


# ---------------------------------------------------------------------------
# Prescription model
# ---------------------------------------------------------------------------

class Prescription(BaseModel):
    """Top-level model for a fully-parsed prescription."""

    prescription_id: str = Field(
        ...,
        description="Unique identifier for this extraction run.",
    )
    image_quality: ImageQuality = Field(
        ...,
        description="Assessed quality: good | fair | poor.",
    )
    medicines: list[Medicine] = Field(
        default_factory=list,
        description="Ordered list of extracted medicines. May be empty on failure.",
    )
    prescriber: Optional[Prescriber] = Field(
        default=None,
        description="Prescriber info if legible; None otherwise.",
    )
    prescription_date: Optional[date] = Field(
        default=None,
        description="Date on the prescription (ISO 8601). None if absent.",
    )
    patient_name: Optional[str] = Field(
        default=None,
        description="Patient name ONLY if explicitly printed. Never inferred.",
    )
    raw_text: Optional[str] = Field(
        default=None,
        description="Full OCR text dump before parsing.",
        repr=False,
    )
    overall_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Mean confidence across all medicines. Auto-computed if absent.",
    )
    needs_human_review: bool = Field(
        default=False,
        description="True when image is POOR, any medicine needs_review, or list is empty.",
    )
    review_summary: list[str] = Field(
        default_factory=list,
        description="Prescription-level reasons for human review.",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def compute_aggregates(self) -> "Prescription":
        reasons: list[str] = list(self.review_summary)

        if self.overall_confidence is None and self.medicines:
            self.overall_confidence = round(
                sum(m.confidence for m in self.medicines) / len(self.medicines), 4
            )

        if self.image_quality == ImageQuality.POOR:
            reasons.append("Image quality is POOR; OCR accuracy is unreliable.")
            self.needs_human_review = True

        if not self.medicines:
            reasons.append("No medicines could be extracted from the prescription.")
            self.needs_human_review = True

        for med in self.medicines:
            if med.needs_review:
                reasons.append(
                    f"Medicine '{med.name_raw}' requires review: "
                    + "; ".join(med.review_reasons)
                )
                self.needs_human_review = True

        if reasons:
            self.review_summary = reasons

        return self

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def medicines_needing_review(self) -> list[Medicine]:
        return [m for m in self.medicines if m.needs_review]

    def dosing_schedule(self) -> dict[str, list[str]]:
        """Return slot -> [medicine labels] for daily reminder view."""
        schedule: dict[str, list[str]] = {}
        for med in self.medicines:
            if not med.timing_slots:
                continue
            label = med.name_generic or med.name_raw
            if med.strength:
                label = f"{label} {med.strength}"
            for slot in med.timing_slots:
                schedule.setdefault(slot, []).append(label)
        return schedule
