"""Shared fixtures. No network access and no real API key are ever used."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeLLMClient


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"env": "test", "gemini_api_key": None, "ai_rate_limit_per_minute": 1000}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    return FakeLLMClient()


@pytest.fixture
def client(fake_llm: FakeLLMClient) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings(), llm_client=fake_llm)) as test_client:
        yield test_client


def entry(**overrides: object) -> dict[str, object]:
    """A valid income entry payload."""
    base: dict[str, object] = {
        "id": "e1",
        "date": "2026-10-12",
        "kind": "income",
        "amount": 1200,
        "category": "earnings",
        "platform": "swiggy",
        "hours": 8,
    }
    base.update(overrides)
    return base
