"""Home-screen 'Ask Saathi' endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import AI_RATE_LIMIT, LLMDep
from app.models.ask import AskRequest, AskResponse
from app.services.router_intent import route_question

router = APIRouter(prefix="/api", tags=["ask"])


@router.post("/ask", dependencies=AI_RATE_LIMIT)
async def ask(payload: AskRequest, llm: LLMDep) -> AskResponse:
    """Classify intent with structured output and route the user."""
    return await route_question(llm, payload)
