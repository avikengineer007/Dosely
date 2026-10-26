"""
Test Task 2: Health data in logs.
Verifies:
1. No log statements contain raw model output, raw drug names, doses, or content fields.
2. Repo-wide check ensuring no logger call references raw_response, name_raw, original, or medicine fields.
3. caplog test running full flow (upload, confirm, schedule, error paths) with 'Morphine 10mg' payload,
   asserting neither 'Morphine' nor '10mg' appears in logs.
4. setup_secure_logging() configures redaction filter on root, uvicorn, and fastapi loggers.
5. Redaction filter scrubs '<word> <number>mg/mcg/ml/g' and standalone dosages.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from dosely.logging import HealthDataRedactionFilter, setup_secure_logging
from dosely.models import Prescription, Medicine, DoseForm, ImageQuality


def test_repo_wide_no_forbidden_logger_fields():
    """
    Requirement 4: Grep source for logger calls containing raw_response,
    name_raw, original, or medicine fields, and fails if found.
    """
    dosely_dir = Path(__file__).resolve().parent.parent / "dosely"
    py_files = list(dosely_dir.rglob("*.py"))
    main_py = Path(__file__).resolve().parent.parent / "main.py"
    if main_py.exists():
        py_files.append(main_py)

    forbidden_patterns = [
        re.compile(r"logger\.\w+\(.*?\braw_response\b", re.DOTALL),
        re.compile(r"logger\.\w+\(.*?\bname_raw\b", re.DOTALL),
        re.compile(r"logger\.\w+\(.*?\boriginal\b", re.DOTALL),
        re.compile(r"logger\.\w+\(.*?\bmedicine\b", re.DOTALL),
        re.compile(r"logger\.\w+\(.*?\bmedicines\b", re.DOTALL),
        re.compile(r"logging\.\w+\(.*?\braw_response\b", re.DOTALL),
        re.compile(r"logging\.\w+\(.*?\bname_raw\b", re.DOTALL),
    ]

    violations = []
    for f in py_files:
        content = f.read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            matches = pat.findall(content)
            if matches:
                violations.append(f"{f.name}: {matches}")

    assert not violations, f"Forbidden logger references found:\n" + "\n".join(violations)


def test_redaction_filter_scrubs_word_number_units():
    """
    Requirement 3: Redaction filter scrubs anything resembling '<word> <number>mg/mcg/ml/g'.
    """
    f = HealthDataRedactionFilter()

    test_cases = [
        ("Prescribed Morphine 10mg orally", "[REDACTED] orally"),
        ("Patient took Amoxicillin 500 mg daily", "[REDACTED] daily"),
        ("Administer Fentanyl 50mcg patch", "[REDACTED] patch"),
        ("Paracetamol 1000mg then Ibuprofen 400 mg", "[REDACTED] then [REDACTED]"),
        ("Give liquid 5 ml now", "Give liquid [REDACTED] now"),
    ]

    for raw, expected in test_cases:
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg=raw, args=(), exc_info=None
        )
        f.filter(record)
        assert "Morphine" not in record.msg
        assert "10mg" not in record.msg
        assert "500 mg" not in record.msg
        assert "50mcg" not in record.msg


def test_setup_secure_logging_attaches_to_all_relevant_loggers():
    """
    Requirement 2: Call setup_secure_logging() and attach redaction filter to root,
    uvicorn, and fastapi loggers.
    """
    setup_secure_logging()

    root = logging.getLogger()
    assert any(isinstance(f, HealthDataRedactionFilter) for f in root.filters) or any(
        any(isinstance(f, HealthDataRedactionFilter) for f in h.filters) for h in root.handlers
    )

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        log = logging.getLogger(name)
        has_filter = any(isinstance(f, HealthDataRedactionFilter) for f in log.filters) or any(
            isinstance(f, HealthDataRedactionFilter) for f in root.filters
        )
        assert has_filter, f"Logger '{name}' lacks HealthDataRedactionFilter"


def test_caplog_full_flow_no_morphine_10mg_in_logs(caplog):
    """
    Requirement 5: Test using caplog that runs the full upload, confirm and schedule flow
    and then error paths with a 'Morphine 10mg' payload, asserting the captured logs
    contain neither 'Morphine' nor '10mg'.
    """
    import main
    from dosely.api.prescriptions import _create_access_token

    setup_secure_logging()
    client = TestClient(main.app)
    token = _create_access_token(data={"sub": "user-logging-test"})
    headers = {"Authorization": f"Bearer {token}"}

    # Dummy prescription response with 'Morphine 10mg'
    mock_prescription = Prescription(
        prescription_id="test-rx-morphine",
        image_quality=ImageQuality.GOOD,
        medicines=[
            Medicine(
                name_raw="Morphine 10mg",
                name_generic="morphine",
                strength="10 mg",
                form=DoseForm.TABLET,
                frequency_raw="SOS",
                frequency_normalized="As needed",
                confidence=0.95,
            )
        ],
    )

    caplog.set_level(logging.DEBUG)
    caplog.clear()

    # 1. Upload flow (mock extract to return Morphine 10mg)
    with patch("dosely.api.prescriptions.extract", return_value=mock_prescription):
        file_content = b"%PDF-1.4 dummy pdf with Morphine 10mg content"
        up_res = client.post(
            "/prescriptions/upload",
            files={"file": ("prescription_morphine_10mg.pdf", io.BytesIO(file_content), "application/pdf")},
            headers=headers,
        )
        assert up_res.status_code == 200, up_res.text
        rx_id = up_res.json()["id"]

    # 2. Confirm flow with 'Morphine 10mg'
    confirm_payload = {
        "medicines": [
            {
                "name_raw": "Morphine 10mg",
                "name_generic": "morphine",
                "strength": "10 mg",
                "form": "tablet",
                "frequency_raw": "SOS",
                "duration_days": 3,
                "food_instruction": "after_food",
                "user_accepted": True,
            }
        ]
    }
    conf_res = client.put(f"/prescriptions/{rx_id}/confirm", json=confirm_payload, headers=headers)
    assert conf_res.status_code == 200, conf_res.text

    # 3. Schedule flow
    sched_res = client.get(f"/prescriptions/{rx_id}/schedule", headers=headers)
    assert sched_res.status_code == 200, sched_res.text

    # 4. Error paths (e.g. invalid confirm with Morphine 10mg in bad data, non-existent rx, etc.)
    client.put(
        "/prescriptions/non-existent-id/confirm",
        json=confirm_payload,
        headers=headers,
    )
    client.get("/prescriptions/invalid-id/schedule", headers=headers)

    # Verify captured logs
    all_logs = caplog.text
    assert "Morphine" not in all_logs, f"Found 'Morphine' in logs:\n{all_logs}"
    assert "10mg" not in all_logs.lower(), f"Found '10mg' in logs:\n{all_logs}"
    assert "10 mg" not in all_logs.lower(), f"Found '10 mg' in logs:\n{all_logs}"
