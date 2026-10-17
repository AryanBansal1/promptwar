"""Security middleware, rate limiting and input/output sanitising helpers."""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import AppError, error_body

logger = logging.getLogger("saathi.access")

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; font-src 'self'; media-src 'self'; manifest-src 'self'; object-src 'none'; "
        "frame-src 'none'; worker-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    # Microphone only for this origin (voice input); everything else stays denied.
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=(), usb=(), display-capture=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "X-Permitted-Cross-Domain-Policies": "none",
}
_ENCODED_HEADERS = [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]
_NO_STORE = (b"cache-control", b"no-store")
JSON_TYPE = "application/json"


@dataclass(frozen=True)
class BodyRule:
    """Per-path request body policy: a size cap, and whether raw (non-JSON) bodies are expected."""

    max_bytes: int
    raw: bool = False


@dataclass
class _Exchange:
    """State for one request: a body-size-capped ``receive`` and a header-adding ``send``."""

    receive: Receive
    send: Send
    rule: BodyRule
    is_api: bool
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    status: int = 500
    response_started: bool = False
    received: int = 0
    too_large: bool = False

    async def receive_capped(self) -> Message:
        """Stop reading once the body passes the cap (also for streamed bodies without Content-Length)
        and report a disconnect so the app stops; the middleware then answers 413 itself."""
        if self.too_large:
            return {"type": "http.disconnect"}
        message = await self.receive()
        if message["type"] == "http.request":
            self.received += len(message.get("body", b""))
            if self.received > self.rule.max_bytes:
                self.too_large = True
                return {"type": "http.disconnect"}
        return message

    async def send_with_headers(self, message: Message) -> None:
        """Add security headers, the request id and, for API answers, ``Cache-Control: no-store``."""
        if self.too_large:
            return  # the app's response is replaced by the 413
        if message["type"] == "http.response.start":
            self.status, self.response_started = message["status"], True
            headers = list(message.get("headers", []))
            headers.extend(_ENCODED_HEADERS)
            headers.append((b"x-request-id", self.request_id.encode()))
            if self.is_api and not any(key == b"cache-control" for key, _ in headers):
                headers.append(_NO_STORE)  # API answers are personal: never cache them
            message["headers"] = headers
        await self.send(message)


class SecurityMiddleware:
    """Pure-ASGI middleware: request ids, safe access logs, security headers, body caps,
    content-type enforcement, and a last-resort error barrier.

    Logs carry only request id, method, route template, status and latency: never bodies,
    query strings, client IPs, raw paths, exception messages or tracebacks.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int, raw_paths: dict[str, int] | None = None) -> None:
        self.app = app
        self.default_rule = BodyRule(max_body_bytes)
        self.rules = {path: BodyRule(cap, raw=True) for path, cap in (raw_paths or {}).items()}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        exchange = _Exchange(receive, send, self.rules.get(path, self.default_rule), path.startswith("/api/"))
        scope.setdefault("state", {})["request_id"] = exchange.request_id
        started = time.perf_counter()

        rejection = _reject_early(scope, exchange.rule)
        if rejection is not None:
            exchange.status, code, message = rejection
            await _send_json(send, exchange.status, error_body(code, message))
        else:
            await self._run(scope, exchange)
        self._log(scope, exchange.request_id, exchange.status, started)

    async def _run(self, scope: Scope, exchange: _Exchange) -> None:
        """Call the app; replace its answer with 413 or a generic 500 when needed."""
        try:
            await self.app(scope, exchange.receive_capped, exchange.send_with_headers)
        except Exception as exc:  # last-resort barrier: nothing internal may leak
            if not exchange.too_large:
                logger.error("unhandled_error request_id=%s type=%s", exchange.request_id, type(exc).__name__)
                if not exchange.response_started:
                    exchange.status = 500
                    await _send_json(
                        exchange.send, 500, error_body("internal_error", "Something went wrong on our side.")
                    )
        if exchange.too_large:
            exchange.status = 413
            await _send_json(exchange.send, 413, error_body("payload_too_large", "Request is too large."))

    @staticmethod
    def _log(scope: Scope, request_id: str, status: int, started: float) -> None:
        route = scope.get("route")
        # Only the route *template* is logged; raw paths are attacker-controlled (log injection, PII).
        path = getattr(route, "path", None) or ("/static/*" if scope.get("path", "").startswith("/static/") else "-")
        logger.info(
            "request_id=%s method=%s route=%s status=%s latency_ms=%.1f",
            request_id,
            scope.get("method"),
            path,
            status,
            (time.perf_counter() - started) * 1000,
        )


def _header(scope: Scope, name: bytes) -> str | None:
    """First value of a request header (ASGI headers are lower-case latin-1 byte pairs)."""
    for key, value in scope.get("headers", []):
        if key == name:
            return bytes(value).decode("latin-1")
    return None


def _reject_early(scope: Scope, rule: BodyRule) -> tuple[int, str, str] | None:
    """Reject before reading the body: declared size over the cap, or an unexpected content type."""
    declared = _header(scope, b"content-length")
    if declared is not None:
        try:
            if int(declared) > rule.max_bytes:
                return 413, "payload_too_large", "Request is too large."
        except ValueError:
            return 400, "bad_request", "Invalid Content-Length."
    if scope.get("method") == "POST" and not rule.raw:
        content_type = (_header(scope, b"content-type") or "").split(";", 1)[0].strip().lower()
        if content_type != JSON_TYPE:
            return 415, "unsupported_media_type", "Send JSON with Content-Type: application/json."
    return None


async def _send_json(send: Send, status: int, body: dict[str, object]) -> None:
    payload = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()), _NO_STORE]
    headers.extend(_ENCODED_HEADERS)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": payload})


# ---------- Rate limiting ----------


class RateLimiter:
    """In-memory, cost-weighted sliding window keyed by client (per serverless instance).

    Each request spends ``cost`` units (an image import costs more than a chat message).
    State lives in this process only; see README "Rate limiting" for the shared-store fix.
    """

    def __init__(self, limit: int, window_seconds: float = 60.0, max_keys: int = 50_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[tuple[float, int]]] = {}
        self._spent: dict[str, int] = {}

    def check(self, key: str, now: float | None = None, cost: int = 1) -> float | None:
        """Spend ``cost`` units. Return ``None`` if allowed, else seconds until enough units free up."""
        now = time.monotonic() if now is None else now
        hits = self._hits.get(key)
        if hits is None:
            if len(self._hits) >= self.max_keys:
                self._evict(now)
            hits = self._hits[key] = deque()
            self._spent[key] = 0
        while hits and now - hits[0][0] >= self.window:
            self._spent[key] -= hits.popleft()[1]
        if self._spent[key] + cost > self.limit:
            return max(0.0, self.window - (now - hits[0][0])) if hits else self.window
        hits.append((now, cost))
        self._spent[key] += cost
        return None

    def _evict(self, now: float) -> None:
        stale = [k for k, d in self._hits.items() if not d or now - d[-1][0] >= self.window]
        for key in stale:
            del self._hits[key]
            del self._spent[key]
        if len(self._hits) >= self.max_keys:
            self._hits.clear()
            self._spent.clear()

    def reset(self) -> None:
        """Forget all recorded hits."""
        self._hits.clear()
        self._spent.clear()


@dataclass
class Limiters:
    """Per-client AI budget, per-client general API budget, and one per-instance AI budget."""

    ai: RateLimiter
    api: RateLimiter
    ai_global: RateLimiter


VERCEL_CLIENT_IP_HEADER = "x-vercel-forwarded-for"


def client_ip(request: Request) -> str:
    """The caller's IP address, used only as the rate-limit key.

    Why this is safe:
    - On Vercel (``VERCEL=1``, set by the platform) every request passes through Vercel's edge, which
      writes the client's public IP into ``x-vercel-forwarded-for`` and replaces any value the client
      sent. Vercel documents that this header, unlike ``x-forwarded-for``, is not overwritten by a
      proxy placed in front of Vercel.
    - Everywhere else (local, Docker, Cloud Run) no header is trusted at all: the IP is the direct
      connection address, so a client cannot pick its own rate-limit key by sending headers.
    """
    if request.app.state.settings.vercel:
        first = request.headers.get(VERCEL_CLIENT_IP_HEADER, "").split(",", 1)[0].strip()
        if first:
            return first[:64]
    return request.client.host if request.client else "unknown"


def _too_many(retry_after: float) -> AppError:
    return AppError(
        "rate_limited",
        "Too many requests. Please wait a minute and try again.",
        status_code=429,
        headers={"Retry-After": str(int(retry_after) + 1)},
    )


def ai_rate_limit(cost: int = 1) -> Callable[[Request], Awaitable[None]]:
    """Dependency factory for Gemini endpoints: per-client and per-instance budgets, weighted by cost."""

    async def dependency(request: Request) -> None:
        limiters: Limiters = request.app.state.limiters
        retry_after = limiters.ai.check(client_ip(request), cost=cost)
        if retry_after is None:
            retry_after = limiters.ai_global.check("instance", cost=cost)
        if retry_after is not None:
            raise _too_many(retry_after)

    return dependency


async def api_rate_limit(request: Request) -> None:
    """Dependency for every /api route: a generous per-client cap against floods of cheap requests."""
    limiters: Limiters = request.app.state.limiters
    retry_after = limiters.api.check(client_ip(request))
    if retry_after is not None:
        raise _too_many(retry_after)


# ---------- Text sanitising (inputs and LLM outputs) ----------

_CONTROL_CHARS = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏‪-‮⁦-⁩]")
_DELIMITER = re.compile(r"</?\s*user_input\s*>", re.IGNORECASE)
_PHONE_CANDIDATE = re.compile(r"(?<!\w)\+?\d[\d\s\-]{6,}\d(?!\w)")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+")
_MIN_PHONE_DIGITS = 10  # Indian mobile/landline/toll-free numbers have 10+ digits; dates have 8
LINK_REMOVED = "[link removed]"


def clean_text(value: str) -> str:
    """Normalise Unicode, drop control/bidi characters and trim whitespace (newlines are kept)."""
    return _CONTROL_CHARS.sub("", unicodedata.normalize("NFC", value)).strip()


def wrap_user_text(value: str) -> str:
    """Wrap untrusted user text in delimiters the system prompt treats as data only."""
    return f"<user_input>\n{_DELIMITER.sub('', clean_text(value))}\n</user_input>"


def cap_text(value: str, max_chars: int) -> str:
    """Trim text to a hard character limit on a word boundary."""
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


def safe_ai_text(value: str, max_chars: int, *, strip_phones: bool = False) -> str:
    """Every piece of LLM text passes through here before reaching a client: control and bidi
    characters removed, links removed (only curated links are ever shown), optionally phone
    numbers removed, and length capped. The browser renders it with textContent only."""
    text = _URL.sub(LINK_REMOVED, clean_text(value))
    if strip_phones:
        text = strip_phone_numbers(text)
    return cap_text(text, max_chars)
