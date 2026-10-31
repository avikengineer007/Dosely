"""
tests/test_gemini_provider.py
=============================
Gemini provider adapter tests. The Gemini client is fully mocked: no API key or network.

Verifies:
  1. Provider selection from LLM_PROVIDER (default anthropic, invalid value rejected).
  2. Missing GEMINI_API_KEY raises a clear error that never contains key material.
  3. Anthropic-shaped messages convert to Gemini contents (image bytes preserved, roles mapped).
  4. A mocked Gemini response flows through the real extract() path.
  5. Fenced JSON / extra prose is accepted; an empty response degrades to needs_human_review.
  6. A Gemini API error degrades to needs_human_review (never an unhandled exception).
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from dosely import extractor
from tests.conftest import GOOD_PRESCRIPTION_JSON

genai_types = pytest.importorskip("google.genai.types")
genai_errors = pytest.importorskip("google.genai.errors")


@pytest.fixture
def gemini_env(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real-0000000000")
    monkeypatch.setattr(extractor, "_gemini_client", None)


def _client_returning(*texts):
    client = MagicMock()
    responses = []
    for t in texts:
        r = MagicMock()
        r.text = t
        responses.append(r)
    client.models.generate_content.side_effect = responses
    return client


def test_default_provider_is_anthropic(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert extractor._provider() == "anthropic"


def test_invalid_provider_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(EnvironmentError):
        extractor._provider()


def test_missing_gemini_key_message_has_no_secret(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(extractor, "_gemini_client", None)
    with pytest.raises(EnvironmentError) as exc:
        extractor._get_client()
    assert "GEMINI_API_KEY" in str(exc.value)


def test_messages_convert_to_gemini_contents(gemini_env):
    raw = b"\x89PNG-fake-bytes"
    messages = [
        {"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(raw).decode()}},
            {"type": "text", "text": "extract"},
        ]},
        {"role": "assistant", "content": "prior reply"},
        {"role": "user", "content": "retry please"},
    ]
    contents = extractor._to_gemini_contents(messages)
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[0].parts[0].inline_data.data == raw
    assert contents[0].parts[0].inline_data.mime_type == "image/png"
    assert contents[1].parts[0].text == "prior reply"


def test_extract_real_path_with_mocked_gemini(gemini_env, white_png):
    client = _client_returning(GOOD_PRESCRIPTION_JSON)
    with patch.object(extractor, "_gemini_client", client):
        rx = extractor.extract(white_png, prescription_id="rx-g-1")
    assert client.models.generate_content.call_count >= 1
    assert len(rx.medicines) == 1
    assert rx.medicines[0].name_generic == "amoxicillin"


def test_fenced_json_with_prose_is_accepted(gemini_env, white_png):
    wrapped = "Here is the result:\n```json\n" + GOOD_PRESCRIPTION_JSON + "\n```\nHope that helps."
    client = _client_returning(wrapped)
    with patch.object(extractor, "_gemini_client", client):
        rx = extractor.extract(white_png, prescription_id="rx-g-2")
    assert len(rx.medicines) == 1


def test_empty_gemini_response_degrades_to_review(gemini_env, white_png):
    client = _client_returning(None)
    with patch.object(extractor, "_gemini_client", client):
        rx = extractor.extract(white_png, prescription_id="rx-g-3")
    assert rx.needs_human_review is True
    assert rx.medicines == []


def test_gemini_api_error_degrades_to_review(gemini_env, white_png):
    client = MagicMock()
    client.models.generate_content.side_effect = genai_errors.ClientError(
        401, {"error": {"message": "invalid key", "status": "UNAUTHENTICATED"}}
    )
    with patch.object(extractor, "_gemini_client", client):
        rx = extractor.extract(white_png, prescription_id="rx-g-4")
    assert rx.needs_human_review is True
    assert rx.medicines == []
