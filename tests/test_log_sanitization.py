"""
tests/test_log_sanitization.py
==============================
Test suite for TASK 3: Health data leaking through logs.

Verifies:
1. Redaction on final formatted output including message, args, exception text, stack info.
2. Overridden formatException emits only exception class name and stack frames, NO message text.
3. Repo-wide check: no logger call interpolates upload.filename or raw exception messages.
4. Pydantic validation errors (422) log and return only loc and type, never msg, input, or ctx.
5. Third-party loggers (anthropic, httpx, httpcore, urllib3, uvicorn.access) forced to WARNING+.
6. Failures carrying "Morphine 10mg", "Warfarin 5 mg", "John Doe", "john_doe_morphine.png"
   through logger.exception, logger.error with %s args, Pydantic failure, extractor failure,
   and upload with hostile filename are completely sanitized (case-insensitive).
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
import re
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from dosely.api.prescriptions import _create_access_token
from dosely.logging import setup_secure_logging, HealthDataRedactionFilter, SafeRedactingFormatter


SENSITIVE_STRINGS = [
    "morphine",
    "10mg",
    "10 mg",
    "warfarin",
    "5mg",
    "5 mg",
    "john doe",
    "john_doe_morphine.png",
]


@pytest.fixture(autouse=True)
def init_logging():
    setup_secure_logging()


def _assert_clean(log_output: str):
    lower = log_output.lower()
    for s in SENSITIVE_STRINGS:
        assert s not in lower, f"Leak detected: found '{s}' in log capture:\n{log_output}"


def test_logger_exception_redacts_message_and_args(caplog):
    logger = logging.getLogger("dosely.test.exception")
    caplog.clear()

    try:
        raise ValueError("Critical error for patient John Doe with Morphine 10mg")
    except ValueError as exc:
        logger.exception("Failed processing patient file: %s", "john_doe_morphine.png")

    _assert_clean(caplog.text)
    assert "ValueError" in caplog.text  # class name preserved
    assert "[EXCEPTION_MESSAGE_REDACTED]" in caplog.text or "[REDACTED" in caplog.text


def test_logger_error_with_format_args(caplog):
    logger = logging.getLogger("dosely.test.error")
    caplog.clear()

    logger.error("Prescription error for %s taking %s at %s", "John Doe", "Warfarin 5 mg", "10 mg")
    _assert_clean(caplog.text)


def test_format_exception_omits_message_text():
    formatter = SafeRedactingFormatter()
    try:
        raise RuntimeError("Secret health condition: patient John Doe needs Morphine 10mg immediately")
    except RuntimeError:
        import sys
        exc_info = sys.exc_info()
        formatted = formatter.formatException(exc_info)

    _assert_clean(formatted)
    assert "RuntimeError" in formatted


def test_pydantic_validation_error_does_not_echo_input_in_422_or_logs(caplog):
    client = TestClient(main.app)
    token = _create_access_token(data={"sub": "test-user"})
    headers = {"Authorization": f"Bearer {token}"}

    caplog.clear()
    res = client.put(
        "/prescriptions/test-rx-id/confirm",
        json={"medicines": [{"invalid_field": "Morphine 10mg John Doe"}]},
        headers=headers,
    )
    assert res.status_code == 422
    body = res.json()
    detail = body.get("detail", [])
    assert isinstance(detail, list)

    for item in detail:
        assert "input" not in item, f"Found 'input' in 422 detail: {item}"
        assert "msg" not in item or item["msg"] == "[REDACTED]", f"Found unredacted 'msg' in 422 detail: {item}"
        assert "ctx" not in item, f"Found 'ctx' in 422 detail: {item}"
        assert "loc" in item
        assert "type" in item

    _assert_clean(res.text)
    _assert_clean(caplog.text)


def test_extractor_failure_does_not_leak_in_logs(caplog):
    client = TestClient(main.app)
    token = _create_access_token(data={"sub": "test-user"})
    headers = {"Authorization": f"Bearer {token}"}

    caplog.clear()
    with patch(
        "dosely.api.prescriptions.extract",
        side_effect=ValueError("Failed extracting Morphine 10mg for John Doe"),
    ):
        res = client.post(
            "/prescriptions/upload",
            files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4 dummy"), "application/pdf")},
            headers=headers,
        )

    _assert_clean(caplog.text)


def test_upload_with_hostile_filename_does_not_leak_in_logs(caplog):
    client = TestClient(main.app)
    token = _create_access_token(data={"sub": "test-user"})
    headers = {"Authorization": f"Bearer {token}"}

    caplog.clear()
    res = client.post(
        "/prescriptions/upload",
        files={"file": ("john_doe_morphine.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50), "image/png")},
        headers=headers,
    )

    _assert_clean(caplog.text)


def test_third_party_loggers_forced_to_warning():
    for name in ("anthropic", "httpx", "httpcore", "urllib3", "uvicorn.access"):
        log = logging.getLogger(name)
        assert log.level >= logging.WARNING, f"Logger '{name}' level is {log.level}, expected >= WARNING ({logging.WARNING})"


def test_repo_wide_no_filename_or_raw_exception_in_logger():
    dosely_dir = Path(__file__).resolve().parent.parent / "dosely"
    py_files = list(dosely_dir.rglob("*.py"))
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    if main_py.exists():
        py_files.append(main_py)

    forbidden_patterns = [
        re.compile(r"logger\.\w+\([^)]*\bupload\.filename\b", re.DOTALL),
        re.compile(r"logger\.\w+\([^)]*\bfile\.filename\b", re.DOTALL),
        re.compile(r"logger\.exception\([^)]*%s[^)]*\bexc\b", re.DOTALL),
    ]

    violations = []
    for f in py_files:
        content = f.read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            matches = pat.findall(content)
            if matches:
                violations.append(f"{f.name}: {matches}")

    assert not violations, f"Forbidden logger interpolations found:\n" + "\n".join(violations)
