"""Deterministic Income & Expenses statistics. Plain Python, never the LLM."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TypeVar

from app.llm.client import LLMClient
from app.llm.prompts import WEEKDAYS, advice_request
from app.models.common import PLATFORM_NAMES, WORK_TYPE_NAMES, Language, Platform
from app.models.ledger import (
    AdviceTips,
    Category,
    EntryKind,
    LedgerEntry,
    LedgerSummary,
    PeriodTotal,
    PlatformStat,
    WorkTypeStat,
)
from app.services.ai import cap_items

_CENT = Decimal("0.01")
_ZERO = Decimal("0")
KeyT = TypeVar("KeyT", bound=Hashable)


def _d(value: float) -> Decimal:
    """Convert a float from JSON to an exact Decimal via its string form."""
    return Decimal(str(value))


def money(value: Decimal) -> float:
    """Round half-up to paise."""
    return float(value.quantize(_CENT, rounding=ROUND_HALF_UP))


def _per_hour(net: Decimal, hours: Decimal) -> float | None:
    return money(net / hours) if hours > 0 else None


@dataclass
class _Bucket:
    income: Decimal = _ZERO
    expenses: Decimal = _ZERO
    hours: Decimal = _ZERO
    work_days: set[date] = field(default_factory=set)

    @property
    def net(self) -> Decimal:
        return self.income - self.expenses

    def add(self, entry: LedgerEntry) -> None:
        if entry.kind is EntryKind.INCOME:
            self.income += _d(entry.amount)
            self.hours += _d(entry.hours)
            self.work_days.add(entry.date)
        else:
            self.expenses += _d(entry.amount)

    def totals(self) -> dict[str, float]:
        return {
            "income": money(self.income),
            "expenses": money(self.expenses),
            "net": money(self.net),
            "hours": money(self.hours),
        }


def week_key(day: date) -> str:
    """ISO week label such as ``2026-W07``."""
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def month_key(day: date) -> str:
    """Month label such as ``2026-10``."""
    return f"{day.year}-{day.month:02d}"


def _group(entries: Iterable[LedgerEntry], key: Callable[[LedgerEntry], KeyT]) -> dict[KeyT, _Bucket]:
    buckets: dict[KeyT, _Bucket] = defaultdict(_Bucket)
    for entry in entries:
        buckets[key(entry)].add(entry)
    return buckets


def _periods(buckets: dict[str, _Bucket]) -> list[PeriodTotal]:
    return [
        PeriodTotal(period=k, days=len(b.work_days), **b.totals()) for k, b in sorted(buckets.items(), reverse=True)
    ]


def _best_platform(stats: dict[Platform | None, _Bucket]) -> Platform | None:
    """Highest net per hour among platforms with income and hours, else highest net."""
    earning = {p: b for p, b in stats.items() if p is not None and b.income > 0}
    if not earning:
        return None
    timed = {p: b.net / b.hours for p, b in earning.items() if b.hours > 0}
    if timed:
        return max(timed, key=lambda p: (timed[p], p.value))
    return max(earning, key=lambda p: (earning[p].net, p.value))


def _best_weekday(entries: Iterable[LedgerEntry]) -> int | None:
    """Weekday (0=Monday) with the highest average net per day that had income."""
    days = _group(entries, lambda e: e.date)
    nets_by_weekday: dict[int, list[Decimal]] = defaultdict(list)
    for day, bucket in days.items():
        if bucket.income > 0:
            nets_by_weekday[day.weekday()].append(bucket.net)
    if not nets_by_weekday:
        return None
    averages = {wd: sum(nets, _ZERO) / len(nets) for wd, nets in nets_by_weekday.items()}
    return max(averages, key=lambda wd: (averages[wd], -wd))


def _by_category(entries: Iterable[LedgerEntry], kind: EntryKind) -> dict[Category, float]:
    totals: dict[Category, Decimal] = defaultdict(lambda: _ZERO)
    for entry in entries:
        if entry.kind is kind:
            totals[entry.category] += _d(entry.amount)
    return {c: money(v) for c, v in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)}


def compute_summary(entries: list[LedgerEntry], today: date | None = None) -> LedgerSummary:
    """Totals, per-hour rates, weekly/monthly groups, platform and work-type breakdowns."""
    total = _Bucket()
    for entry in entries:
        total.add(entry)
    platforms = _group(entries, lambda e: e.platform)
    work_types = _group(entries, lambda e: e.work_type)

    return LedgerSummary(
        entry_count=len(entries),
        income_count=sum(e.kind is EntryKind.INCOME for e in entries),
        expense_count=sum(e.kind is EntryKind.EXPENSE for e in entries),
        total_income=money(total.income),
        total_expenses=money(total.expenses),
        total_net=money(total.net),
        total_hours=money(total.hours),
        net_per_hour=_per_hour(total.net, total.hours),
        days_worked=len(total.work_days),
        income_by_category=_by_category(entries, EntryKind.INCOME),
        expense_by_category=_by_category(entries, EntryKind.EXPENSE),
        weekly=_periods(_group(entries, lambda e: week_key(e.date))),
        monthly=_periods(_group(entries, lambda e: month_key(e.date))),
        platforms=[
            PlatformStat(platform=p, net_per_hour=_per_hour(b.net, b.hours), **b.totals())
            for p, b in sorted(platforms.items(), key=lambda kv: kv[1].net, reverse=True)
        ],
        work_types=[
            WorkTypeStat(work_type=w, net_per_hour=_per_hour(b.net, b.hours), **b.totals())
            for w, b in sorted(work_types.items(), key=lambda kv: kv[1].net, reverse=True)
        ],
        best_platform=_best_platform(platforms),
        best_weekday=_best_weekday(entries),
        today=day_total(entries, today) if today else None,
    )


def day_total(entries: Iterable[LedgerEntry], day: date) -> PeriodTotal:
    """Income, expenses and net for one calendar day."""
    bucket = _Bucket()
    for entry in entries:
        if entry.date == day:
            bucket.add(entry)
    return PeriodTotal(period=day.isoformat(), days=len(bucket.work_days), **bucket.totals())


def summary_for_prompt(summary: LedgerSummary) -> dict[str, object]:
    """Compact, PII-free view of the summary sent to the LLM (no ids, dates or notes)."""
    return {
        "income_rs": summary.total_income,
        "expenses_rs": summary.total_expenses,
        "net_rs": summary.total_net,
        "hours": summary.total_hours,
        "net_per_hour_rs": summary.net_per_hour,
        "days_worked": summary.days_worked,
        "income_by_category_rs": {c.value: v for c, v in summary.income_by_category.items()},
        "expenses_by_category_rs": {c.value: v for c, v in summary.expense_by_category.items()},
        "best_platform": PLATFORM_NAMES[summary.best_platform] if summary.best_platform else None,
        "best_weekday": WEEKDAYS[summary.best_weekday] if summary.best_weekday is not None else None,
        "platforms": [
            {
                "name": PLATFORM_NAMES[p.platform] if p.platform else "Not linked to a platform",
                "net_rs": p.net,
                "hours": p.hours,
                "net_per_hour_rs": p.net_per_hour,
            }
            for p in summary.platforms
        ],
        "work_types": [
            {"name": WORK_TYPE_NAMES[w.work_type] if w.work_type else "Not set", "net_rs": w.net}
            for w in summary.work_types
        ],
        "recent_weeks": [
            w.model_dump(include={"period", "income", "expenses", "net", "hours"}) for w in summary.weekly[:4]
        ],
    }


async def generate_advice(llm: LLMClient, summary: LedgerSummary, language: Language) -> list[str]:
    """Ask Gemini for exactly three short tips grounded on the computed summary."""
    output = await llm.generate_json(advice_request(summary_for_prompt(summary), language), AdviceTips)
    return cap_items(output.tips, "ledger_advice", 3)
