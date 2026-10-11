"""
dosely/api/schemas.py
======================
Pydantic schemas for the Dosely API endpoints:
- Upload responses
- Confirmation requests & validated medicine items
- Schedule responses
- JWT Auth token requests & responses
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    username: str = Field(default="demo_clinician")
    password: str = Field(default="secret")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str


class ConfirmedMedicineItem(BaseModel):
    name_raw: str = Field(..., min_length=1, description="Raw drug name as read/edited")
    name_generic: Optional[str] = Field(default=None, description="Resolved generic ingredient")
    drug_class: Optional[str] = Field(default=None, description="Therapeutic class")
    purpose: Optional[str] = Field(default=None, description="Clinical purpose")
    strength: Optional[str] = Field(default=None, description="Dose strength (e.g. '500 mg')")
    form: Optional[str] = Field(default="tablet", description="Dose form")
    frequency_raw: Optional[str] = Field(default=None, description="Frequency as written (e.g. 'BD', 'TDS')")
    frequency_normalized: Optional[str] = Field(default=None, description="Normalized frequency description")
    timing_slots: Optional[list[str]] = Field(default=None, description="Time slots: morning, afternoon, night...")
    duration_days: Optional[int] = Field(default=None, ge=1, description="Course length in days")
    food_instruction: Optional[str] = Field(default="after_food", description="Food instruction")
    special_instructions: Optional[str] = Field(default=None)
    user_reviewed: bool = Field(default=True, description="Whether the user reviewed this item")
    user_accepted: bool = Field(default=True, description="Whether the item was explicitly verified/accepted")


class ConfirmPrescriptionRequest(BaseModel):
    medicines: list[ConfirmedMedicineItem] = Field(
        ...,
        min_length=1,
        description="List of verified and confirmed medicines",
    )
    patient_name: Optional[str] = Field(default=None)
    prescription_date: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)


class PrescriptionDetailResponse(BaseModel):
    prescription_id: str
    status: str
    image_quality: str
    medicines: list[dict[str, Any]]
    prescriber: Optional[dict[str, Any]] = None
    prescription_date: Optional[str] = None
    patient_name: Optional[str] = None
    overall_confidence: Optional[float] = None
    needs_human_review: bool
    review_summary: list[str]
    image_url: str
    created_at: str
    confirmed_at: Optional[str] = None


class ScheduleTimelineItem(BaseModel):
    time: str
    drug: str
    instruction: str
    source: str
    suggested: bool
    warnings: list[str] = Field(default_factory=list)
    drug_class: str = ""


class PrescriptionScheduleResponse(BaseModel):
    prescription_id: str
    status: str
    generated_at: str
    timeline: list[ScheduleTimelineItem]
    warnings_and_conflicts: list[str] = Field(default_factory=list)
