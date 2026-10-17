"""Health check and translations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response

from app.deps import StoreDep
from app.models.common import Language

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness probe; reports whether AI is configured (never the key)."""
    return {"status": "ok", "ai_configured": request.app.state.settings.ai_configured}


@router.get("/api/i18n/{lang}")
async def translations(lang: Language, store: StoreDep, response: Response) -> dict[str, Any]:
    """UI strings for one language."""
    response.headers["Cache-Control"] = "public, max-age=3600"
    return store.i18n[lang]
