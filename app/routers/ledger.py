"""Income & Expenses endpoints (stateless: entries live in the worker's browser)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.deps import AI_RATE_LIMIT, LLMDep
from app.models.imports import ImportParams, ImportResponse, TextImportRequest
from app.models.ledger import AdviceRequest, AdviceResponse, LedgerSummary, SummaryRequest
from app.services.imports import import_from_file, import_from_text
from app.services.ledger import compute_summary, generate_advice
from app.services.uploads import IMPORT_TYPES, MAX_IMPORT_BYTES, validate_upload

router = APIRouter(prefix="/api/ledger", tags=["ledger"])

IMPORT_FILE_PATH = "/api/ledger/import/file"


@router.post("/summary")
async def summary(payload: SummaryRequest) -> LedgerSummary:
    """Compute totals, per-hour rates and breakdowns in plain Python."""
    return compute_summary(payload.entries, payload.today)


@router.post("/advice", dependencies=AI_RATE_LIMIT)
async def advice(payload: AdviceRequest, llm: LLMDep) -> AdviceResponse:
    """Three Gemini tips based only on the computed (PII-free) summary."""
    result = compute_summary(payload.entries, payload.today)
    tips = await generate_advice(llm, result, payload.language)
    return AdviceResponse(tips=tips, summary=result)


@router.post("/import/file", dependencies=AI_RATE_LIMIT)
async def import_file(request: Request, params: Annotated[ImportParams, Query()], llm: LLMDep) -> ImportResponse:
    """Read transactions from a screenshot or PDF sent as the raw request body (kept in memory only)."""
    upload = validate_upload(request.headers.get("content-type"), await request.body(), IMPORT_TYPES, MAX_IMPORT_BYTES)
    return await import_from_file(llm, upload, params.today, params.language)


@router.post("/import/text", dependencies=AI_RATE_LIMIT)
async def import_text(payload: TextImportRequest, llm: LLMDep) -> ImportResponse:
    """Read transactions from pasted SMS or statement text."""
    return await import_from_text(llm, payload.text, payload.today, payload.language)
