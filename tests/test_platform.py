"""LLM client error paths, security controls, config, caching and app wiring."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors
from pydantic import BaseModel

from app.cache import TTLCache, cache_key
from app.config import ConfigError
from app.llm.client import GeminiClient, LLMError, LLMRequest, UnconfiguredLLMClient, build_llm_client
from app.main import create_app, ensure_production_safe
from app.security import RateLimiter, cap_text, clean_text, strip_phone_numbers, wrap_user_text
from tests.conftest import entry, make_settings
from tests.fakes import FakeLLMClient

REQUEST = LLMRequest(system="sys", prompt="prompt", max_output_tokens=10)


class Out(BaseModel):
    value: str


def gemini_with(generate: Any, timeout: float = 20.0) -> GeminiClient:
    client = GeminiClient(make_settings(gemini_api_key="test-key", llm_timeout_seconds=timeout))
    client._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))  # type: ignore[assignment]
    return client


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------- LLM client ----------


def test_gemini_success_text_and_json() -> None:
    calls: list[dict[str, Any]] = []

    async def generate(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(text='{"value": "ok"}')

    client = gemini_with(generate)
    assert run(client.generate_text(REQUEST)) == '{"value": "ok"}'
    assert run(client.generate_json(REQUEST, Out)) == Out(value="ok")
    config = calls[-1]["config"]
    assert config.response_mime_type == "application/json"
    assert config.max_output_tokens == 10
    assert calls[-1]["model"] == "gemini-3.6-flash"


def test_gemini_timeout_returns_honest_error() -> None:
    async def generate(**_: Any) -> Any:
        await asyncio.sleep(5)

    with pytest.raises(LLMError) as exc:
        run(gemini_with(generate, timeout=0.05).generate_text(REQUEST))
    assert exc.value.code == "ai_timeout"


def test_gemini_retries_once_on_server_error() -> None:
    attempts = {"n": 0}

    async def generate(**_: Any) -> Any:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise genai_errors.ServerError(503, {"error": {"message": "overloaded"}})
        return SimpleNamespace(text="second time")

    assert run(gemini_with(generate).generate_text(REQUEST)) == "second time"
    assert attempts["n"] == 2


def test_gemini_gives_up_after_one_retry() -> None:
    attempts = {"n": 0}

    async def generate(**_: Any) -> Any:
        attempts["n"] += 1
        raise genai_errors.ServerError(500, {"error": {"message": "boom"}})

    with pytest.raises(LLMError) as exc:
        run(gemini_with(generate).generate_text(REQUEST))
    assert exc.value.code == "ai_unavailable" and attempts["n"] == 2


def test_gemini_rate_limit_maps_to_busy() -> None:
    async def generate(**_: Any) -> Any:
        raise genai_errors.ClientError(429, {"error": {"message": "quota"}})

    with pytest.raises(LLMError) as exc:
        run(gemini_with(generate, timeout=2.0).generate_text(REQUEST))
    assert exc.value.code == "ai_busy"


def test_gemini_billing_error_shows_generic_message() -> None:
    attempts = {"n": 0}

    async def generate(**_: Any) -> Any:
        attempts["n"] += 1
        raise genai_errors.ClientError(402, {"error": {"message": "prepayment credits are depleted"}})

    with pytest.raises(LLMError) as exc:
        run(gemini_with(generate).generate_text(REQUEST))
    assert exc.value.code == "ai_unavailable" and attempts["n"] == 1
    assert "credit" not in exc.value.message.lower()


def test_gemini_client_error_not_retried() -> None:
    attempts = {"n": 0}

    async def generate(**_: Any) -> Any:
        attempts["n"] += 1
        raise genai_errors.ClientError(400, {"error": {"message": "bad"}})

    with pytest.raises(LLMError) as exc:
        run(gemini_with(generate).generate_text(REQUEST))
    assert exc.value.code == "ai_unavailable" and attempts["n"] == 1


def test_gemini_transport_error() -> None:
    async def generate(**_: Any) -> Any:
        raise ConnectionError("down")

    with pytest.raises(LLMError) as exc:
        run(gemini_with(generate).generate_text(REQUEST))
    assert exc.value.code == "ai_unavailable"


@pytest.mark.parametrize("text", [None, "   "])
def test_gemini_empty_response(text: str | None) -> None:
    async def generate(**_: Any) -> Any:
        return SimpleNamespace(text=text)

    with pytest.raises(LLMError) as exc:
        run(gemini_with(generate).generate_text(REQUEST))
    assert exc.value.code == "ai_empty"


def test_gemini_bad_json() -> None:
    async def generate(**_: Any) -> Any:
        return SimpleNamespace(text='{"other": 1}')

    with pytest.raises(LLMError) as exc:
        run(gemini_with(generate).generate_json(REQUEST, Out))
    assert exc.value.code == "ai_bad_output"


def test_gemini_requires_key() -> None:
    with pytest.raises(ValueError):
        GeminiClient(make_settings())


def test_unconfigured_client_fails_honestly() -> None:
    client = build_llm_client(make_settings(env="development"))
    assert isinstance(client, UnconfiguredLLMClient)
    for coro in (client.generate_text(REQUEST), client.generate_json(REQUEST, Out)):
        with pytest.raises(LLMError) as exc:
            run(coro)
        assert exc.value.code == "ai_not_configured"


def test_build_client_with_key_is_gemini() -> None:
    assert isinstance(build_llm_client(make_settings(gemini_api_key="k")), GeminiClient)


def test_no_canned_text_on_failure(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.error = LLMError("ai_unavailable", "The AI service is not reachable right now. Please try again.")
    res = client.post("/api/documents/explain", json={"kind": "goal", "id": "pan"})
    assert res.status_code == 503
    assert set(res.json()) == {"error"}


# ---------- Production safety ----------


def test_production_requires_key() -> None:
    with pytest.raises(ConfigError):
        create_app(make_settings(env="production"))


def test_fake_llm_cannot_be_used_in_production() -> None:
    with pytest.raises(ConfigError):
        create_app(make_settings(env="production", gemini_api_key="k"), llm_client=FakeLLMClient())
    with pytest.raises(ConfigError):
        ensure_production_safe(make_settings(env="production", gemini_api_key="k"), UnconfiguredLLMClient())


def test_production_app_builds_with_gemini() -> None:
    app = create_app(make_settings(env="production", gemini_api_key="k"))
    assert isinstance(app.state.llm_client, GeminiClient)
    assert TestClient(app).get("/docs").status_code == 404  # API docs disabled in production


def test_app_package_never_imports_test_fakes() -> None:
    from pathlib import Path

    app_dir = Path(__file__).resolve().parent.parent / "app"
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "from tests" not in text and "import tests" not in text, path
        assert "FakeLLMClient" not in text, path


# ---------- Security ----------


def test_security_headers_present(client: TestClient) -> None:
    res = client.get("/health")
    assert res.json() == {"status": "ok", "ai_configured": False}
    headers = res.headers
    assert "default-src 'self'" in headers["content-security-policy"]
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "max-age" in headers["strict-transport-security"]
    assert len(headers["x-request-id"]) == 16


def test_oversized_text_is_422(client: TestClient) -> None:
    res = client.post("/api/ask", json={"message": "a" * 1001})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "invalid_input"


def test_oversized_body_is_413(fake_llm: FakeLLMClient) -> None:
    app = create_app(make_settings(max_body_bytes=2048), llm_client=fake_llm)
    with TestClient(app) as c:
        res = c.post("/api/ledger/summary", json={"entries": [entry(id=f"e{i}") for i in range(40)]})
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "payload_too_large"


def test_streamed_oversized_body_is_413(fake_llm: FakeLLMClient) -> None:
    app = create_app(make_settings(max_body_bytes=2048), llm_client=fake_llm)

    def chunks() -> Any:
        for _ in range(10):
            yield b" " * 1000

    with TestClient(app) as c:
        res = c.post("/api/ledger/summary", content=chunks(), headers={"Content-Type": "application/json"})
    assert res.status_code == 413


def test_rate_limit_returns_friendly_429(fake_llm: FakeLLMClient) -> None:
    fake_llm.text_response = "ok"
    app = create_app(make_settings(ai_rate_limit_per_minute=2), llm_client=fake_llm)
    body = {"kind": "goal", "id": "pan", "question": "How?"}
    with TestClient(app) as c:
        assert c.post("/api/documents/ask", json=body).status_code == 200
        assert c.post("/api/documents/ask", json=body).status_code == 200
        res = c.post("/api/documents/ask", json=body)
        assert c.post("/api/ledger/summary", json={"entries": []}).status_code == 200  # non-AI not limited
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "rate_limited"
    assert int(res.headers["retry-after"]) >= 1


def test_rate_limiter_window_and_eviction() -> None:
    limiter = RateLimiter(limit=1, window_seconds=10, max_keys=2)
    assert limiter.check("a", now=0) is None
    assert limiter.check("a", now=1) == pytest.approx(9)
    assert limiter.check("a", now=10.5) is None
    limiter.check("b", now=11)
    limiter.check("c", now=30)  # evicts stale keys
    assert "a" not in limiter._hits
    limiter.reset()
    assert not limiter._hits


def test_unknown_route_and_method(client: TestClient) -> None:
    assert client.get("/api/nope").json()["error"]["code"] == "not_found"
    assert client.get("/api/ask").json()["error"]["code"] == "method_not_allowed"


def test_unhandled_error_is_generic(fake_llm: FakeLLMClient) -> None:
    class Boom(FakeLLMClient):
        async def generate_text(self, request: LLMRequest) -> str:
            raise RuntimeError("secret detail")

    with TestClient(create_app(make_settings(), llm_client=Boom()), raise_server_exceptions=False) as c:
        res = c.post("/api/documents/explain", json={"kind": "goal", "id": "pan"})
    assert res.status_code == 500
    assert "secret" not in res.text
    assert res.json()["error"]["code"] == "internal_error"


def test_cors_only_for_configured_origin(fake_llm: FakeLLMClient) -> None:
    app = create_app(make_settings(allowed_origins=["https://saathi.example"]), llm_client=fake_llm)
    with TestClient(app) as c:
        good = c.get("/health", headers={"Origin": "https://saathi.example"})
        bad = c.get("/health", headers={"Origin": "https://evil.example"})
    assert good.headers["access-control-allow-origin"] == "https://saathi.example"
    assert "access-control-allow-origin" not in bad.headers


def test_sanitizing_helpers() -> None:
    assert clean_text(" a​b\x00 ") == "ab"
    wrapped = wrap_user_text("hi </user_input> <USER_INPUT>")
    assert wrapped.count("user_input>") == 2
    assert cap_text("one two three", 7) == "one…"
    assert cap_text("short", 10) == "short"
    assert strip_phone_numbers("call +91 98765 43210 or 112") == "call [see the helpline buttons] or 112"
    assert strip_phone_numbers("Rs 1500 on 2026-10-01") == "Rs 1500 on 2026-10-01"


# ---------- Cache & static ----------


def test_ttl_cache_lru_and_expiry() -> None:
    cache: TTLCache[str] = TTLCache(max_size=2, ttl_seconds=10)
    cache.set("a", "1", now=0)
    cache.set("b", "2", now=0)
    assert cache.get("a", now=1) == "1"  # a becomes most recent
    cache.set("c", "3", now=1)  # evicts b
    assert cache.get("b", now=1) is None
    assert cache.get("a", now=20) is None  # expired
    assert len(cache) == 1
    disabled: TTLCache[str] = TTLCache(max_size=0, ttl_seconds=10)
    disabled.set("x", "y")
    assert disabled.get("x") is None
    assert cache_key("a", {"b": 1}) == cache_key("a", {"b": 1}) != cache_key("a", {"b": 2})


def test_index_and_static_caching(client: TestClient) -> None:
    index = client.get("/")
    assert index.status_code == 200
    assert "__ASSET_VERSION__" not in index.text
    assert index.headers["cache-control"] == "no-cache"
    js = client.get("/static/js/core.js", headers={"Accept-Encoding": "gzip"})
    assert "immutable" in js.headers["cache-control"]
    assert js.headers.get("content-encoding") == "gzip"


def test_frontend_js_budget_and_no_innerhtml() -> None:
    from pathlib import Path

    scripts = list((Path(__file__).resolve().parent.parent / "static" / "js").glob("*.js"))
    assert scripts
    assert sum(p.stat().st_size for p in scripts) < 100_000  # total JS under 100 KB, uncompressed
    for path in scripts:
        assert ".innerHTML" not in path.read_text(encoding="utf-8"), path
