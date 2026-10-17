"""Request/response schemas for Income & Expenses (a ledger of individual transactions)."""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, StringConstraints, model_validator

from app.models.common import Language, PastDate, Platform, StrictModel, WorkType, today_ist

EntryId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Amount = Annotated[float, Field(gt=0, le=1_000_000)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]


class EntryKind(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class Category(StrEnum):
    """Income and expense categories. ``other`` is valid for both kinds."""

    EARNINGS = "earnings"
    TIPS = "tips"
    INCENTIVE = "incentive"
    FUEL = "fuel"
    COMMISSION = "commission"
    MAINTENANCE = "maintenance"
    PHONE = "phone"
    FOOD = "food"
    OTHER = "other"


INCOME_CATEGORIES: frozenset[Category] = frozenset(
    {Category.EARNINGS, Category.TIPS, Category.INCENTIVE, Category.OTHER}
)
EXPENSE_CATEGORIES: frozenset[Category] = frozenset(
    {Category.FUEL, Category.COMMISSION, Category.MAINTENANCE, Category.PHONE, Category.FOOD, Category.OTHER}
)


def categories_for(kind: EntryKind) -> frozenset[Category]:
    """Categories allowed for an entry kind."""
    return INCOME_CATEGORIES if kind is EntryKind.INCOME else EXPENSE_CATEGORIES


class EntrySource(StrEnum):
    """Where an entry came from, so imported/voice entries stay labelled."""

    MANUAL = "manual"
    IMPORT = "import"
    VOICE = "voice"


class LedgerEntry(StrictModel):
    """One income or expense. Contains no personal data except an optional short note."""

    id: EntryId
    date: PastDate
    kind: EntryKind
    amount: Amount
    category: Category
    platform: Platform | None = None
    work_type: WorkType | None = None
    hours: Annotated[float, Field(ge=0, le=24)] = 0
    source: EntrySource = EntrySource.MANUAL
    note: Note = ""

    @model_validator(mode="after")
    def _consistent(self) -> LedgerEntry:
        if self.category not in categories_for(self.kind):
            raise ValueError("category does not match the entry type")  # no user values echoed back
        if self.kind is EntryKind.EXPENSE and self.hours:
            raise ValueError("hours are only recorded on income entries")
        return self


def _client_today(value: date) -> date:
    # The browser's local date may be one day ahead of India time near midnight in other zones.
    if value > today_ist() + timedelta(days=1):
        raise ValueError("today is in the future")
    return value


ClientToday = Annotated[date, AfterValidator(_client_today)]


class SummaryRequest(StrictModel):
    """All entries kept in the worker's browser, plus their local date for 'today' totals."""

    entries: Annotated[list[LedgerEntry], Field(max_length=5000)]
    today: ClientToday | None = None


class AdviceRequest(SummaryRequest):
    language: Language = Language.EN


class Totals(BaseModel):
    income: float
    expenses: float
    net: float
    hours: float


class PeriodTotal(Totals):
    """Totals for one ISO week (``2026-W42``), month (``2026-10``) or day."""

    period: str
    days: int


class PlatformStat(Totals):
    platform: Platform | None
    net_per_hour: float | None


class WorkTypeStat(Totals):
    work_type: WorkType | None
    net_per_hour: float | None


class LedgerSummary(BaseModel):
    """Deterministic statistics computed in Python (never by the LLM)."""

    entry_count: int
    income_count: int
    expense_count: int
    total_income: float
    total_expenses: float
    total_net: float
    total_hours: float
    net_per_hour: float | None
    days_worked: int
    income_by_category: dict[Category, float]
    expense_by_category: dict[Category, float]
    weekly: list[PeriodTotal]
    monthly: list[PeriodTotal]
    platforms: list[PlatformStat]
    work_types: list[WorkTypeStat]
    best_platform: Platform | None
    best_weekday: Annotated[int, Field(ge=0, le=6)] | None = None
    today: PeriodTotal | None = None


class AdviceTips(BaseModel):
    """Structured Gemini output for advice."""

    tips: list[str]


class AdviceResponse(BaseModel):
    tips: list[str]
    summary: LedgerSummary
