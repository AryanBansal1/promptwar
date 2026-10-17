"""Document Help: checklists from curated data, plus grounded Gemini explanations and Q&A."""

from __future__ import annotations

from collections.abc import Sequence

from app.cache import TTLCache
from app.errors import AppError
from app.llm.client import LLMClient
from app.llm.prompts import documents_ask_request, documents_explain_request
from app.models.common import ChatTurn, Language
from app.models.documents import Checklist, TargetKind
from app.services.ai import generate_capped_text
from app.services.data_store import DataStore


def build_checklist(store: DataStore, kind: TargetKind, target_id: str) -> Checklist:
    """Resolve a goal or a scheme into a checklist of documents and steps."""
    if kind is TargetKind.GOAL:
        goal = store.goals.get(target_id)
        if goal is None:
            raise AppError("not_found", "Unknown document goal.", 404)
        return Checklist(
            kind=kind,
            id=goal.id,
            title=goal.name,
            items=[store.documents[d] for d in goal.documents],
            steps=goal.steps,
            official_url=goal.official_url,
        )
    scheme = store.schemes.get(target_id)
    if scheme is None:
        raise AppError("not_found", "Unknown scheme.", 404)
    return Checklist(
        kind=kind,
        id=scheme.id,
        title=scheme.name,
        items=[store.documents[d] for d in scheme.required_documents],
        steps=scheme.how_to_apply,
        official_url=scheme.official_url,
    )


def checklist_record(checklist: Checklist) -> dict[str, object]:
    """Fields sent to Gemini as grounding."""
    return {
        "goal": checklist.title,
        "documents": [{"name": i.name, "note": i.description} for i in checklist.items],
        "official_steps": checklist.steps,
    }


async def explain_checklist(llm: LLMClient, checklist: Checklist, language: Language, cache: TTLCache[str]) -> str:
    """Step-by-step explanation. Cached because the input is fully curated."""
    request = documents_explain_request(checklist_record(checklist), language)
    key = (language.value, checklist.kind.value, checklist.id)
    return await generate_capped_text(llm, "documents_explain", request, cache, cache_key=key)


async def answer_question(
    llm: LLMClient,
    checklist: Checklist,
    question: str,
    history: Sequence[ChatTurn],
    language: Language,
) -> str:
    """Follow-up Q&A about one checklist (not cached: free-form user input)."""
    request = documents_ask_request(checklist_record(checklist), question, history, language)
    return await generate_capped_text(llm, "documents_ask", request)
