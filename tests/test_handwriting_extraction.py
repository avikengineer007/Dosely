"""
Tests for Task 2: Handwriting-aware extraction prompt and schema
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from dosely.models import (
    FieldRead,
    ImageQuality,
    Medicine,
    Prescription,
    ReadAlternative,
)
from dosely.prompts import build_handwriting_system_prompt
from dosely.extractor import (
    extract_handwritten_line,
    extract_handwritten_page,
)


class TestHandwritingSchemaAndModels:
    def test_field_read_confidence_clamping(self):
        """Confidences must be clamped to [0.0, 1.0] when out of range."""
        read_high = FieldRead(text="Metformin", confidence=1.25)
        assert read_high.confidence == 1.0

        read_low = FieldRead(text="Metformin", confidence=-0.4)
        assert read_low.confidence == 0.0

        alt = ReadAlternative(text="Metformin", confidence=1.5)
        assert alt.confidence == 1.0

    def test_illegible_field_forces_needs_review(self):
        """An illegible field must force needs_review=True and record reason."""
        med = Medicine(
            name_raw="unknown",
            raw_transcription="???",
            name_read=FieldRead(
                text=None,
                legibility="illegible",
                confidence=0.1,
                masked_text="???",
            ),
            confidence=0.9,
        )
        assert med.needs_review is True
        assert any("illegible" in r.lower() or "name" in r.lower() for r in med.review_reasons)

    def test_alternatives_preserved(self):
        """Alternatives up to 3 candidate readings are preserved."""
        med = Medicine(
            name_raw="Metformin",
            raw_transcription="Metf?rmin",
            name_read=FieldRead(
                text="Metformin",
                confidence=0.75,
                legibility="partial",
                alternatives=[
                    ReadAlternative(text="Metformin", confidence=0.75),
                    ReadAlternative(text="Metoprolol", confidence=0.20),
                ],
                char_uncertain=[4],
                masked_text="Metf?rmin",
            ),
            confidence=0.85,
        )
        assert med.name_read is not None
        assert len(med.name_read.alternatives) == 2
        assert med.name_read.alternatives[0].text == "Metformin"
        assert med.name_read.alternatives[1].text == "Metoprolol"
        assert med.name_read.masked_text == "Metf?rmin"

    def test_transcription_first_fields_distinct(self):
        """Raw transcription is stored distinctly from interpreted name."""
        med = Medicine(
            name_raw="Metformin 500",
            raw_transcription="Metformn 500",
            name_interpreted="Metformin",
            confidence=0.8,
        )
        assert med.raw_transcription == "Metformn 500"
        assert med.name_interpreted == "Metformin"

    def test_backward_compatibility_printed_prescriptions(self):
        """Existing printed prescription data without handwriting read objects works seamlessly."""
        med = Medicine(
            name_raw="Paracetamol 500mg",
            name_generic="paracetamol",
            strength="500 mg",
            frequency_raw="TDS",
            frequency_normalized="Three times a day",
            duration_days=5,
            confidence=0.95,
        )
        assert med.name_read is None
        assert med.name_raw == "Paracetamol 500mg"
        assert med.strength == "500 mg"
        assert med.needs_review is False


class TestHandwritingPrompt:
    def test_handwriting_system_prompt_rules(self):
        prompt = build_handwriting_system_prompt()
        assert "transcribe exactly what is written" in prompt.lower()
        assert "never expand abbreviations" in prompt.lower()
        assert "never replace an unclear word" in prompt.lower()
        assert "alternatives" in prompt.lower()
        assert "illegible" in prompt.lower()
        assert "patient" in prompt.lower()
        assert "json" in prompt.lower()


class TestHandwritingExtractorFlow:
    @patch("dosely.extractor._get_client")
    def test_handwritten_line_retries_once_on_malformed_json(self, mock_get_client):
        """Malformed JSON must be retried exactly once before succeeding."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Turn 1: Malformed JSON, Turn 2: Valid JSON
        valid_response_data = {
            "name_raw": "Metformin",
            "raw_transcription": "Metformn",
            "name_interpreted": "Metformin",
            "name_read": {
                "text": "Metformin",
                "confidence": 0.85,
                "legibility": "clear",
                "alternatives": [{"text": "Metformin", "confidence": 0.85}],
            },
            "strength": "500 mg",
            "confidence": 0.85,
        }

        mock_turn1 = MagicMock()
        mock_turn1.content = [MagicMock(text="INVALID_JSON{broken")]
        mock_turn2 = MagicMock()
        mock_turn2.content = [MagicMock(text=json.dumps(valid_response_data))]

        mock_client.messages.create.side_effect = [mock_turn1, mock_turn2]

        crop_bytes = b"fake_line_crop_bytes"
        med = extract_handwritten_line(crop_bytes, prescription_id="rx-test-123", line_id="line_001")

        assert mock_client.messages.create.call_count == 2
        assert med.raw_transcription == "Metformn"
        assert med.name_interpreted == "Metformin"

    @patch("dosely.extractor._get_client")
    def test_handwritten_line_double_failure_marks_needs_review(self, mock_get_client):
        """When retrying also fails with malformed JSON, mark line as needs_review without crashing."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_turn1 = MagicMock()
        mock_turn1.content = [MagicMock(text="INVALID_JSON_1")]
        mock_turn2 = MagicMock()
        mock_turn2.content = [MagicMock(text="INVALID_JSON_2")]

        mock_client.messages.create.side_effect = [mock_turn1, mock_turn2]

        crop_bytes = b"fake_line_crop_bytes"
        med = extract_handwritten_line(crop_bytes, prescription_id="rx-test-123", line_id="line_001")

        assert mock_client.messages.create.call_count == 2
        assert med.needs_review is True
        assert any("retry" in r.lower() or "malformed" in r.lower() for r in med.review_reasons)
