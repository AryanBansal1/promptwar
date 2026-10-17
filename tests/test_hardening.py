"""Tests for the security and efficiency hardening: early rejection, error barrier, output cleaning,
cost-weighted limits, trusted client IPs, safe logs and HTTP caching."""

from __future__ import annotations

import asyncio
import logging
import tomllib
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config import ConfigError, load_settings
from app.errors import AppError
from app.llm.client import LLMRequest
from app.main import create_app
from app.security import LINK_REMOVED, RateLimiter, SecurityMiddleware, client_ip, safe_ai_text
from app.services.uploads import read_capped
from tests.conftest import make_settings
from tests.fakes import FakeLLMClient

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ROOT = Path(__file__).resolve().parent.parent


# ---------- 415 before the body is read ----------


def test_wrong_content_type_is_rejected_before_reading_the_body() -> None:
    reached_app = False
    body_read = False

    async def app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover - must not run
        nonlocal reached_app
        reached_app = True

    async def receive() -> dict[str, Any]:  # pragma: no cover - must not be called
        nonlocal body_read
        body_read = True
        return {"type": "http.request", "body": b"x", "more_body": False}

    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/api/ask", "headers": [(b"content-type", b"text/plain")]}
    asyncio.run(SecurityMiddleware(app, max_body_bytes=1024)(scope, receive, send))
    assert sent[0]["status"] == 415
    assert not reached_app and not body_read


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data"])
def test_json_endpoints_return_415_for_other_types(client: TestClient, content_type: str) -> None:
    res = client.post("/api/ledger/summary", content=b'{"entries":[]}', headers={"Content-Type": content_type})
    assert res.status_code == 415
    assert res.json()["error"]["code"] == "unsupported_media_type"


def test_json_with_charset_is_accepted(client: TestClient) -> None:
    res = client.post(
        "/api/ledger/summary", content=b'{"entries":[]}', headers={"Content-Type": "application/json; charset=utf-8"}
    )
    assert res.status_code == 200


def test_upload_type_is_checked_before_the_body(client: TestClient) -> None:
    res = client.post("/api/ledger/import/file?today=2026-10-01", content=PNG, headers={"Content-Type": "text/html"})
    assert res.status_code == 415


def test_invalid_content_length_is_400(client: TestClient) -> None:
    res = client.post("/api/ask", content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "x1"})
    assert res.status_code in (400, 422)  # the server rejects it; it is never trusted


# ---------- Size cap on streamed uploads ----------


def test_streamed_upload_over_the_cap_is_413(fake_llm: FakeLLMClient) -> None:
    app = create_app(make_settings(), llm_client=fake_llm)

    def chunks() -> Iterator[bytes]:  # no Content-Length: the size is only known while streaming
        yield PNG
        for _ in range(6):
            yield b"\x00" * 1_000_000

    with TestClient(app) as c:
        res = c.post(
            "/api/ledger/import/file?today=2026-10-01", content=chunks(), headers={"Content-Type": "image/png"}
        )
    assert res.status_code == 413
    assert fake_llm.requests == []  # nothing reached Gemini


def test_read_capped_stops_reading_once_over_the_limit() -> None:
    consumed = 0

    async def stream() -> AsyncIterator[bytes]:
        nonlocal consumed
        for _ in range(100):
            consumed += 1
            yield b"x" * 10

    request = Request({"type": "http", "method": "POST", "headers": []})
    request.stream = stream  # type: ignore[method-assign]
    with pytest.raises(AppError) as exc:
        asyncio.run(read_capped(request, max_bytes=25))
    assert exc.value.status_code == 413
    assert consumed == 3  # stopped at the first chunk past the cap, not after reading everything


# ---------- Generic error handler ----------


def test_unexpected_errors_never_leak_details(
    fake_llm: FakeLLMClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    leak_marker = "fake-env-value-DO-NOT-LEAK-1234"  # a fake value standing in for a leak_marker
    monkeypatch.setenv("SAATHI_TEST_SECRET", leak_marker)

    class Boom(FakeLLMClient):
        async def generate_text(self, request: LLMRequest) -> str:
            raise RuntimeError(f"{leak_marker} at {__file__}")

    with (
        caplog.at_level(logging.DEBUG),
        TestClient(create_app(make_settings(), llm_client=Boom()), raise_server_exceptions=False) as c,
    ):
        res = c.post("/api/documents/explain", json={"kind": "goal", "id": "pan"})
    assert res.status_code == 500
    assert res.json() == {"error": {"code": "internal_error", "message": "Something went wrong on our side."}}
    for text in (res.text, caplog.text):
        assert leak_marker not in text
        assert __file__ not in text and "test_hardening.py" not in text
        assert "Traceback" not in text
    assert "type=RuntimeError" in caplog.text  # only the exception type is logged


def test_bad_settings_report_the_field_not_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "999-secret-value")
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert "llm_timeout_seconds" in str(exc.value)
    assert "secret-value" not in str(exc.value)


def test_pytest_tracebacks_never_print_argument_values() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "--tb=short" in config["tool"]["pytest"]["ini_options"]["addopts"]


def test_unknown_paths_are_not_written_to_logs(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        client.get("/api/nope%0Ainjected=1")
    app_logs = [r.getMessage() for r in caplog.records if r.name.startswith("saathi")]
    assert app_logs and "route=- " in app_logs[-1]
    assert not any("injected" in line for line in app_logs)  # only the route template is logged


# ---------- AI output cleaning ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Pay ‮evil‬ now", "Pay evil now"),  # bidi override
        ("zero​width", "zerowidth"),
        ("bell\x07char", "bellchar"),
        ("Visit https://evil.example/pay now", f"Visit {LINK_REMOVED} now"),
        ("or www.evil.example", f"or {LINK_REMOVED}"),
        ("line one\nline two", "line one\nline two"),  # newlines are kept
    ],
)
def test_safe_ai_text_removes_control_bidi_and_links(raw: str, expected: str) -> None:
    assert safe_ai_text(raw, 500) == expected


def test_safe_ai_text_strips_phones_only_when_asked() -> None:
    assert "98765" in safe_ai_text("call 98765 43210", 100)
    assert "98765" not in safe_ai_text("call 98765 43210", 100, strip_phones=True)
    assert len(safe_ai_text("word " * 100, 20)) <= 21


def test_letters_and_explanations_are_cleaned(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.json_responses["GrievanceDraft"] = {
        "letter_english": "To Zomato‮, see https://phish.example/form",
        "letter_local": "प्रिय ​महोदय www.phish.example",
        "next_steps": ["Wait\x07 15 days", "https://phish.example"],
    }
    body = client.post(
        "/api/grievance/draft",
        json={
            "language": "hi",
            "issue_type": "unpaid_earnings",
            "platform": "zomato",
            "start_date": "2026-10-01",
            "description": "My payout was not credited.",
        },
    ).json()
    blob = str(body["letter_english"]) + str(body["letter_local"]) + " ".join(body["next_steps"])
    assert "phish" not in blob and "‮" not in blob and "​" not in blob and "\x07" not in blob
    fake_llm.text_response = "Step 1: open http://bad.example ‮"
    text = client.post("/api/documents/explain", json={"kind": "goal", "id": "pan"}).json()["text"]
    assert "bad.example" not in text and "‮" not in text


# ---------- Cost-weighted rate limits ----------


def test_rate_limiter_spends_cost_units() -> None:
    limiter = RateLimiter(limit=6, window_seconds=60)
    assert limiter.check("ip", now=0, cost=3) is None
    assert limiter.check("ip", now=1, cost=3) is None
    assert limiter.check("ip", now=2, cost=1) is not None  # budget of 6 used up
    assert limiter.check("ip", now=61, cost=3) is None  # the first spend expired
    assert limiter.check("other", now=2, cost=7) is not None  # a single request can never exceed the limit


def test_image_import_costs_more_than_a_text_call(fake_llm: FakeLLMClient) -> None:
    fake_llm.json_responses["RawImportOutput"] = {"transactions": []}
    fake_llm.text_response = "ok"
    app = create_app(make_settings(ai_rate_limit_per_minute=6), llm_client=fake_llm)
    with TestClient(app) as c:
        upload = lambda: c.post(  # noqa: E731
            "/api/ledger/import/file?today=2026-10-01", content=PNG, headers={"Content-Type": "image/png"}
        )
        assert upload().status_code == 200  # 3 units
        assert upload().status_code == 200  # 6 units
        third = upload()
        text = c.post("/api/documents/ask", json={"kind": "goal", "id": "pan", "question": "How long?"})
    assert third.status_code == 429 and text.status_code == 429


def test_per_server_budget_applies_across_clients(fake_llm: FakeLLMClient) -> None:
    fake_llm.text_response = "ok"
    settings = make_settings(vercel=True, ai_rate_limit_per_minute=100, ai_global_per_minute=2)
    body = {"kind": "goal", "id": "pan", "question": "How long?"}
    with TestClient(create_app(settings, llm_client=fake_llm)) as c:
        codes = [
            c.post("/api/documents/ask", json=body, headers={"x-vercel-forwarded-for": ip}).status_code
            for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3")
        ]
    assert codes == [200, 200, 429]  # third client is new, but the instance budget is spent


def test_general_api_limit_covers_cheap_endpoints(fake_llm: FakeLLMClient) -> None:
    with TestClient(create_app(make_settings(api_rate_limit_per_minute=2), llm_client=fake_llm)) as c:
        codes = [c.post("/api/ledger/summary", json={"entries": []}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# ---------- Client IP (rate-limit key) ----------


def _request(vercel: bool, headers: dict[str, str], peer: str = "10.0.0.9") -> Request:
    class _State:
        settings = make_settings(vercel=vercel)

    class _App:
        state = _State()

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 1234), "app": _App()})


def test_client_ip_uses_vercel_header_only_on_vercel() -> None:
    headers = {"x-vercel-forwarded-for": "203.0.113.7", "x-forwarded-for": "6.6.6.6"}
    assert client_ip(_request(True, headers)) == "203.0.113.7"


def test_client_ip_ignores_headers_off_vercel() -> None:
    spoofed = {"x-vercel-forwarded-for": "1.2.3.4", "x-forwarded-for": "5.6.7.8", "x-real-ip": "9.9.9.9"}
    assert client_ip(_request(False, spoofed)) == "10.0.0.9"


def test_client_ip_on_vercel_ignores_spoofable_x_forwarded_for() -> None:
    assert client_ip(_request(True, {"x-forwarded-for": "6.6.6.6"})) == "10.0.0.9"
    assert client_ip(_request(True, {"x-vercel-forwarded-for": "203.0.113.7, 6.6.6.6"})) == "203.0.113.7"


def test_spoofed_headers_cannot_dodge_the_limit_off_vercel(fake_llm: FakeLLMClient) -> None:
    fake_llm.text_response = "ok"
    body = {"kind": "goal", "id": "pan", "question": "How long?"}
    with TestClient(create_app(make_settings(ai_rate_limit_per_minute=1), llm_client=fake_llm)) as c:
        codes = [
            c.post("/api/documents/ask", json=body, headers={"x-vercel-forwarded-for": f"7.7.7.{i}"}).status_code
            for i in range(2)
        ]
    assert codes == [200, 429]


# ---------- Headers and HTTP caching ----------


def test_api_responses_are_not_cached_and_carry_new_headers(client: TestClient) -> None:
    res = client.post("/api/ledger/summary", json={"entries": []})
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["cross-origin-resource-policy"] == "same-origin"
    assert res.headers["x-permitted-cross-domain-policies"] == "none"
    csp = res.headers["content-security-policy"]
    assert "frame-src 'none'" in csp and "worker-src 'none'" in csp and "unsafe" not in csp


@pytest.mark.parametrize("path", ["/", "/api/i18n/hi"])
def test_etag_revalidation_returns_304(client: TestClient, path: str) -> None:
    first = client.get(path)
    tag = first.headers["etag"]
    second = client.get(path, headers={"If-None-Match": tag})
    assert second.status_code == 304 and second.content == b""


def test_static_text_is_served_precompressed_and_cached(client: TestClient) -> None:
    gz = client.get("/static/js/core.js", headers={"Accept-Encoding": "gzip"})
    plain = client.get("/static/js/core.js", headers={"Accept-Encoding": "identity"})
    assert gz.headers["content-encoding"] == "gzip" and "immutable" in gz.headers["cache-control"]
    assert gz.text == plain.text  # same content, compressed once and reused
    font = client.get("/static/fonts/noto-sans-tamil.woff2", headers={"Accept-Encoding": "gzip"})
    assert font.headers["content-encoding"] == "identity"  # woff2 is already compressed: never re-gzipped
    assert font.headers["content-type"] == "font/woff2"


def test_language_picker_uses_system_font_so_only_one_script_font_loads() -> None:
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".lang-picker select, .lang-picker option" in css
    rule = css.split(".lang-picker select, .lang-picker option", 1)[1].split("}", 1)[0]
    assert "system-ui" in rule and "Noto" not in rule


def test_production_docs_return_the_same_generic_404_as_unknown_routes() -> None:
    with TestClient(create_app(make_settings(env="production", gemini_api_key="k"))) as c:
        responses = [c.get(path) for path in ("/docs", "/openapi.json", "/redoc", "/does-not-exist")]
    assert {r.status_code for r in responses} == {404}
    assert len({r.text for r in responses}) == 1  # identical bodies: nothing reveals which routes exist
