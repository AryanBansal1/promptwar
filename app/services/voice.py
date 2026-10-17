"""Speech-to-text fallback with Gemini audio input. Audio and transcripts stay in memory and are never logged."""

from __future__ import annotations

from app.errors import AppError
from app.llm.client import Attachment, LLMClient
from app.llm.prompts import SPECS, transcribe_request
from app.models.common import Language
from app.models.voice import TranscriptOutput, TranscriptResponse
from app.security import cap_text, clean_text
from app.services.uploads import ValidUpload


async def transcribe(llm: LLMClient, audio: ValidUpload, language: Language) -> TranscriptResponse:
    """Return what was said, in the selected language's script. No speech is an honest error, not empty text."""
    request = transcribe_request(language, Attachment(audio.data, audio.mime_type))
    output = await llm.generate_json(request, TranscriptOutput)
    text = cap_text(clean_text(output.transcript), SPECS["transcribe"].max_chars)
    if not output.has_speech or not text:
        raise AppError("no_speech", "No speech was heard in the recording.", 422)
    return TranscriptResponse(text=text)
