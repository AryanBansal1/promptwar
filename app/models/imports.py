"""Schemas for importing income & expenses from a screenshot, PDF or pasted SMS text."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.models.common import Language, PastDate, Platform, StrictModel
from app.models.ledger import Amount, Category, ClientToday, EntryKind

MAX_IMPORT_ROWS = 100
LOW_CONFIDENCE = 0.6


class Direction(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"


class ImportParams(StrictModel):
    """Query parameters for a raw file upload."""

    language: Language = Language.EN
    today: ClientToday


class TextImportRequest(StrictModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=5000)]
    language: Language = Language.EN
    today: ClientToday


class RawImportRow(BaseModel):
    """Gemini's structured output for one row. Loosely typed so each row is validated on its own."""

    date: str | None = None
    amount: float | None = None
    direction: Direction
    counterparty: str | None = None
    suggested_platform: str | None = None
    suggested_category: str | None = None
    confidence: float | None = None


class RawImportOutput(BaseModel):
    transactions: list[RawImportRow]


class ImportRow(BaseModel):
    """A validated, proposed entry. Nothing is saved until the worker confirms it."""

    date: PastDate | None
    amount: Amount
    direction: Direction
    kind: EntryKind
    category: Category
    platform: Platform | None
    counterparty: Annotated[str, Field(max_length=60)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    low_confidence: bool
    needs_date: bool


class ImportResponse(BaseModel):
    rows: list[ImportRow]
    found: int
    dropped: int
