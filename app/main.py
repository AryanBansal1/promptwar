"""Application factory: middleware, routers, static files and /health.

Run with ``uvicorn app.main:create_app --factory``. Vercel imports the module-level ``app``.
"""

from __future__ import annotations

import gzip
import hashlib
import logging
import mimetypes
from pathlib import Path

import anyio
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.cache import TTLCache
from app.config import ConfigError, Settings, get_settings, validate_settings
from app.errors import register_exception_handlers
from app.http_cache import cached_bytes_response
from app.llm.client import GeminiClient, LLMClient, build_llm_client
from app.routers import ask, documents, grievance, ledger, meta, safety, schemes, voice
from app.security import Limiters, RateLimiter, SecurityMiddleware, api_rate_limit
from app.services.data_store import get_data_store
from app.services.uploads import MAX_AUDIO_BYTES, MAX_IMPORT_BYTES

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ASSET_VERSION_MARKER = "__ASSET_VERSION__"
# Raw-upload endpoints get their own body cap and skip the JSON content-type rule.
UPLOAD_LIMITS: dict[str, int] = {ledger.IMPORT_FILE_PATH: MAX_IMPORT_BYTES, voice.TRANSCRIBE_PATH: MAX_AUDIO_BYTES}
_COMPRESSIBLE = {".js", ".css", ".html", ".txt", ".json", ".svg"}
_PRECOMPRESSED = {".woff2", ".png", ".jpg", ".jpeg", ".webp", ".pdf"}  # gzip would only waste CPU
mimetypes.add_type("font/woff2", ".woff2")

logger = logging.getLogger("saathi")


class CachedStaticFiles(StaticFiles):
    """Static files with long-lived cache headers (URLs carry a content hash). Text assets are gzipped
    once and kept in memory; already-compressed formats (fonts, images, PDF) are never re-compressed."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory=directory)
        self._gzipped: dict[str, bytes] = {}  # path -> gzip bytes, filled on first request

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code != 200:
            return response
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        suffix = Path(path).suffix
        if suffix in _PRECOMPRESSED:
            response.headers["Content-Encoding"] = "identity"  # tells GZipMiddleware to leave it alone
            return response
        accepts_gzip = b"gzip" in dict(scope.get("headers", [])).get(b"accept-encoding", b"")
        if accepts_gzip and suffix in _COMPRESSIBLE:
            return await self._gzip_response(path, response)
        return response

    async def _gzip_response(self, path: str, response: Response) -> Response:
        """The same file, gzipped once and served from memory afterwards."""
        compressed = self._gzipped.get(path)
        if compressed is None:
            full, _ = self.lookup_path(path)
            data = await anyio.Path(full).read_bytes()  # off the event loop
            compressed = self._gzipped[path] = gzip.compress(data, compresslevel=9, mtime=0)
        headers = dict(response.headers)
        headers.pop("content-length", None)
        headers.update({"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        return Response(compressed, status_code=200, headers=headers, media_type=response.media_type)


def _asset_version(static_dir: Path) -> str:
    """Content hash of every script and stylesheet, so any change busts the browser cache."""
    digest = hashlib.sha256()
    for path in sorted([*static_dir.glob("*.css"), *static_dir.glob("js/*.js")]):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def _render_index(static_dir: Path) -> bytes:
    """Read index.html once and stamp asset URLs with a content hash for cache busting."""
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    return html.replace(ASSET_VERSION_MARKER, _asset_version(static_dir)).encode()


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
    app.state.limiters = Limiters(
        ai=RateLimiter(settings.ai_rate_limit_per_minute),
        api=RateLimiter(settings.api_rate_limit_per_minute),
        ai_global=RateLimiter(settings.ai_global_per_minute),
    )

    register_exception_handlers(app)
    app.add_middleware(GZipMiddleware, minimum_size=500)
    if settings.allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
    app.add_middleware(SecurityMiddleware, max_body_bytes=settings.max_body_bytes, raw_paths=UPLOAD_LIMITS)

    app.include_router(meta.router)
    for module in (ledger, safety, schemes, documents, grievance, ask, voice):
        app.include_router(module.router, dependencies=[Depends(api_rate_limit)])

    index_html = _render_index(STATIC_DIR)

    @app.get("/", include_in_schema=False)
    async def index(request: Request) -> Response:
        return cached_bytes_response(request, index_html, "text/html; charset=utf-8", "no-cache")

    app.mount("/static", CachedStaticFiles(directory=STATIC_DIR), name="static")
    logger.info("startup env=%s ai_configured=%s model=%s", settings.env, settings.ai_configured, settings.gemini_model)
    return app


# Module-level instance for platforms that import an ASGI app (Vercel). Settings come from the
# environment; with ENV=production a missing GEMINI_API_KEY fails the import with a clear log.
app = create_app()
