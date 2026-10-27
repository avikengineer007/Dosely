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

from dosely.auth import create_access_token, get_current_user, require_admin

# Alias for test compatibility — tests import _create_access_token from this module
_create_access_token = create_access_token

from dosely.drug_db import DrugDatabase
from dosely.drug_matcher import DrugMatcher
from dosely.extractor import extract
from dosely.models import IngredientInfo, Medicine, Prescription
from dosely.rate_limiter import rate_limit
from dosely.repository import PrescriptionRepository
from dosely.scheduler import MedicationScheduler
from dosely.scheduler.models import DrugInput
from dosely.storage import EncryptedStorage
from dosely.upload_validator import validate_and_sanitize_upload

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
        db = DrugDatabase.build(strict=True)
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


class FeedbackRequest(BaseModel):
    drug_name: str
    field_name: Optional[str] = None
    issue_type: str = "incorrect_info"
    user_comment: Optional[str] = None


class AdminPurgeRequest(BaseModel):
    retention_days: int = Field(default=30, ge=1, le=365)


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
@router.post(
    "/prescriptions/upload",
    status_code=status.HTTP_200_OK,
    summary="Upload prescription (image or PDF) — /upload alias",
    include_in_schema=False,
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def upload_prescription(
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
    storage: Annotated[EncryptedStorage, Depends(get_storage)],
) -> dict[str, Any]:
    file_bytes = await file.read(_MAX_FILE_BYTES + 1)
    if len(file_bytes) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds 20MB limit.",
        )

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    clean_bytes, clean_mime = validate_and_sanitize_upload(file_bytes, content_type)

    prescription_id = str(uuid.uuid4())
    user_id = str(current_user.get("sub", "anonymous"))

    # Encrypt and save sanitized image at rest
    enc_path = storage.save(prescription_id, clean_bytes)

    # Perform extraction
    try:
        extracted: Prescription = extract(clean_bytes, prescription_id=prescription_id)
        matcher_instance = get_matcher()
        for m in extracted.medicines:
            if m.name_raw:
                m_res = matcher_instance.match(m.name_raw)
                if m_res.ingredients:
                    m.ingredients = [IngredientInfo(**ing) for ing in m_res.ingredients]
                if m_res.match_type in ("exact_generic", "exact_brand"):
                    m.name_generic = m_res.generic
                    if m_res.needs_review:
                        m.needs_review = True
                        for r in m_res.review_reasons:
                            if r not in m.review_reasons:
                                m.review_reasons.append(r)
                if m_res.match_type == "fuzzy_suggestion":
                    m.suggested_generic = m_res.suggested_generic
                    m.suggested_class = m_res.suggested_class
                    m.lasa_warning = m_res.lasa_warning
                elif m_res.lasa_warning:
                    m.lasa_warning = m_res.lasa_warning
        extracted_dict = extracted.model_dump(mode="json")
    except Exception as exc:
        # Graceful fallback: return extraction with needs_human_review=True
        extracted_dict = {
            "prescription_id": prescription_id,
            "image_quality": "poor",
            "medicines": [],
            "needs_human_review": True,
            "review_summary": [f"Extraction error: {exc.__class__.__name__}"],
        }

    # Persist in SQLite
    created = repo.create(
        prescription_id=prescription_id,
        user_id=user_id,
        image_path=enc_path,
        mime_type=clean_mime,
        image_quality=extracted_dict.get("image_quality", "fair"),
        extracted_data=extracted_dict,
    )

    return {
        "id": prescription_id,
        "status": created["status"],
        "confirmed": created["confirmed"],
        "extracted_data": extracted_dict,
        "prescription": extracted_dict,
        "disclaimer": "This is not medical advice.",
    }


@router.get(
    "/prescriptions/{prescription_id}",
    summary="Get prescription details",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
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
        chosen_generic = med.name_generic.strip() if med.name_generic else None
        if chosen_generic and chosen_generic.lower() != "unknown":
            # User explicitly chose or confirmed a generic
            match_res = matcher.match(chosen_generic)
            matched_dict = match_res.to_dict()
            if not matched_dict.get("generic"):
                matched_dict["generic"] = chosen_generic
        else:
            match_res = matcher.match(med.name_raw)
            matched_dict = match_res.to_dict()
            if chosen_generic and chosen_generic.lower() == "unknown":
                matched_dict["generic"] = None
                matched_dict["class"] = "unknown"

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
                ingredients=matched_dict.get("ingredients", []),
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
        "timeline": timeline,
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
        "timeline": rec.get("schedule", []),
        "disclaimer": "This is not medical advice.",
    }


# ---------------------------------------------------------------------------
# Phase 5: Dose Tracking & Actions
# ---------------------------------------------------------------------------

class DoseActionRequest(BaseModel):
    drug_name: str
    scheduled_time: str
    action: str = Field(..., pattern="^(taken|skipped|snoozed)$")
    snooze_minutes: Optional[int] = None
    notes: Optional[str] = None


@router.post(
    "/prescriptions/{prescription_id}/doses/log",
    status_code=status.HTTP_201_CREATED,
    summary="Record taken, skipped, or snoozed action for a dose",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def log_dose_action(
    prescription_id: str,
    payload: DoseActionRequest,
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

    log_id = str(uuid.uuid4())
    logged = repo.log_dose(
        log_id=log_id,
        prescription_id=prescription_id,
        user_id=user_id,
        drug_name=payload.drug_name,
        scheduled_time=payload.scheduled_time,
        action=payload.action,
        snooze_minutes=payload.snooze_minutes,
        notes=payload.notes,
    )

    return {
        "status": "success",
        "dose_log": logged,
        "disclaimer": "This is not medical advice.",
    }


@router.get(
    "/prescriptions/{prescription_id}/doses",
    summary="Get dose history for prescription",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_dose_logs(
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

    logs = repo.get_dose_logs(prescription_id, user_id=user_id)
    return {
        "prescription_id": prescription_id,
        "dose_logs": logs,
        "disclaimer": "This is not medical advice.",
    }


# ---------------------------------------------------------------------------
# Phase 5: Reminders Engine Endpoints
# ---------------------------------------------------------------------------

class GenerateRemindersRequest(BaseModel):
    timezone: str = "Asia/Kolkata"


class SnoozeReminderRequest(BaseModel):
    snooze_minutes: int = 15


@router.post(
    "/prescriptions/{prescription_id}/reminders/generate",
    status_code=status.HTTP_201_CREATED,
    summary="Generate scheduled reminders from confirmed timeline",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def generate_reminders(
    prescription_id: str,
    payload: GenerateRemindersRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
) -> dict[str, Any]:
    from dosely.reminders import ReminderEngine

    user_id = str(current_user.get("sub", "anonymous"))
    rec = repo.get(prescription_id, user_id=user_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prescription '{prescription_id}' not found",
        )

    if not rec.get("confirmed"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prescription has not been confirmed. Confirm before generating reminders.",
        )

    schedule = rec.get("schedule", [])
    reminders = ReminderEngine.generate_reminders_from_timeline(
        prescription_id=prescription_id,
        user_id=user_id,
        timeline=schedule,
        tz_name=payload.timezone,
    )

    repo.save_reminders(reminders)

    return {
        "prescription_id": prescription_id,
        "timezone": payload.timezone,
        "reminders_count": len(reminders),
        "reminders": reminders,
        "disclaimer": "This is not medical advice.",
    }


@router.get(
    "/prescriptions/{prescription_id}/reminders",
    summary="Get scheduled reminders for prescription",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def get_reminders(
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

    reminders = repo.get_reminders(prescription_id, user_id=user_id)
    return {
        "prescription_id": prescription_id,
        "reminders": reminders,
        "disclaimer": "This is not medical advice.",
    }


@router.post(
    "/prescriptions/{prescription_id}/reminders/{reminder_id}/snooze",
    summary="Snooze a scheduled reminder by N minutes",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def snooze_reminder(
    prescription_id: str,
    reminder_id: str,
    payload: SnoozeReminderRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
) -> dict[str, Any]:
    from dosely.reminders import ReminderEngine

    rem = repo.get_reminder(reminder_id)
    if not rem or rem["prescription_id"] != prescription_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reminder '{reminder_id}' not found",
        )

    snoozed_until = ReminderEngine.calculate_snooze(
        rem["fire_at_timestamp"],
        snooze_minutes=payload.snooze_minutes,
    )

    updated = repo.snooze_reminder(reminder_id, snoozed_until_iso=snoozed_until)
    return {
        "status": "snoozed",
        "reminder": updated,
        "disclaimer": "This is not medical advice.",
    }


@router.post(
    "/prescriptions/{prescription_id}/reminders/{reminder_id}/fire",
    summary="Simulate / execute reminder firing (Web Push / FCM delivery)",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def fire_reminder(
    prescription_id: str,
    reminder_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
) -> dict[str, Any]:
    rem = repo.get_reminder(reminder_id)
    if not rem or rem["prescription_id"] != prescription_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Reminder '{reminder_id}' not found",
        )

    updated = repo.fire_reminder(reminder_id)
    return {
        "status": "fired",
        "delivered": True,
        "reminder": updated,
        "notification": rem.get("notification_payload"),
        "disclaimer": "This is not medical advice.",
    }


# ---------------------------------------------------------------------------
# Phase 6: Deletion, Feedback, Admin Purge
# ---------------------------------------------------------------------------

@router.delete(
    "/prescriptions/{prescription_id}",
    summary="Permanently delete a prescription and all associated data",
    dependencies=[Depends(rate_limit(max_requests=30, window_seconds=60))],
)
async def delete_prescription(
    prescription_id: str,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
    storage: Annotated[EncryptedStorage, Depends(get_storage)],
) -> dict[str, Any]:
    """Delete prescription, cascade all DB rows and encrypted image file."""
    user_id = str(current_user.get("sub", "anonymous"))
    deleted = repo.delete_prescription(prescription_id, user_id=user_id, storage=storage)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prescription '{prescription_id}' not found",
        )
    return {"deleted": True, "prescription_id": prescription_id}


@router.post(
    "/prescriptions/{prescription_id}/feedback",
    status_code=status.HTTP_201_CREATED,
    summary="Submit 'this is wrong' feedback for a medicine card (no PII logged)",
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60))],
)
async def submit_feedback(
    prescription_id: str,
    payload: FeedbackRequest,
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
) -> dict[str, Any]:
    """Record field-level feedback without logging any patient-identifying information."""
    user_id = str(current_user.get("sub", "anonymous"))
    rec = repo.get(prescription_id, user_id=user_id)
    if not rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Prescription '{prescription_id}' not found",
        )
    feedback_id = str(uuid.uuid4())
    # SAFETY: user_comment is stored as-is but never logged (Rule 5).
    report = repo.log_feedback(
        feedback_id=feedback_id,
        prescription_id=prescription_id,
        drug_name=payload.drug_name,
        issue_type=payload.issue_type,
        field_name=payload.field_name,
        user_comment=payload.user_comment,
    )
    return {"created": True, "feedback": report}


@router.post(
    "/admin/purge",
    summary="Admin: purge prescription records older than retention_days (default 30)",
    dependencies=[Depends(rate_limit(max_requests=5, window_seconds=60))],
)
async def admin_purge(
    payload: AdminPurgeRequest,
    current_user: Annotated[dict[str, Any], Depends(require_admin)],
    repo: Annotated[PrescriptionRepository, Depends(get_repo)],
    storage: Annotated[EncryptedStorage, Depends(get_storage)],
) -> dict[str, Any]:
    """Delete all prescriptions (and related data) older than retention_days.

    Requires admin role in the JWT payload (role='admin').
    Normal users receive HTTP 403 Forbidden.
    Logs only the count of purged records, never their content or identifiers.
    """
    purged = repo.purge_older_than(days=payload.retention_days, storage=storage)
    return {
        "purged_count": purged,
        "retention_days": payload.retention_days,
    }
