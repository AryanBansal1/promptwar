"""Deterministic Income & Expenses statistics. Plain Python, never the LLM."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app.llm.client import LLMClient
from app.llm.prompts import WEEKDAYS, advice_request
from app.models.common import PLATFORM_NAMES, WORK_TYPE_NAMES, Language, Platform, WorkType
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

    def add(self, income: bool, amount: Decimal, hours: Decimal, day: date) -> None:
        if income:
            self.income += amount
            self.hours += hours
            self.work_days.add(day)
        else:
            self.expenses += amount

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


@dataclass
class _Totals:
    """Every aggregate the summary needs, filled in a single pass over the entries."""

    total: _Bucket = field(default_factory=_Bucket)
    days: defaultdict[date, _Bucket] = field(default_factory=lambda: defaultdict(_Bucket))
    weeks: defaultdict[str, _Bucket] = field(default_factory=lambda: defaultdict(_Bucket))
    months: defaultdict[str, _Bucket] = field(default_factory=lambda: defaultdict(_Bucket))
    platforms: defaultdict[Platform | None, _Bucket] = field(default_factory=lambda: defaultdict(_Bucket))
    work_types: defaultdict[WorkType | None, _Bucket] = field(default_factory=lambda: defaultdict(_Bucket))
    categories: dict[EntryKind, defaultdict[Category, Decimal]] = field(
        default_factory=lambda: {k: defaultdict(lambda: _ZERO) for k in EntryKind}
    )
    income_count: int = 0

    @classmethod
    def of(cls, entries: Iterable[LedgerEntry]) -> _Totals:
        acc = cls()
        week_of: dict[date, str] = {}  # many entries share a date: compute its labels once
        for entry in entries:
            income = entry.kind is EntryKind.INCOME
            amount, hours, day = _d(entry.amount), _d(entry.hours), entry.date
            week = week_of.get(day) or week_of.setdefault(day, week_key(day))
            for bucket in (
                acc.total,
                acc.days[day],
                acc.weeks[week],
                acc.months[month_key(day)],
                acc.platforms[entry.platform],
                acc.work_types[entry.work_type],
            ):
                bucket.add(income, amount, hours, day)
            acc.categories[entry.kind][entry.category] += amount
            acc.income_count += income
        return acc


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


def _best_weekday(days: dict[date, _Bucket]) -> int | None:
    """Weekday (0=Monday) with the highest average net per day that had income."""
    nets_by_weekday: dict[int, list[Decimal]] = defaultdict(list)
    for day, bucket in days.items():
        if bucket.income > 0:
            nets_by_weekday[day.weekday()].append(bucket.net)
    if not nets_by_weekday:
        return None
    averages = {wd: sum(nets, _ZERO) / len(nets) for wd, nets in nets_by_weekday.items()}
    return max(averages, key=lambda wd: (averages[wd], -wd))


def _by_category(totals: dict[Category, Decimal]) -> dict[Category, float]:
    return {c: money(v) for c, v in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)}


def _day_period(day: date, bucket: _Bucket | None) -> PeriodTotal:
    bucket = bucket or _Bucket()
    return PeriodTotal(period=day.isoformat(), days=len(bucket.work_days), **bucket.totals())


def compute_summary(entries: list[LedgerEntry], today: date | None = None) -> LedgerSummary:
    """Totals, per-hour rates, weekly/monthly groups, platform and work-type breakdowns (one pass)."""
    acc = _Totals.of(entries)
    total = acc.total
    return LedgerSummary(
        entry_count=len(entries),
        income_count=acc.income_count,
        expense_count=len(entries) - acc.income_count,
        total_income=money(total.income),
        total_expenses=money(total.expenses),
        total_net=money(total.net),
        total_hours=money(total.hours),
        net_per_hour=_per_hour(total.net, total.hours),
        days_worked=len(total.work_days),
        income_by_category=_by_category(acc.categories[EntryKind.INCOME]),
        expense_by_category=_by_category(acc.categories[EntryKind.EXPENSE]),
        weekly=_periods(acc.weeks),
        monthly=_periods(acc.months),
        platforms=[
            PlatformStat(platform=p, net_per_hour=_per_hour(b.net, b.hours), **b.totals())
            for p, b in sorted(acc.platforms.items(), key=lambda kv: kv[1].net, reverse=True)
        ],
        work_types=[
            WorkTypeStat(work_type=w, net_per_hour=_per_hour(b.net, b.hours), **b.totals())
            for w, b in sorted(acc.work_types.items(), key=lambda kv: kv[1].net, reverse=True)
        ],
        best_platform=_best_platform(acc.platforms),
        best_weekday=_best_weekday(acc.days),
        today=_day_period(today, acc.days.get(today)) if today else None,
    )


def day_total(entries: Iterable[LedgerEntry], day: date) -> PeriodTotal:
    """Income, expenses and net for one calendar day."""
    bucket = _Bucket()
    for entry in entries:
        if entry.date == day:
            bucket.add(entry.kind is EntryKind.INCOME, _d(entry.amount), _d(entry.hours), day)
    return _day_period(day, bucket)


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
