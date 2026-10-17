"""Document Help endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.deps import AI_RATE_LIMIT, CacheDep, LLMDep, StoreDep
from app.models.documents import AskDocsRequest, Checklist, ExplainDocsRequest, RecordId, TargetKind, TextAnswer
from app.services.documents import answer_question, build_checklist, explain_checklist

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("/goals")
async def goals(store: StoreDep) -> list[dict[str, str]]:
    """Available document goals."""
    return [{"id": g.id, "name": g.name} for g in store.goals.values()]


@router.get("/checklist/{kind}/{target_id}")
async def checklist(kind: TargetKind, target_id: RecordId, store: StoreDep) -> Checklist:
    """Checklist for a goal or for a scheme's required documents."""
    return build_checklist(store, kind, target_id)


@router.post("/explain", dependencies=AI_RATE_LIMIT)
async def explain(payload: ExplainDocsRequest, store: StoreDep, llm: LLMDep, cache: CacheDep) -> TextAnswer:
    """Gemini step-by-step explanation grounded on the checklist."""
    target = build_checklist(store, payload.kind, payload.id)
    return TextAnswer(text=await explain_checklist(llm, target, payload.language, cache))


@router.post("/ask", dependencies=AI_RATE_LIMIT)
async def ask(payload: AskDocsRequest, store: StoreDep, llm: LLMDep) -> TextAnswer:
    """Follow-up question about a checklist."""
    target = build_checklist(store, payload.kind, payload.id)
    text = await answer_question(llm, target, payload.question, payload.history, payload.language)
    return TextAnswer(text=text)
