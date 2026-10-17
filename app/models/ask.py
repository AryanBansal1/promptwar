"""Schemas for the home-screen 'Ask Saathi' router, including logging income/expenses from speech or text."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from app.models.common import IssueType, Language, PastDate, Platform, Question, StrictModel, WorkTypes
from app.models.ledger import Amount, Category, ClientToday, EntryKind


class Intent(StrEnum):
    LEDGER = "ledger"
    LOG_TRANSACTION = "log_transaction"
    SAFETY = "safety"
    SCHEMES = "schemes"
    DOCUMENTS = "documents"
    GRIEVANCE = "grievance"
    GENERAL = "general"


class DocumentGoalId(StrEnum):
    ESHRAM = "eshram"
    BANK_ACCOUNT = "bank_account"
    DRIVING_LICENCE = "driving_licence"
    AYUSHMAN_CARD = "ayushman_card"
    PAN = "pan"
    RATION_CARD = "ration_card"


class RelativeDay(StrEnum):
    """What the user said about *when*. The calendar date is computed in code, never by the LLM."""

    TODAY = "today"
    YESTERDAY = "yesterday"
    DAY_BEFORE_YESTERDAY = "day_before_yesterday"
    DATE = "date"  # the user gave an explicit calendar date
    UNSPECIFIED = "unspecified"


class AskRequest(StrictModel):
    language: Language = Language.EN
    message: Question
    work_types: WorkTypes = Field(default_factory=list)  # from the worker's profile; no PII
    today: ClientToday | None = None  # the worker's local date, for resolving "aaj" / "kal"


class Prefill(BaseModel):
    """Context Gemini extracts so the target feature opens pre-filled."""

    issue_type: IssueType | None = None
    platform: Platform | None = None
    amount: float | None = None
    document_goal: DocumentGoalId | None = None
    question: str | None = None


class TransactionSchema(BaseModel):
    """What Gemini is constrained to produce for each money item (enums enforced by the API)."""

    type: EntryKind
    amount: float
    platform: Platform | None = None
    category: Category | None = None
    day: RelativeDay
    date: str | None = None
    confidence: float


class IntentSchema(BaseModel):
    """Strict structured-output schema sent to Gemini."""

    intent: Intent
    answer: str
    prefill: Prefill
    transactions: list[TransactionSchema]


class RawTransaction(BaseModel):
    """Lenient parse of one item, so a bad item is dropped on its own instead of failing the whole reply."""

    type: str | None = None
    amount: float | None = None
    platform: str | None = None
    category: str | None = None
    day: str | None = None
    date: str | None = None
    confidence: float | None = None


class IntentOutput(BaseModel):
    """Parsed Gemini output: the intent enum is strict, transaction items are validated one by one."""

    intent: Intent
    answer: str
    prefill: Prefill
    transactions: list[RawTransaction] = Field(default_factory=list)


class ProposedTransaction(BaseModel):
    """A validated income/expense item awaiting the worker's confirmation. Never saved by the server."""

    kind: EntryKind
    amount: Amount
    category: Category
    platform: Platform | None
    date: PastDate
    confidence: Annotated[float, Field(ge=0, le=1)]
    low_confidence: bool


class AskResponse(BaseModel):
    intent: Intent
    route: str
    answer: str
    prefill: Prefill
    transactions: list[ProposedTransaction] = Field(default_factory=list)
    dropped: int = 0
