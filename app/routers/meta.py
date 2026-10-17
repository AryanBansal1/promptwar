"""Health check and translations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.deps import StoreDep
from app.http_cache import cached_bytes_response
from app.models.common import Language

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness probe; reports whether AI is configured (never the key)."""
    return {"status": "ok", "ai_configured": request.app.state.settings.ai_configured}


@router.get("/api/i18n/{lang}")
async def translations(lang: Language, store: StoreDep, request: Request) -> Response:
    """UI strings for one language: bytes prepared at startup, with an ETag for cheap revalidation."""
    return cached_bytes_response(request, store.i18n_json[lang], "application/json", "public, max-age=3600")
