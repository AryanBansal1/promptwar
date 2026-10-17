"""Safety Assistant endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import AI_RATE_LIMIT, LLMDep
from app.models.safety import SafetyChatRequest, SafetyChatResponse
from app.services.safety import EMERGENCY_CONTACTS, safety_reply

router = APIRouter(prefix="/api/safety", tags=["safety"])


@router.get("/contacts")
async def contacts() -> list[dict[str, str]]:
    """Hardcoded national helplines (never LLM-generated)."""
    return [{"key": c.key, "number": c.number} for c in EMERGENCY_CONTACTS]


@router.post("/chat", dependencies=AI_RATE_LIMIT)
async def chat(payload: SafetyChatRequest, llm: LLMDep) -> SafetyChatResponse:
    """Gemini safety guidance with an optional 'Draft a complaint' suggestion."""
    return await safety_reply(llm, payload)
