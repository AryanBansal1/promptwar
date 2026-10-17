"""Rights & Welfare Schemes endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import AI_RATE_LIMIT, CacheDep, LLMDep, StoreDep
from app.errors import AppError
from app.models.schemes import EligibilityProfile, ExplainRequest, ExplainResponse, MatchResponse, Scheme
from app.services.schemes import explain_schemes, match_schemes

router = APIRouter(prefix="/api/schemes", tags=["schemes"])


@router.get("")
async def list_schemes(store: StoreDep) -> list[Scheme]:
    """All curated schemes."""
    return list(store.schemes.values())


@router.post("/match")
async def match(payload: EligibilityProfile, store: StoreDep) -> MatchResponse:
    """Rule-based eligibility filter (no AI)."""
    matches, needs_bank = match_schemes(store.schemes, payload)
    return MatchResponse(matches=matches, unmatched_needs_bank=needs_bank)


@router.post("/explain", dependencies=AI_RATE_LIMIT)
async def explain(payload: ExplainRequest, store: StoreDep, llm: LLMDep, cache: CacheDep) -> ExplainResponse:
    """Re-run matching server-side, then let Gemini explain only the matched records."""
    profile = EligibilityProfile.model_validate(payload.model_dump(exclude={"language"}))
    matches, _ = match_schemes(store.schemes, profile)
    if not matches:
        raise AppError("no_matches", "No schemes matched, so there is nothing to explain.", 404)
    text = await explain_schemes(llm, matches, payload.language, cache)
    return ExplainResponse(scheme_ids=[m.scheme.id for m in matches], explanation=text)
