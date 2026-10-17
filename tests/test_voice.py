"""Voice-input fallback: POST /api/voice/transcribe."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.uploads import MAX_AUDIO_BYTES
from tests.conftest import make_settings
from tests.fakes import FakeLLMClient

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 256
OGG = b"OggS" + b"\x00" * 256
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 256
MP4 = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 256
URL = "/api/voice/transcribe"


def post(client: TestClient, data: bytes, mime: str, language: str = "hi") -> object:
    return client.post(f"{URL}?language={language}", content=data, headers={"Content-Type": mime})


@pytest.mark.parametrize(
    ("data", "mime"),
    [(WEBM, "audio/webm;codecs=opus"), (OGG, "audio/ogg; codecs=opus"), (WAV, "audio/wav"), (MP4, "audio/mp4")],
    ids=["webm", "ogg", "wav", "mp4"],
)
def test_transcribe_happy_path(client: TestClient, fake_llm: FakeLLMClient, data: bytes, mime: str) -> None:
    fake_llm.json_responses["TranscriptOutput"] = {"transcript": "  Rapido से 150 कमाए  ", "has_speech": True}
    res = post(client, data, mime)
    assert res.status_code == 200  # type: ignore[attr-defined]
    assert res.json() == {"text": "Rapido से 150 कमाए"}  # type: ignore[attr-defined]
    request = fake_llm.requests[-1]
    assert request.attachments[0].data == data
    assert request.attachments[0].mime_type == mime.split(";")[0]
    assert request.timeout_seconds == 30


@pytest.mark.parametrize(
    ("language", "name"), [("pa", "Punjabi"), ("ta", "Tamil"), ("bn", "Bengali"), ("en", "English")]
)
def test_language_passthrough(client: TestClient, fake_llm: FakeLLMClient, language: str, name: str) -> None:
    fake_llm.json_responses["TranscriptOutput"] = {"transcript": "hello", "has_speech": True}
    post(client, OGG, "audio/ogg", language)
    assert name in fake_llm.requests[-1].system


def test_wrong_mime_is_415(client: TestClient) -> None:
    res = post(client, WEBM, "video/mp4")
    assert res.status_code == 415  # type: ignore[attr-defined]
    assert res.json()["error"]["code"] == "unsupported_audio_type"  # type: ignore[attr-defined]


def test_mime_and_bytes_mismatch_is_415(client: TestClient) -> None:
    assert post(client, OGG, "audio/webm").status_code == 415  # type: ignore[attr-defined]


def test_empty_audio_is_422(client: TestClient) -> None:
    res = post(client, b"", "audio/webm")
    assert res.status_code == 422  # type: ignore[attr-defined]
    assert res.json()["error"]["code"] == "empty_file"  # type: ignore[attr-defined]


def test_oversized_audio_is_413(client: TestClient) -> None:
    res = post(client, WEBM + b"\x00" * MAX_AUDIO_BYTES, "audio/webm")
    assert res.status_code == 413  # type: ignore[attr-defined]


def test_bad_language_is_422(client: TestClient) -> None:
    assert post(client, OGG, "audio/ogg", "fr").status_code == 422  # type: ignore[attr-defined]


@pytest.mark.parametrize("output", [{"transcript": "", "has_speech": False}, {"transcript": "   ", "has_speech": True}])
def test_no_speech_is_an_honest_error(client: TestClient, fake_llm: FakeLLMClient, output: dict[str, object]) -> None:
    fake_llm.json_responses["TranscriptOutput"] = output
    res = post(client, OGG, "audio/ogg")
    assert res.status_code == 422  # type: ignore[attr-defined]
    assert res.json()["error"]["code"] == "no_speech"  # type: ignore[attr-defined]


def test_transcript_is_never_logged(
    client: TestClient, fake_llm: FakeLLMClient, caplog: pytest.LogCaptureFixture
) -> None:
    fake_llm.json_responses["TranscriptOutput"] = {"transcript": "secret words 12345", "has_speech": True}
    with caplog.at_level(logging.DEBUG):
        post(client, OGG, "audio/ogg")
    assert "secret words" not in caplog.text


def test_transcribe_is_rate_limited(fake_llm: FakeLLMClient) -> None:
    fake_llm.json_responses["TranscriptOutput"] = {"transcript": "hi", "has_speech": True}
    with TestClient(create_app(make_settings(ai_rate_limit_per_minute=2), llm_client=fake_llm)) as c:
        codes = [post(c, OGG, "audio/ogg").status_code for _ in range(3)]  # type: ignore[attr-defined]
    assert codes == [200, 200, 429]


def test_permissions_policy_allows_only_own_microphone(client: TestClient) -> None:
    policy = client.get("/health").headers["permissions-policy"]
    assert "microphone=(self)" in policy
    assert "camera=()" in policy and "geolocation=()" in policy
    csp = client.get("/health").headers["content-security-policy"]
    assert "default-src 'self'" in csp and "*" not in csp
