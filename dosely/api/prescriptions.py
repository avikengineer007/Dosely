"""
dosely/api/prescriptions.py
===========================
FastAPI endpoints for Phase 4 Prescription workflow:
  - POST /prescriptions               (upload & extract)
  - GET  /prescriptions/{id}          (retrieve prescription & status)
  - PUT  /prescriptions/{id}/confirm  (confirm fields, then trigger matching & scheduling)
  - GET  /prescriptions/{id}/schedule (get daily timeline — REFUSES unconfirmed prescriptions)
  - GET  /prescriptions/{id}/image    (stream decrypted image for confirm screen)
  - POST /auth/token                  (JWT login / token generation)
"""

from __future__ import annotations

import base64
import io
import uuid
from typing import Annotated, Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from dosely.auth import create_access_token, get_current_user
from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import DrugMatcher
from dosely.extractor import extract
from dosely.models import Medicine, Prescription
from dosely.rate_limiter import rate_limit
from dosely.repository import PrescriptionRepository
from dosely.scheduler import MedicationScheduler
from dosely.scheduler.models import DrugInput
from dosely.storage import EncryptedStorage

router = APIRouter(prefix="", tags=["prescriptions"])

# Shared singletons
_repo: Optional[PrescriptionRepository] = None
_storage: Optional[EncryptedStorage] = None
_matcher: Optional[DrugMatcher] = None
_scheduler: Optional[MedicationScheduler] = None


def get_repo() -> PrescriptionRepository:
    global _repo
    if _repo is None:
        _repo = PrescriptionRepository()
    return _repo


def get_storage() -> EncryptedStorage:
    global _storage
    if _storage is None:
        _storage = EncryptedStorage()
    return _storage


def get_matcher() -> DrugMatcher:
    global _matcher
    if _matcher is None:
        db = DrugDatabase.build()
        _matcher = DrugMatcher(db)
    return _matcher


def get_scheduler() -> MedicationScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = MedicationScheduler()
    return _scheduler


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------

class TokenRequest(BaseModel):
    username: str
    password: Optional[str] = "password"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ConfirmedMedicineInput(BaseModel):
    name_raw: str
    name_generic: Optional[str] = None
    strength: Optional[str] = None
    form: Optional[str] = None
    frequency_raw: Optional[str] = None
    duration_days: Optional[int] = None
    food_instruction: Optional[str] = None
    user_accepted: bool = True


class ConfirmPrescriptionRequest(BaseModel):
    medicines: list[ConfirmedMedicineInput] = Field(..., min_length=1)
    notes: Optional[str] = None


# Max file size: 20 MB
_MAX_FILE_BYTES = 20 * 1024 * 1024
_ACCEPTED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/bmp",
    "application/pdf",
}


# ---------------------------------------------------------------------------
# Auth endpoint
# ---------------------------------------------------------------------------

@router.post("/auth/token", response_model=TokenResponse, summary="Generate JWT Access Token")
async def login_for_access_token(payload: TokenRequest) -> TokenResponse:
    if not payload.username.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username must not be empty",
        )
    token = create_access_token(data={"sub": payload.username.strip()})
    return TokenResponse(access_token=token)


# ---------------------------------------------------------------------------
# Prescriptions endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/prescriptions",
    status_code=status.HTTP_201_CREATED,
    summary="Upload prescription (image or PDF)",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def upload_prescription(
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
    storage: Annotated[EncryptedStorage, Depends(get_storage)],
) -> dict[str, Any]:
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type not in _ACCEPTED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file type '{content_type}'. Must be one of {_ACCEPTED_MIME_TYPES}",
        )

    file_bytes = await file.read(_MAX_FILE_BYTES + 1)
    if len(file_bytes) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds 20MB limit.",
        )

    prescription_id = str(uuid.uuid4())
    user_id = str(current_user.get("sub", "anonymous"))

    # Encrypt and save image at rest
    enc_path = storage.save(prescription_id, file_bytes)

    # Perform extraction
    try:
        extracted: Prescription = extract(file_bytes, prescription_id=prescription_id)
        extracted_dict = extracted.model_dump(mode="json")
    except Exception as exc:
        # Graceful fallback: return extraction with needs_human_review=True
        extracted_dict = {
            "prescription_id": prescription_id,
            "image_quality": "poor",
            "medicines": [],
            "needs_human_review": True,
            "review_summary": [f"Extraction error: {exc}"],
        }

    # Persist in SQLite
    created = repo.create(
        prescription_id=prescription_id,
        user_id=user_id,
        image_path=enc_path,
        mime_type=content_type,
        image_quality=extracted_dict.get("image_quality", "fair"),
        extracted_data=extracted_dict,
    )

    return {
        "id": prescription_id,
        "status": created["status"],
        "confirmed": created["confirmed"],
        "prescription": extracted_dict,
        "disclaimer": "This is not medical advice.",
    }


@router.get(
    "/prescriptions/{prescription_id}",
    summary="Get prescription details",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_prescription(
    prescription_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
) -> dict[str, Any]:
    user_id = str(current_user.get("sub", "anonymous"))
    rec = repo.get(prescription_id, user_id=user_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prescription '{prescription_id}' not found",
        )

    return {
        "id": rec["id"],
        "status": rec["status"],
        "confirmed": rec["confirmed"],
        "image_quality": rec["image_quality"],
        "mime_type": rec["mime_type"],
        "extracted_data": rec["extracted_data"],
        "confirmed_data": rec["confirmed_data"],
        "matched_drugs": rec["matched_drugs"],
        "disclaimer": "This is not medical advice.",
    }


@router.get(
    "/prescriptions/{prescription_id}/image",
    summary="Get decrypted original prescription image",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_prescription_image(
    prescription_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
    storage: Annotated[EncryptedStorage, Depends(get_storage)],
) -> Response:
    user_id = str(current_user.get("sub", "anonymous"))
    rec = repo.get(prescription_id, user_id=user_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prescription '{prescription_id}' not found",
        )

    decrypted_bytes = storage.load(rec["image_path"])
    return Response(content=decrypted_bytes, media_type=rec["mime_type"])


@router.put(
    "/prescriptions/{prescription_id}/confirm",
    summary="Confirm extracted prescription data (triggers matching & scheduling)",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def confirm_prescription(
    prescription_id: str,
    payload: ConfirmPrescriptionRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
    matcher: Annotated[DrugMatcher, Depends(get_matcher)],
    scheduler: Annotated[MedicationScheduler, Depends(get_scheduler)],
) -> dict[str, Any]:
    """
    Global Safety Rule 2:
    Nothing is matched or scheduled until the user has confirmed the extracted data.
    """
    user_id = str(current_user.get("sub", "anonymous"))
    rec = repo.get(prescription_id, user_id=user_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prescription '{prescription_id}' not found",
        )

    # 1. Run DrugMatcher on confirmed medicines
    matched_results: list[dict[str, Any]] = []
    scheduler_inputs: list[DrugInput] = []

    for med in payload.medicines:
        match_res = matcher.match(med.name_raw)
        matched_dict = match_res.to_dict()
        # If user explicitly supplied generic or food instruction, preserve it
        if med.name_generic and not matched_dict.get("generic"):
            matched_dict["generic"] = med.name_generic

        matched_results.append({
            "name_raw": med.name_raw,
            "strength": med.strength,
            "frequency_raw": med.frequency_raw,
            "duration_days": med.duration_days,
            "food_instruction": med.food_instruction,
            "matching": matched_dict,
        })

        scheduler_inputs.append(
            DrugInput(
                name=matched_dict.get("generic") or med.name_raw,
                drug_class=matched_dict.get("class", "unknown"),
                frequency_raw=med.frequency_raw,
                food_relation=med.food_instruction,
            )
        )

    # 2. Run deterministic MedicationScheduler
    timeline = scheduler.schedule_timeline(scheduler_inputs)

    # 3. Save confirmed data, matches, and schedule
    confirmed_data_dict = payload.model_dump()
    updated = repo.confirm(
        prescription_id=prescription_id,
        user_id=user_id,
        confirmed_data=confirmed_data_dict,
        matched_drugs=matched_results,
        schedule=timeline,
    )

    return {
        "id": prescription_id,
        "status": "confirmed",
        "confirmed": True,
        "matched_drugs": matched_results,
        "schedule": timeline,
        "disclaimer": "This is not medical advice.",
    }


@router.get(
    "/prescriptions/{prescription_id}/schedule",
    summary="Get scheduled medication timeline (refuses unconfirmed prescriptions)",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_prescription_schedule(
    prescription_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
) -> dict[str, Any]:
    """
    Exit Check Rule:
    Refuses to provide a schedule if the prescription is not confirmed.
    """
    user_id = str(current_user.get("sub", "anonymous"))
    rec = repo.get(prescription_id, user_id=user_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prescription '{prescription_id}' not found",
        )

    # Strict check: Must be confirmed!
    if not rec.get("confirmed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prescription has not been confirmed. Nothing is matched or scheduled until user confirms.",
        )

    return {
        "id": rec["id"],
        "status": "confirmed",
        "confirmed": True,
        "schedule": rec.get("schedule", []),
        "disclaimer": "This is not medical advice.",
    }
