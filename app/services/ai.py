"""Shared helpers for calling the LLM with sanitised, capped output and optional caching."""

from __future__ import annotations

from collections.abc import Hashable

from app.cache import TTLCache
from app.llm.client import LLMClient, LLMRequest, ai_empty
from app.llm.prompts import SPECS
from app.security import safe_ai_text


async def generate_capped_text(
    llm: LLMClient,
    feature: str,
    request: LLMRequest,
    cache: TTLCache[str] | None = None,
    cache_key: tuple[Hashable, ...] | None = None,
) -> str:
    """Generate text, sanitise and cap it, and reuse a cached result for identical curated inputs.

    ``cache_key`` names the deterministic inputs (feature, language, record ids) so a lookup costs a
    tuple hash, not a hash of the whole prompt.
    """
    key = (feature, *cache_key) if cache is not None and cache_key is not None else None
    if key is not None and cache is not None and (hit := cache.get(key)) is not None:
        return hit
    text = safe_ai_text(await llm.generate_text(request), SPECS[feature].max_chars)
    if not text:
        raise ai_empty()
    if key is not None and cache is not None:
        cache.set(key, text)
    return text


def cap_items(items: list[str], feature: str, max_items: int) -> list[str]:
    """Sanitise and cap each item, drop empty ones, and cap the number of items."""
    per_item = SPECS[feature].max_chars // max(1, max_items)
    cleaned = [text for item in items[: max_items * 2] if (text := safe_ai_text(item or "", per_item))]
    if not cleaned:
        raise ai_empty()
    return cleaned[:max_items]
