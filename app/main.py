"""Application factory: middleware, routers, static files and /health.

Run with ``uvicorn app.main:create_app --factory``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.cache import TTLCache
from app.config import ConfigError, Settings, get_settings, validate_settings
from app.errors import register_exception_handlers
from app.llm.client import GeminiClient, LLMClient, build_llm_client
from app.routers import ask, documents, grievance, ledger, meta, safety, schemes, voice
from app.security import RateLimiter, SecurityMiddleware
from app.services.data_store import get_data_store
from app.services.uploads import MAX_AUDIO_BYTES, MAX_IMPORT_BYTES

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ASSET_VERSION_MARKER = "__ASSET_VERSION__"
# Raw-upload endpoints get their own body cap; everything else keeps settings.max_body_bytes.
UPLOAD_LIMITS: dict[str, int] = {ledger.IMPORT_FILE_PATH: MAX_IMPORT_BYTES, voice.TRANSCRIBE_PATH: MAX_AUDIO_BYTES}

logger = logging.getLogger("saathi")


class CachedStaticFiles(StaticFiles):
    """Static files with long-lived cache headers; URLs are versioned by content hash."""

    async def get_response(self, path: str, scope: Scope):  # type: ignore[no-untyped-def]
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=604800, immutable"
        return response


def _asset_version(static_dir: Path) -> str:
    """Content hash of every script and stylesheet, so any change busts the browser cache."""
    digest = hashlib.sha256()
    for path in sorted([*static_dir.glob("*.css"), *static_dir.glob("js/*.js")]):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _render_index(static_dir: Path) -> str:
    """Read index.html once and stamp asset URLs with a content hash for cache busting."""
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    return html.replace(ASSET_VERSION_MARKER, _asset_version(static_dir))


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")


def ensure_production_safe(settings: Settings, llm_client: LLMClient) -> None:
    """In production only the real Gemini client may be used. Fakes are test-only."""
    if settings.env == "production" and not isinstance(llm_client, GeminiClient):
        raise ConfigError("Only GeminiClient may be used when ENV=production.")


def create_app(settings: Settings | None = None, llm_client: LLMClient | None = None) -> FastAPI:
    """Build the FastAPI application."""
    settings = settings or get_settings()
    _configure_logging(settings.log_level)
    validate_settings(settings)
    client = llm_client or build_llm_client(settings)
    ensure_production_safe(settings, client)

    is_prod = settings.env == "production"
    app = FastAPI(
        title="Saathi - Gig Worker Companion",
        version="1.0.0",
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.state.settings = settings
    app.state.llm_client = client
    app.state.data_store = get_data_store()
    app.state.ai_cache = TTLCache[str](settings.ai_cache_size, settings.ai_cache_ttl_seconds)
    app.state.rate_limiter = RateLimiter(settings.ai_rate_limit_per_minute)

    register_exception_handlers(app)
    app.add_middleware(GZipMiddleware, minimum_size=500)
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
    app.add_middleware(SecurityMiddleware, max_body_bytes=settings.max_body_bytes, path_limits=UPLOAD_LIMITS)

    for module in (meta, ledger, safety, schemes, documents, grievance, ask, voice):
        app.include_router(module.router)

    index_html = _render_index(STATIC_DIR)

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(index_html, headers={"Cache-Control": "no-cache"})

    app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")
    logger.info("startup env=%s ai_configured=%s model=%s", settings.env, settings.ai_configured, settings.gemini_model)
    return app
