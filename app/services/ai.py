"""Shared helpers for calling the LLM with output caps and optional caching."""

from __future__ import annotations

from app.cache import TTLCache, cache_key
from app.llm.client import LLMClient, LLMRequest, ai_empty
from app.llm.prompts import SPECS
from app.security import cap_text


async def generate_capped_text(
    llm: LLMClient,
    feature: str,
    request: LLMRequest,
    cache: TTLCache[str] | None = None,
) -> str:
    """Generate text, cap its length, and reuse a cached result for identical requests."""
    key = cache_key(feature, request.system, request.prompt) if cache is not None else ""
    if cache is not None and (hit := cache.get(key)) is not None:
        return hit
    text = cap_text(await llm.generate_text(request), SPECS[feature].max_chars)
    if not text:
        raise ai_empty()
    if cache is not None:
        cache.set(key, text)
    return text


def cap_items(items: list[str], feature: str, max_items: int) -> list[str]:
    """Drop empty strings, cap each item's length and the number of items."""
    per_item = SPECS[feature].max_chars // max(1, max_items)
    cleaned = [cap_text(item, per_item) for item in items if item and item.strip()]
    if not cleaned:
        raise ai_empty()
    return cleaned[:max_items]
