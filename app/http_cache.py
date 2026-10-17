"""HTTP caching helpers: strong ETags and 304 revalidation for bytes prepared ahead of time."""

from __future__ import annotations

import hashlib

from fastapi import Request
from fastapi.responses import Response


def etag_for(data: bytes) -> str:
    """Strong validator for a response body."""
    return '"' + hashlib.sha256(data).hexdigest()[:20] + '"'


def cached_bytes_response(request: Request, body: bytes, media_type: str, cache_control: str) -> Response:
    """Serve prepared bytes with an ETag; answer 304 when the browser already has them."""
    tag = etag_for(body)
    headers = {"ETag": tag, "Cache-Control": cache_control}
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=media_type, headers=headers)
