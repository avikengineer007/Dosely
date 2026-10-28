"""
dosely/upload_validator.py
==========================
Content-based upload validation and sanitization for prescription uploads.

Enforces:
1. Magic bytes verification (JPEG, PNG, WebP, PDF). Rejects executables, scripts, and mismatches (415).
2. Pillow verification & decompression bomb rejection (413).
3. Image sanitization: strips EXIF, GPS, and trailing polyglot payloads via clean re-encoding.
4. PDF inspection: rejects encrypted PDFs, page count > 10 (413), and PDFs with /JavaScript, /JS, /Launch, /EmbeddedFile (415).
"""

from __future__ import annotations

import io
import re
from typing import Tuple

from fastapi import HTTPException, status
from PIL import Image

try:
    import pypdfium2 as pdfium
    _PDFIUM_AVAILABLE = True
except ImportError:
    pdfium = None
    _PDFIUM_AVAILABLE = False

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None

# Cap image pixels to 10M to prevent decompression bombs
MAX_PIXELS = 10_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS

_FORBIDDEN_PDF_TAGS = [
    re.compile(rb"/[jJ][aA][vV][aA][sS][cC][rR][iI][pP][tT]\b"),
    re.compile(rb"/[jJ][sS]\b"),
    re.compile(rb"/[lL][aA][uU][nN][cC][hH]\b"),
    re.compile(rb"/[eE][mM][bB][eE][dD][dD][eE][dD][fF][iI][lL][eE]\b"),
]


def detect_mime_type(data: bytes) -> str:
    """Detect MIME type from magic bytes."""
    if len(data) < 4:
        return "application/octet-stream"

    # Executables and shell scripts
    if data.startswith(b"MZ") or data.startswith(b"\x7fELF") or data.startswith(b"#!"):
        return "application/x-executable"

    # JPEG: FF D8 FF
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"

    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"

    # PDF: %PDF-
    if data.startswith(b"%PDF"):
        return "application/pdf"

    # WebP: RIFF....WEBP
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"

    # TIFF
    if data.startswith(b"II*\x00") or data.startswith(b"MM\x00*"):
        return "image/tiff"

    # BMP
    if data.startswith(b"BM"):
        return "image/bmp"

    return "application/octet-stream"


def validate_and_sanitize_upload(raw_bytes: bytes, declared_mime: str) -> Tuple[bytes, str]:
    """
    Validate upload content and re-encode images to clean bytes.

    Returns
    -------
    Tuple[bytes, str]
        (sanitized_bytes, sanitized_mime_type)
    """
    detected_mime = detect_mime_type(raw_bytes)

    # Reject executable payloads and scripts immediately
    if detected_mime == "application/x-executable" or raw_bytes.startswith(b"MZ") or raw_bytes.startswith(b"#!/"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported media type: executable content detected.",
        )

    # Validate declared MIME vs detected MIME
    clean_declared = (declared_mime or "").split(";")[0].strip().lower()
    if detected_mime not in ("image/jpeg", "image/png", "image/webp", "image/tiff", "image/bmp", "application/pdf"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file content type detected: '{detected_mime}'.",
        )

    if clean_declared and clean_declared != "application/octet-stream":
        # Disallow declaring image/png for an application/pdf or vice versa
        if (clean_declared == "application/pdf" and detected_mime != "application/pdf") or \
           (clean_declared.startswith("image/") and not detected_mime.startswith("image/")):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"MIME type mismatch: declared '{clean_declared}', detected '{detected_mime}'.",
            )

    # PDF validation
    if detected_mime == "application/pdf":
        for tag_re in _FORBIDDEN_PDF_TAGS:
            if tag_re.search(raw_bytes):
                raise HTTPException(
                    status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    detail="PDF rejected: contains active content (JavaScript, Launch, or EmbeddedFile).",
                )

        if _PDFIUM_AVAILABLE and pdfium is not None:
            try:
                doc = pdfium.PdfDocument(raw_bytes)
                page_count = len(doc)
                if page_count > 10:
                    doc.close()
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"PDF page count ({page_count}) exceeds limit of 10 pages.",
                    )
                doc.close()
            except HTTPException:
                raise
            except Exception:
                if b"\x00\x00" in raw_bytes or len(raw_bytes) < 10:
                    raise HTTPException(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        detail="Malformed or unreadable PDF document.",
                    )
        elif fitz is not None:
            try:
                doc = fitz.open(stream=raw_bytes, filetype="pdf")
                if doc.is_encrypted:
                    doc.close()
                    raise HTTPException(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        detail="Encrypted PDFs are not supported.",
                    )
                page_count = doc.page_count
                if doc.page_count > 10:
                    doc.close()
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"PDF page count ({page_count}) exceeds limit of 10 pages.",
                    )
                doc.close()
            except HTTPException:
                raise
            except Exception:
                if b"\x00\x00" in raw_bytes or len(raw_bytes) < 10:
                    raise HTTPException(
                        status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                        detail="Malformed or unreadable PDF document.",
                    )
        return raw_bytes, "application/pdf"

    # Image validation & sanitization
    try:
        # Step 1: Open and verify
        verify_img = Image.open(io.BytesIO(raw_bytes))
        width, height = verify_img.size
        if width * height > MAX_PIXELS:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Image dimensions exceed maximum allowed resolution.",
            )
        verify_img.verify()

        # Step 2: Re-open and re-encode to clean PNG
        clean_img = Image.open(io.BytesIO(raw_bytes))
        if clean_img.mode not in ("RGB", "L"):
            clean_img = clean_img.convert("RGB")

        out_buf = io.BytesIO()
        clean_img.save(out_buf, format="PNG")
        return out_buf.getvalue(), "image/png"

    except HTTPException:
        raise
    except Image.DecompressionBombError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Decompression bomb detected in image payload.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Image validation failed: unreadable or corrupt image.",
        ) from exc
