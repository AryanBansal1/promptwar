"""LLM client abstraction and the production Gemini implementation.

The only implementation shipped in ``app/`` calls Gemini. Tests inject a fake
from ``tests/fakes.py`` through ``create_app(llm_client=...)``; the app factory
refuses any non-Gemini client when ``ENV=production``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from app.config import Settings

logger = logging.getLogger("saathi.llm")

ModelT = TypeVar("ModelT", bound=BaseModel)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MIN_RETRY_BUDGET_SECONDS = 3.0


class LLMError(Exception):
    """An honest, user-facing AI failure. Never replaced by pre-written text."""

    def __init__(self, code: str, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def ai_timeout() -> LLMError:
    return LLMError("ai_timeout", "The AI took too long to answer. Please try again.", 504)


def ai_unavailable() -> LLMError:
    return LLMError("ai_unavailable", "The AI service is not reachable right now. Please try again.")


def ai_busy() -> LLMError:
    return LLMError("ai_busy", "The AI service is busy. Please try again in a minute.")


def ai_empty() -> LLMError:
    return LLMError("ai_empty", "The AI could not answer this. Please rephrase and try again.", 502)


def ai_bad_output() -> LLMError:
    return LLMError("ai_bad_output", "The AI gave an unusable answer. Please try again.", 502)


def ai_not_configured() -> LLMError:
    return LLMError("ai_not_configured", "AI is not configured on this server.")


@dataclass(frozen=True)
class Attachment:
    """An in-memory file (image, PDF or audio) sent with a request. Never written to disk or logged."""

    data: bytes
    mime_type: str


@dataclass(frozen=True)
class LLMRequest:
    """Everything needed for one generation call."""

    system: str
    prompt: str
    max_output_tokens: int
    temperature: float = 0.4
    attachments: tuple[Attachment, ...] = ()
    timeout_seconds: float | None = None  # overrides the default deadline for slow inputs (images, audio)
    # Optional stricter schema sent to Gemini (e.g. with enums); the reply is still parsed with the
    # caller's schema so that each list item can be validated, and dropped, on its own.
    response_schema: type[BaseModel] | None = None

    def contents(self) -> str | list[types.Part]:
        """Prompt text, preceded by any attachments as inline parts."""
        if not self.attachments:
            return self.prompt
        parts = [types.Part.from_bytes(data=a.data, mime_type=a.mime_type) for a in self.attachments]
        return [*parts, types.Part.from_text(text=self.prompt)]


class LLMClient(Protocol):
    """Interface every LLM backend implements."""

    async def generate_text(self, request: LLMRequest) -> str:
        """Return plain text, or raise :class:`LLMError`."""
        ...

    async def generate_json(self, request: LLMRequest, schema: type[ModelT]) -> ModelT:
        """Return output validated against ``schema``, or raise :class:`LLMError`."""
        ...


class GeminiClient:
    """Async Gemini client with an overall deadline and one retry on transient failures."""

    def __init__(self, settings: Settings) -> None:
        if not settings.ai_configured or settings.gemini_api_key is None:
            raise ValueError("GeminiClient requires GEMINI_API_KEY")
        self._client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self._model = settings.gemini_model
        self._timeout = settings.llm_timeout_seconds
        self._thinking_budget = settings.gemini_thinking_budget

    async def generate_text(self, request: LLMRequest) -> str:
        """Generate plain text."""
        return await self._generate(request, schema=None)

    async def generate_json(self, request: LLMRequest, schema: type[ModelT]) -> ModelT:
        """Generate structured JSON constrained by ``schema`` and validate it."""
        raw = await self._generate(request, schema=schema)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            logger.warning("llm_bad_output schema=%s", schema.__name__)
            raise ai_bad_output() from exc

    def _config(self, request: LLMRequest, schema: type[BaseModel] | None) -> types.GenerateContentConfig:
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            max_output_tokens=request.max_output_tokens,
            temperature=request.temperature,
        )
        if self._thinking_budget >= 0:
            config.thinking_config = types.ThinkingConfig(thinking_budget=self._thinking_budget)
        if schema is not None:
            config.response_mime_type = "application/json"
            config.response_schema = request.response_schema or schema
        return config

    async def _generate(self, request: LLMRequest, schema: type[BaseModel] | None) -> str:
        config = self._config(request, schema)
        deadline = time.monotonic() + (request.timeout_seconds or self._timeout)
        for attempt in (1, 2):
            remaining = deadline - time.monotonic()
            started = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    self._client.aio.models.generate_content(
                        model=self._model, contents=request.contents(), config=config
                    ),
                    timeout=remaining,
                )
            except TimeoutError as exc:
                logger.warning("llm_timeout attempt=%d", attempt)
                raise ai_timeout() from exc
            except genai_errors.APIError as exc:
                logger.warning("llm_api_error attempt=%d status=%s", attempt, exc.code)
                retry_budget = deadline - time.monotonic()
                if exc.code in _RETRYABLE_STATUS and attempt == 1 and retry_budget > _MIN_RETRY_BUDGET_SECONDS:
                    await asyncio.sleep(0.5)
                    continue
                raise _api_error(exc.code) from exc
            except Exception as exc:  # network errors from the HTTP layer
                logger.warning("llm_transport_error attempt=%d type=%s", attempt, type(exc).__name__)
                raise ai_unavailable() from exc
            logger.info("llm_ok model=%s latency_ms=%.0f", self._model, (time.perf_counter() - started) * 1000)
            text = response.text
            if not text or not text.strip():
                raise ai_empty()
            return text
        raise ai_unavailable()  # pragma: no cover - loop always returns or raises


def _api_error(status: int | None) -> LLMError:
    """Map a Gemini API status to a user-facing error. Billing/account details are never exposed to users."""
    if status == 429:
        return ai_busy()
    return ai_unavailable()


class UnconfiguredLLMClient:
    """Used only in development without a key: every call fails honestly."""

    async def generate_text(self, request: LLMRequest) -> str:
        raise ai_not_configured()

    async def generate_json(self, request: LLMRequest, schema: type[ModelT]) -> ModelT:
        raise ai_not_configured()


def build_llm_client(settings: Settings) -> LLMClient:
    """Pick the real Gemini client, or the honest-failure client when no key is set (dev only)."""
    if settings.ai_configured:
        return GeminiClient(settings)
    logger.warning("GEMINI_API_KEY not set: AI features will return an 'AI not configured' error.")
    return UnconfiguredLLMClient()
