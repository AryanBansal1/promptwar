"""Edge cases of the hardening code paths: lazy SDK client, caps, eviction, data integrity, empty AI answers."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.errors import AppError
from app.llm.client import Attachment, GeminiClient, LLMRequest
from app.security import BodyRule, RateLimiter, _Exchange
from app.services.data_store import DATA_DIR, load_data_store
from app.services.uploads import AUDIO_TYPES, MAX_AUDIO_BYTES, read_capped, validate_upload
from tests.conftest import make_settings
from tests.fakes import FakeLLMClient


def test_sdk_client_is_created_once_on_first_use_with_a_timeout() -> None:
    client = GeminiClient(make_settings(gemini_api_key="test-key", llm_timeout_seconds=20))
    assert client._sdk is None  # nothing created at startup: cold starts skip the SDK
    sdk = client._client
    assert client._client is sdk  # reused afterwards (pooled connections)
    assert sdk._api_client._http_options.timeout == 60_000


def test_attachments_become_inline_parts_before_the_prompt() -> None:
    request = LLMRequest(
        system="s", prompt="read this", max_output_tokens=10, attachments=(Attachment(b"%PDF-1", "application/pdf"),)
    )
    parts = GeminiClient._contents(request)
    assert isinstance(parts, list) and len(parts) == 2
    assert parts[0].inline_data.mime_type == "application/pdf" and parts[1].text == "read this"
    assert GeminiClient._contents(LLMRequest(system="s", prompt="plain", max_output_tokens=1)) == "plain"


def test_missing_static_file_is_a_plain_404(client: TestClient) -> None:
    res = client.get("/static/js/nope.js")
    assert res.status_code == 404 and "immutable" not in res.headers.get("cache-control", "")


def test_capped_receive_keeps_reporting_disconnect_after_overflow() -> None:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"x" * 10, "more_body": True}

    async def send(_: Any) -> None:  # pragma: no cover - not used here
        return None

    exchange = _Exchange(receive, send, BodyRule(max_bytes=15), is_api=True)

    async def run() -> list[str]:
        return [(await exchange.receive_capped())["type"] for _ in range(3)]

    assert asyncio.run(run()) == ["http.request", "http.disconnect", "http.disconnect"]
    assert exchange.too_large


def test_rate_limiter_clears_when_every_key_is_still_active() -> None:
    limiter = RateLimiter(limit=5, window_seconds=60, max_keys=2)
    limiter.check("a", now=0)
    limiter.check("b", now=0)
    assert limiter.check("c", now=1) is None  # memory stays bounded even under a flood of new keys
    assert set(limiter._hits) == {"c"}


def _broken_data(tmp_path: Path, name: str, change: Any) -> Path:
    shutil.copytree(DATA_DIR, tmp_path / "data")
    path = tmp_path / "data" / name
    raw = json.loads(path.read_text(encoding="utf-8"))
    change(raw)
    path.write_text(json.dumps(raw), encoding="utf-8")
    return tmp_path / "data"


@pytest.mark.parametrize(
    ("name", "change", "message"),
    [
        ("escalation.json", lambda d: d["channels"]["nch"].update(url="http://consumerhelpline.gov.in"), "https"),
        ("escalation.json", lambda d: d["routes"]["harassment"].append("nowhere"), "unknown channel"),
        ("documents.json", lambda d: d["goals"][0]["documents"].append("ghost"), "unknown document"),
        ("schemes.json", lambda d: d["schemes"][0].update(document_goal="ghost"), "unknown goal"),
    ],
    ids=["http-contact", "unknown-channel", "goal-unknown-document", "scheme-unknown-goal"],
)
def test_broken_curated_data_fails_loading(tmp_path: Path, name: str, change: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_data_store(_broken_data(tmp_path, name, change))


@pytest.mark.parametrize(
    "data", [b"ID3\x04" + b"\x00" * 32, b"\xff\xfb\x90\x00" + b"\x00" * 32], ids=["id3", "frame-sync"]
)
def test_mp3_audio_is_recognised(data: bytes) -> None:
    assert validate_upload("audio/mpeg", data, AUDIO_TYPES, MAX_AUDIO_BYTES).mime_type == "audio/mpeg"


def test_read_capped_rejects_a_declared_oversize_body_without_reading() -> None:
    request = Request({"type": "http", "method": "POST", "headers": [(b"content-length", b"999")]})
    with pytest.raises(AppError) as exc:
        asyncio.run(read_capped(request, max_bytes=10))
    assert exc.value.status_code == 413


@pytest.mark.parametrize(
    ("url", "payload", "schema", "output"),
    [
        ("/api/safety/chat", {"message": "help"}, "SafetyModelOutput", {"reply": "‮", "suggest_complaint": False}),
        ("/api/ask", {"message": "hello there"}, "IntentOutput", {"intent": "general", "answer": "​", "prefill": {}}),
    ],
    ids=["safety", "ask"],
)
def test_answers_empty_after_cleaning_are_honest_errors(
    client: TestClient, fake_llm: FakeLLMClient, url: str, payload: dict[str, Any], schema: str, output: dict[str, Any]
) -> None:
    fake_llm.json_responses[schema] = output
    res = client.post(url, json=payload)
    assert res.status_code == 502 and res.json()["error"]["code"] == "ai_empty"


def test_explanation_empty_after_cleaning_is_not_cached(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.text_response = "‮​"
    assert (
        client.post("/api/documents/explain", json={"kind": "goal", "id": "pan"}).json()["error"]["code"] == "ai_empty"
    )
    fake_llm.text_response = "Step 1"
    assert client.post("/api/documents/explain", json={"kind": "goal", "id": "pan"}).json() == {"text": "Step 1"}
