"""Income & Expenses math (pure Python) and the ledger endpoints."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models.common import Platform, WorkType, today_ist
from app.models.ledger import Category, LedgerEntry, SummaryRequest
from app.services.ledger import compute_summary, day_total, month_key, summary_for_prompt, week_key
from tests.conftest import entry as base_entry
from tests.fakes import FakeLLMClient


def entry(**overrides: object) -> dict[str, object]:
    """A valid income entry payload with a work type."""
    return base_entry(**{"work_type": "delivery", **overrides})


def expense(**overrides: object) -> dict[str, object]:
    """A valid standalone expense payload (no income on the same entry)."""
    base: dict[str, object] = {"id": "x1", "date": "2026-10-12", "kind": "expense", "amount": 200, "category": "fuel"}
    base.update(overrides)
    return base


def make(payload: dict[str, object]) -> LedgerEntry:
    return LedgerEntry.model_validate(payload)


def test_empty_input_gives_zero_summary() -> None:
    s = compute_summary([])
    assert (s.entry_count, s.total_income, s.total_expenses, s.total_net) == (0, 0, 0, 0)
    assert s.net_per_hour is None and s.best_platform is None and s.best_weekday is None
    assert s.weekly == [] and s.platforms == [] and s.today is None


def test_standalone_expense_without_income() -> None:
    s = compute_summary([make(expense())])
    assert s.total_income == 0
    assert s.total_expenses == 200
    assert s.total_net == -200
    assert s.days_worked == 0
    assert s.best_platform is None
    assert s.expense_by_category == {Category.FUEL: 200}
    assert s.platforms[0].platform is None  # not linked to any platform


def test_totals_per_hour_and_categories() -> None:
    s = compute_summary(
        [
            make(entry()),
            make(expense(id="x1", amount=150, platform="swiggy")),
            make(expense(id="x2", amount=100, category="commission", platform="swiggy")),
            make(entry(id="e2", date="2026-10-13", platform="uber", work_type="ride_hailing", hours=5, amount=900)),
            make(entry(id="e3", date="2026-10-13", platform="uber", category="tips", hours=0, amount=50)),
        ]
    )
    assert (s.total_income, s.total_expenses, s.total_net, s.total_hours) == (2150, 250, 1900, 13)
    assert s.net_per_hour == pytest.approx(146.15)
    assert s.income_count == 3 and s.expense_count == 2
    assert s.income_by_category == {Category.EARNINGS: 2100, Category.TIPS: 50}
    assert s.days_worked == 2


def test_best_platform_uses_net_per_hour_after_expenses() -> None:
    s = compute_summary(
        [
            make(entry()),  # swiggy 1200 / 8h
            make(expense(amount=600, category="commission", platform="swiggy")),  # swiggy net 600 / 8h = 75
            make(entry(id="e2", platform="uber", work_type="ride_hailing", hours=5, amount=500)),  # 100/h
        ]
    )
    assert s.best_platform is Platform.UBER


def test_zero_hours_falls_back_to_net() -> None:
    s = compute_summary([make(entry(hours=0)), make(entry(id="e2", platform="ola", hours=0, amount=100))])
    assert s.net_per_hour is None
    assert s.best_platform is Platform.SWIGGY


def test_work_type_breakdown() -> None:
    s = compute_summary(
        [
            make(entry()),
            make(entry(id="e2", platform="rapido", work_type="bike_taxi", amount=300, hours=2)),
            make(expense()),
        ]
    )
    by_type = {w.work_type: w for w in s.work_types}
    assert by_type[WorkType.DELIVERY].income == 1200
    assert by_type[WorkType.BIKE_TAXI].net_per_hour == 150
    assert by_type[None].expenses == 200


def test_weekly_and_monthly_grouping() -> None:
    entries = [
        make(entry(id="a", date="2026-09-28")),  # ISO week 40, September
        make(expense(id="b", date="2026-09-30")),  # week 40, September
        make(entry(id="c", date="2026-10-01")),  # week 40, October
        make(entry(id="d", date="2026-10-05")),  # week 41, October
    ]
    s = compute_summary(entries)
    assert [w.period for w in s.weekly] == ["2026-W41", "2026-W40"]
    assert s.weekly[1].days == 2  # days with income
    assert s.weekly[1].expenses == 200
    assert [m.period for m in s.monthly] == ["2026-10", "2026-09"]


def test_best_weekday_averages_per_day_with_income() -> None:
    entries = [
        make(entry(id="a", date="2026-10-05", amount=500)),  # Monday
        make(entry(id="b", date="2026-10-12", amount=100)),  # Monday
        make(entry(id="c", date="2026-10-10", amount=400)),  # Saturday
        make(expense(id="d", date="2026-10-11", amount=50)),  # Sunday: expense only, not a work day
    ]
    assert compute_summary(entries).best_weekday == 5


def test_today_totals() -> None:
    entries = [make(entry()), make(expense()), make(entry(id="e2", date="2026-10-11"))]
    assert day_total(entries, date(2026, 10, 12)).model_dump() == {
        "period": "2026-10-12",
        "days": 1,
        "income": 1200,
        "expenses": 200,
        "net": 1000,
        "hours": 8,
    }
    assert compute_summary(entries, date(2026, 10, 1)).today.net == 0  # type: ignore[union-attr]


def test_rounding_is_half_up_to_paise() -> None:
    s = compute_summary([make(entry(amount=10.005, hours=3)), make(entry(id="e2", amount=0.1, hours=0))])
    assert s.total_income == 10.11  # 10.005 + 0.1 = 10.105 -> 10.11 (no float drift)
    assert s.net_per_hour == 3.37


def test_keys() -> None:
    assert week_key(date(2026, 1, 1)) == "2026-W01"
    assert month_key(date(2026, 3, 9)) == "2026-03"


@pytest.mark.parametrize(
    "payload",
    [
        entry(amount=0),
        entry(amount=-5),
        entry(amount=1_000_001),
        entry(hours=25),
        entry(platform="flipkart"),
        entry(category="fuel"),  # expense category on income
        expense(category="tips"),  # income category on expense
        expense(hours=2),  # hours only on income
        entry(id="bad id!"),
        entry(date=(today_ist() + timedelta(days=2)).isoformat()),
        entry(date="1999-12-31"),
        entry(note="x" * 81),
        entry(unknown_field=1),
    ],
)
def test_entry_validation_failures(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make(payload)


def test_today_cannot_be_far_future() -> None:
    with pytest.raises(ValidationError):
        SummaryRequest.model_validate({"entries": [], "today": (today_ist() + timedelta(days=3)).isoformat()})


def test_summary_for_prompt_has_no_ids_dates_or_notes() -> None:
    record = summary_for_prompt(compute_summary([make(entry(id="secret123", note="Ravi paid me"))]))
    text = str(record)
    assert "secret123" not in text and "2026-10-12" not in text and "Ravi" not in text
    assert record["best_platform"] == "Swiggy"
    assert record["best_weekday"] == "Monday"
    assert record["work_types"] == [{"name": "Delivery partner", "net_rs": 1200.0}]


def test_summary_endpoint(client: TestClient) -> None:
    res = client.post("/api/ledger/summary", json={"entries": [entry(), expense()], "today": "2026-10-12"})
    assert res.status_code == 200
    body = res.json()
    assert body["total_net"] == 1000
    assert body["today"]["net"] == 1000


def test_summary_endpoint_rejects_invalid(client: TestClient) -> None:
    res = client.post("/api/ledger/summary", json={"entries": [entry(hours=99)]})
    assert res.status_code == 422
    body = res.json()["error"]
    assert body["code"] == "invalid_input"
    assert "99" not in str(body)  # submitted values are never echoed


def test_advice_endpoint_returns_three_tips(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.json_responses["AdviceTips"] = {"tips": ["a", "b", "c", "d"]}
    res = client.post("/api/ledger/advice", json={"entries": [entry(), expense()], "language": "hi"})
    assert res.status_code == 200
    assert res.json()["tips"] == ["a", "b", "c"]
    request = fake_llm.requests[-1]
    assert "Hindi" in request.system
    assert "e1" not in request.prompt


def test_advice_empty_tips_is_an_error(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.json_responses["AdviceTips"] = {"tips": ["  "]}
    res = client.post("/api/ledger/advice", json={"entries": [entry()]})
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "ai_empty"
