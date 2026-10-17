"""Security middleware, per-IP rate limiting and input/output sanitising helpers."""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import uuid
from collections import deque

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import AppError, error_body

logger = logging.getLogger("saathi.access")

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    # Microphone only for this origin (voice input); everything else stays denied.
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=(), usb=(), display-capture=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityMiddleware:
    """Pure-ASGI middleware: request ids, access logs, security headers and a body-size cap.

    Logs carry only request id, method, route, status and latency, never bodies,
    query strings or client IPs.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int, path_limits: dict[str, int] | None = None) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.path_limits = path_limits or {}  # larger caps for the upload endpoints only
        self._encoded_headers = [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        max_body = self.path_limits.get(scope.get("path", ""), self.max_body_bytes)
        request_id = uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status_holder = {"status": 500}

        declared = _content_length(scope)
        if declared is not None and declared > max_body:
            await _send_json(send, 413, error_body("payload_too_large", "Request is too large."))
            self._log(scope, request_id, 413, started)
            return

        received = 0
        too_large = False

        async def limited_receive() -> Message:
            # Streamed bodies without Content-Length: stop reading once over the cap and
            # report a disconnect so the app stops; we answer 413 ourselves below.
            nonlocal received, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_body:
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def send_wrapper(message: Message) -> None:
            if too_large:
                return  # the app's response is replaced by the 413 below
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.extend(self._encoded_headers)
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            try:
                await self.app(scope, limited_receive, send_wrapper)
            except Exception:
                if not too_large:
                    raise
            if too_large:
                status_holder["status"] = 413
                await _send_json(send, 413, error_body("payload_too_large", "Request is too large."))
        finally:
            self._log(scope, request_id, status_holder["status"], started)

    @staticmethod
    def _log(scope: Scope, request_id: str, status: int, started: float) -> None:
        route = scope.get("route")
        path = getattr(route, "path", None) or scope.get("path", "")
        latency_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "request_id=%s method=%s route=%s status=%s latency_ms=%.1f",
            request_id,
            scope.get("method"),
            path,
            status,
            latency_ms,
        )


def _content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers", []):
        if key == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_json(send: Send, status: int, body: dict[str, object]) -> None:
    payload = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]
    headers.extend((k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items())
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


class RateLimiter:
    """In-memory sliding-window limiter keyed by client IP (per Cloud Run instance)."""

    def __init__(self, limit: int, window_seconds: float = 60.0, max_keys: int = 50_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, now: float | None = None) -> float | None:
        """Record a hit. Return ``None`` if allowed, else seconds until the next slot frees."""
        now = time.monotonic() if now is None else now
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_keys:
                self._evict(now)
            hits = self._hits[key] = deque()
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(0.0, self.window - (now - hits[0]))
        hits.append(now)
        return None

    def _evict(self, now: float) -> None:
        stale = [k for k, d in self._hits.items() if not d or now - d[-1] >= self.window]
        for key in stale:
            del self._hits[key]
        if len(self._hits) >= self.max_keys:
            self._hits.clear()

    def reset(self) -> None:
        """Forget all recorded hits."""
        self._hits.clear()


async def ai_rate_limit(request: Request) -> None:
    """FastAPI dependency applied to every endpoint that calls Gemini."""
    limiter: RateLimiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client else "unknown"
    retry_after = limiter.check(client_ip)
    if retry_after is not None:
        raise AppError(
            "rate_limited",
            "Too many AI requests. Please wait a minute and try again.",
            status_code=429,
            headers={"Retry-After": str(int(retry_after) + 1)},
        )


_CONTROL_CHARS = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏‪-‮]")
_DELIMITER = re.compile(r"</?\s*user_input\s*>", re.IGNORECASE)
_PHONE_CANDIDATE = re.compile(r"(?<!\w)\+?\d[\d\s\-]{6,}\d(?!\w)")
_MIN_PHONE_DIGITS = 10  # Indian mobile/landline/toll-free numbers have 10+ digits; dates have 8


def clean_text(value: str) -> str:
    """Normalise Unicode, drop control/bidi characters and trim whitespace."""
    return _CONTROL_CHARS.sub("", unicodedata.normalize("NFC", value)).strip()


def wrap_user_text(value: str) -> str:
    """Wrap untrusted user text in delimiters the system prompt treats as data only."""
    return f"<user_input>\n{_DELIMITER.sub('', clean_text(value))}\n</user_input>"


def cap_text(value: str, max_chars: int) -> str:
    """Trim model output to a hard character limit on a word boundary."""
    value = value.strip()
    if len(value) <= max_chars:
        return value
    cut = value[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def strip_phone_numbers(value: str, replacement: str = "[see the helpline buttons]") -> str:
    """Remove phone-number-like sequences so only the app's hardcoded helplines are shown."""

    def _replace(match: re.Match[str]) -> str:
        digits = sum(ch.isdigit() for ch in match.group())
        return replacement if digits >= _MIN_PHONE_DIGITS else match.group()

    return _PHONE_CANDIDATE.sub(_replace, value)
