"""Application error type and the central exception handlers.

Every error response has the shape ``{"error": {"code": str, "message": str}}``.
The frontend translates ``code`` into the user's language.
"""

from __future__ import annotations

import logging
from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.llm.client import LLMError

logger = logging.getLogger("saathi.errors")

_HTTP_CODES: dict[int, str] = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    429: "rate_limited",
}


class AppError(Exception):
    """A handled, user-facing error with a stable machine-readable code."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.headers = headers or {}


def error_body(code: str, message: str, **extra: object) -> dict[str, object]:
    """Build the standard error envelope."""
    return {"error": {"code": code, "message": message, **extra}}


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    err = cast(AppError, exc)
    return JSONResponse(error_body(err.code, err.message), status_code=err.status_code, headers=err.headers)


async def _llm_error_handler(_: Request, exc: Exception) -> JSONResponse:
    err = cast(LLMError, exc)
    # Honest failure: the client shows a translated message with a retry button.
    return JSONResponse(error_body(err.code, err.message, retryable=True), status_code=err.status_code)


async def _validation_handler(_: Request, exc: Exception) -> JSONResponse:
    err = cast(RequestValidationError, exc)
    # Report only field locations and messages; never echo the submitted values back.
    fields = [
        {"field": ".".join(str(p) for p in e.get("loc", ()) if p != "body"), "msg": e.get("msg")} for e in err.errors()
    ]
    return JSONResponse(
        error_body("invalid_input", "Some fields are missing or invalid.", fields=fields),
        status_code=422,
    )


async def _http_handler(_: Request, exc: Exception) -> JSONResponse:
    err = cast(StarletteHTTPException, exc)
    code = _HTTP_CODES.get(err.status_code, "http_error")
    return JSONResponse(error_body(code, str(err.detail)), status_code=err.status_code)


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "-")
    logger.error("unhandled_error request_id=%s type=%s", request_id, type(exc).__name__)
    return JSONResponse(error_body("internal_error", "Something went wrong on our side."), status_code=500)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all handlers so every failure uses the same envelope."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(LLMError, _llm_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
