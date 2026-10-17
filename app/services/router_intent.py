"""'Ask Saathi': Gemini classifies intent into a fixed enum and we map it to a route.

For ``log_transaction`` Gemini lists each income/expense it heard. Each item is validated on its own
and invalid items are dropped and counted. Relative dates ("aaj", "kal") are resolved here, in code,
from the worker's local date. Nothing is saved on the server: the browser asks the worker to confirm.
"""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import ValidationError

from app.llm.client import LLMClient, ai_empty
from app.llm.prompts import SPECS, intent_request
from app.models.ask import (
    AskRequest,
    AskResponse,
    Intent,
    IntentOutput,
    Prefill,
    ProposedTransaction,
    RawTransaction,
    RelativeDay,
)
from app.models.common import Platform, today_ist
from app.models.imports import LOW_CONFIDENCE
from app.models.ledger import Category, EntryKind, categories_for
from app.security import cap_text, clean_text, strip_phone_numbers

ROUTES: dict[Intent, str] = {
    Intent.LEDGER: "#/ledger",
    Intent.LOG_TRANSACTION: "#/",  # stays on Home and shows a confirmation card
    Intent.SAFETY: "#/safety",
    Intent.SCHEMES: "#/schemes",
    Intent.DOCUMENTS: "#/documents",
    Intent.GRIEVANCE: "#/grievance",
    Intent.GENERAL: "#/",
}

_MAX_PREFILL_AMOUNT = 10_000_000
_DAY_OFFSETS = {RelativeDay.TODAY: 0, RelativeDay.YESTERDAY: 1, RelativeDay.DAY_BEFORE_YESTERDAY: 2}


def sanitize_prefill(prefill: Prefill, intent: Intent) -> Prefill:
    """Keep only prefill fields relevant to the routed feature and within valid ranges."""
    amount = prefill.amount if prefill.amount is not None and 0 <= prefill.amount <= _MAX_PREFILL_AMOUNT else None
    question = cap_text(clean_text(prefill.question), 300) if prefill.question else None
    if intent is Intent.GRIEVANCE:
        return Prefill(issue_type=prefill.issue_type, platform=prefill.platform, amount=amount)
    if intent is Intent.DOCUMENTS:
        return Prefill(document_goal=prefill.document_goal, question=question)
    if intent is Intent.SAFETY:
        return Prefill(question=question)
    if intent is Intent.LEDGER:
        return Prefill(platform=prefill.platform, amount=amount)
    return Prefill()


def resolve_date(day: str | None, explicit: str | None, today: date) -> date | None:
    """Turn what the user said about *when* into a calendar date. ``None`` means invalid."""
    try:
        relative = RelativeDay(day) if day else RelativeDay.UNSPECIFIED
    except ValueError:
        return None
    if relative is RelativeDay.DATE:
        try:
            return date.fromisoformat(explicit or "")
        except ValueError:
            return None
    return today - timedelta(days=_DAY_OFFSETS.get(relative, 0))  # unspecified means today


def to_proposed(raw: RawTransaction, today: date) -> ProposedTransaction | None:
    """Validate one item against the fixed enums and limits. ``None`` means it is dropped."""
    try:
        kind = EntryKind(raw.type or "")
        platform = Platform(raw.platform) if raw.platform else None
        category = Category(raw.category) if raw.category else None
    except ValueError:
        return None
    if category is None:
        category = Category.EARNINGS if kind is EntryKind.INCOME else Category.OTHER
    if category not in categories_for(kind):
        return None
    when = resolve_date(raw.day, raw.date, today)
    confidence = raw.confidence if raw.confidence is not None else 0.0
    try:  # None/zero/oversized amounts and missing or future dates are rejected here
        return ProposedTransaction.model_validate(
            {
                "kind": kind,
                "amount": raw.amount,
                "category": category,
                "platform": platform,
                "date": when,
                "confidence": confidence,
                "low_confidence": confidence < LOW_CONFIDENCE,
            }
        )
    except ValidationError:
        return None


def validate_transactions(raws: list[RawTransaction], today: date) -> tuple[list[ProposedTransaction], int]:
    """Valid items plus the number dropped."""
    items = [item for item in (to_proposed(raw, today) for raw in raws) if item is not None]
    return items, len(raws) - len(items)


async def route_question(llm: LLMClient, payload: AskRequest) -> AskResponse:
    """Classify the message and return the route, a short answer, prefill and any proposed entries."""
    request = intent_request(payload.message, payload.language, payload.work_types)
    output = await llm.generate_json(request, IntentOutput)
    answer = cap_text(strip_phone_numbers(output.answer), SPECS["intent"].max_chars)
    if not answer:
        raise ai_empty()
    transactions: list[ProposedTransaction] = []
    dropped = 0
    if output.intent is Intent.LOG_TRANSACTION:
        transactions, dropped = validate_transactions(output.transactions, payload.today or today_ist())
    return AskResponse(
        intent=output.intent,
        route=ROUTES[output.intent],
        answer=answer,
        prefill=sanitize_prefill(output.prefill, output.intent),
        transactions=transactions,
        dropped=dropped,
    )
