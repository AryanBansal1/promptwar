"""Voice input fallback endpoint (the browser's Web Speech API is used first)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.deps import AI_RATE_LIMIT, LLMDep
from app.models.voice import TranscribeParams, TranscriptResponse
from app.services.uploads import AUDIO_TYPES, MAX_AUDIO_BYTES, validate_upload
from app.services.voice import transcribe

router = APIRouter(prefix="/api/voice", tags=["voice"])

TRANSCRIBE_PATH = "/api/voice/transcribe"


@router.post("/transcribe", dependencies=AI_RATE_LIMIT)
async def transcribe_audio(
    request: Request, params: Annotated[TranscribeParams, Query()], llm: LLMDep
) -> TranscriptResponse:
    """Turn a short recording (raw request body, max 2 MB) into text in the selected language."""
    audio = validate_upload(
        request.headers.get("content-type"),
        await request.body(),
        AUDIO_TYPES,
        MAX_AUDIO_BYTES,
        type_error="unsupported_audio_type",
    )
    return await transcribe(llm, audio, params.language)
