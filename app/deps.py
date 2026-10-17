"""FastAPI dependencies. Tests swap the LLM client via ``create_app(llm_client=...)``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.cache import TTLCache
from app.llm.client import LLMClient
from app.security import ai_rate_limit
from app.services.data_store import DataStore


def get_llm_client(request: Request) -> LLMClient:
    """The LLM client chosen at startup."""
    client: LLMClient = request.app.state.llm_client
    return client


def get_data_store(request: Request) -> DataStore:
    """Datasets loaded once at startup."""
    store: DataStore = request.app.state.data_store
    return store


def get_ai_cache(request: Request) -> TTLCache[str]:
    """Shared cache for deterministic-input AI calls."""
    cache: TTLCache[str] = request.app.state.ai_cache
    return cache


LLMDep = Annotated[LLMClient, Depends(get_llm_client)]
StoreDep = Annotated[DataStore, Depends(get_data_store)]
CacheDep = Annotated[TTLCache[str], Depends(get_ai_cache)]
# Rate-limit cost units: reading an image/PDF or audio is ~3x a text call.
AI_RATE_LIMIT = [Depends(ai_rate_limit(1))]
AI_HEAVY_RATE_LIMIT = [Depends(ai_rate_limit(3))]
