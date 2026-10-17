"""Grievance / Dispute Drafter endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import AI_RATE_LIMIT, LLMDep, StoreDep
from app.models.grievance import GrievanceRequest, GrievanceResponse
from app.services.grievance import draft_grievance

router = APIRouter(prefix="/api/grievance", tags=["grievance"])


@router.post("/draft", dependencies=AI_RATE_LIMIT)
async def draft(payload: GrievanceRequest, store: StoreDep, llm: LLMDep) -> GrievanceResponse:
    """Gemini drafts the letters; contacts come from escalation.json."""
    return await draft_grievance(llm, payload, store.escalation)
