"""
FastAPI Router — Prescription Extraction
==========================================
Endpoints
---------
POST /extract
    Upload a prescription image or PDF and receive a structured Prescription
    JSON object.

POST /extract/batch
    Upload up to 10 files at once. Returns a list of Prescription objects.

GET  /health
    Simple liveness check.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from dosely.extractor import extract
from dosely.models import Prescription

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["prescription"])

# Max file size: 20 MB
_MAX_FILE_BYTES = 20 * 1024 * 1024

# Accepted MIME types
_ACCEPTED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/bmp",
    "application/pdf",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_upload(upload: UploadFile) -> None:
    """Raise HTTPException for invalid content type or oversized files."""
    ct = (upload.content_type or "").split(";")[0].strip().lower()
    if ct not in _ACCEPTED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported content type '{ct}'. "
                f"Accepted types: {sorted(_ACCEPTED_TYPES)}"
            ),
        )


async def _read_safe(upload: UploadFile) -> bytes:
    """Read upload content, enforcing the size limit."""
    data = await upload.read(_MAX_FILE_BYTES + 1)
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {_MAX_FILE_BYTES // (1024 * 1024)} MB limit.",
        )
    return data


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/health", summary="Liveness check")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post(
    "/extract",
    response_model=Prescription,
    summary="Extract structured data from a prescription image or PDF",
    responses={
        200: {"description": "Extraction result (may have needs_human_review=true)"},
        413: {"description": "File too large"},
        415: {"description": "Unsupported file type"},
    },
)
async def extract_prescription(
    file: Annotated[UploadFile, File(description="Prescription image or PDF")],
    prescription_id: Annotated[
        str | None,
        Form(description="Optional stable ID for this extraction run"),
    ] = None,
    pdf_page: Annotated[
        int,
        Form(description="Zero-indexed PDF page to process (PDF only)"),
    ] = 0,
) -> Prescription:
    """
    Upload a prescription image (JPEG, PNG, TIFF, BMP) or PDF and receive
    a structured `Prescription` JSON object.

    The extraction pipeline:
    1. Preprocesses the image (deskew, grayscale, CLAHE).
    2. Calls Claude with the system prompt and requests JSON only.
    3. Validates the response against the `Prescription` Pydantic model.
    4. On validation failure, retries once with the error message.
    5. On second failure, returns a prescription with `needs_human_review=true`.
    """
    _validate_upload(file)
    data = await _read_safe(file)

    loop = asyncio.get_event_loop()
    # Run CPU-bound extraction in a thread pool to avoid blocking the event loop
    fn = partial(extract, data, prescription_id, pdf_page)
    try:
        prescription: Prescription = await loop.run_in_executor(None, fn)
    except Exception as exc:
        logger.exception("Unexpected error during extraction: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Extraction error: {exc}",
        ) from exc

    return prescription


@router.post(
    "/extract/batch",
    summary="Extract structured data from up to 10 prescription files",
    response_class=JSONResponse,
)
async def extract_batch(
    files: Annotated[
        list[UploadFile],
        File(description="Up to 10 prescription images or PDFs"),
    ],
    pdf_page: Annotated[int, Form()] = 0,
) -> JSONResponse:
    """
    Process up to 10 prescription files concurrently.

    Returns a JSON array of `Prescription` objects in the same order as the
    uploaded files.
    """
    if len(files) > 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch endpoint accepts at most 10 files per request.",
        )

    for f in files:
        _validate_upload(f)

    async def _process_one(upload: UploadFile) -> dict:
        data = await _read_safe(upload)
        loop = asyncio.get_event_loop()
        fn = partial(extract, data, None, pdf_page)
        try:
            rx: Prescription = await loop.run_in_executor(None, fn)
            return rx.model_dump(mode="json")
        except Exception as exc:
            logger.exception("Batch extraction error for %s: %s", upload.filename, exc)
            return {
                "error": str(exc),
                "filename": upload.filename,
                "needs_human_review": True,
            }

    results = await asyncio.gather(*[_process_one(f) for f in files])
    return JSONResponse(content=list(results))
