"""A small in-memory LRU cache with TTL, used only for deterministic-input AI calls.

On serverless platforms each warm instance keeps its own copy; it is lost on a cold start.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Hashable
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Least-recently-used cache whose entries also expire after ``ttl_seconds``.

    Scope: reused only for scheme explanations and document explanations, whose
    prompts are fully determined by curated records plus the language. Chats,
    grievance drafts and intent routing are never cached.
    """

    def __init__(self, max_size: int, ttl_seconds: float) -> None:
        self.max_size = max_size
        self.ttl = ttl_seconds
        self._items: OrderedDict[Hashable, tuple[float, T]] = OrderedDict()

    def get(self, key: Hashable, now: float | None = None) -> T | None:
        """Return the cached value or ``None`` if missing/expired."""
        now = time.monotonic() if now is None else now
        item = self._items.get(key)
        if item is None:
            return None
        stored_at, value = item
        if now - stored_at > self.ttl:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return value

    def set(self, key: Hashable, value: T, now: float | None = None) -> None:
        """Store a value, evicting the least recently used entry when full."""
        if self.max_size <= 0:
            return
        now = time.monotonic() if now is None else now
        self._items[key] = (now, value)
        self._items.move_to_end(key)
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)
