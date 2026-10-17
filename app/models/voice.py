"""Schemas for the voice-input fallback (used when the browser has no Web Speech API)."""

from __future__ import annotations

from pydantic import BaseModel

from app.models.common import Language, StrictModel


class TranscribeParams(StrictModel):
    """Query parameters for a raw audio upload."""

    language: Language = Language.EN


class TranscriptOutput(BaseModel):
    """Structured Gemini output."""

    transcript: str
    has_speech: bool


class TranscriptResponse(BaseModel):
    text: str
