"""Ask Saathi 'log_transaction': parsing, relative dates resolved in code, validation, no persistence."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.models.ask import IntentSchema, RawTransaction
from app.models.common import today_ist
from app.services.router_intent import resolve_date, to_proposed, validate_transactions
from tests.fakes import FakeLLMClient

TODAY = date(2026, 10, 17)


def item(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "type": "income",
        "amount": 800,
        "platform": "swiggy",
        "category": "earnings",
        "day": "today",
        "date": None,
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def reply(*items: dict[str, object], intent: str = "log_transaction") -> dict[str, object]:
    return {"intent": intent, "answer": "Please check these entries.", "prefill": {}, "transactions": list(items)}


# ---------- Relative dates are resolved in code ----------


@pytest.mark.parametrize(
    ("day", "explicit", "expected"),
    [
        ("today", None, TODAY),
        ("yesterday", None, TODAY - timedelta(days=1)),
        ("day_before_yesterday", None, TODAY - timedelta(days=2)),
        ("unspecified", None, TODAY),
        (None, None, TODAY),
        ("date", "2026-10-05", date(2026, 10, 5)),
        ("date", "5 Oct", None),
        ("date", None, None),
        ("tomorrow", None, None),
    ],
)
def test_resolve_date(day: str | None, explicit: str | None, expected: date | None) -> None:
    assert resolve_date(day, explicit, TODAY) == expected


def test_yesterday_uses_the_workers_local_date_not_the_llm() -> None:
    proposed = to_proposed(RawTransaction.model_validate(item(day="yesterday", date="2020-01-01")), TODAY)
    assert proposed is not None and proposed.date == date(2026, 10, 16)  # the LLM's date field is ignored


# ---------- Validation drops bad items ----------


@pytest.mark.parametrize(
    "bad",
    [
        {"amount": None},
        {"amount": 0},
        {"amount": -10},
        {"amount": 5_000_000},
        {"type": "transfer"},
        {"type": None},
        {"platform": "paytm"},
        {"category": "salary"},
        {"type": "expense", "category": "tips"},  # category does not fit the type
        {"day": "date", "date": (today_ist() + timedelta(days=5)).isoformat()},  # future
        {"day": "date", "date": "1999-01-01"},
    ],
)
def test_invalid_items_are_dropped(bad: dict[str, object]) -> None:
    assert to_proposed(RawTransaction.model_validate(item(**bad)), TODAY) is None


def test_missing_category_gets_a_safe_default_and_low_confidence_is_flagged() -> None:
    income = to_proposed(RawTransaction.model_validate(item(category=None, confidence=0.3)), TODAY)
    expense = to_proposed(RawTransaction.model_validate(item(type="expense", category=None, platform=None)), TODAY)
    assert income is not None and income.category == "earnings" and income.low_confidence
    assert expense is not None and expense.category == "other" and not expense.low_confidence


def test_validate_transactions_counts_dropped() -> None:
    raws = [RawTransaction.model_validate(item()), RawTransaction.model_validate(item(amount=None))]
    items, dropped = validate_transactions(raws, TODAY)
    assert (len(items), dropped) == (1, 1)


# ---------- Endpoint ----------


def test_hinglish_income_and_expense_in_one_sentence(client: TestClient, fake_llm: FakeLLMClient) -> None:
    today = today_ist()
    fake_llm.json_responses["IntentOutput"] = reply(
        item(amount=800),
        item(type="expense", amount=200, platform=None, category="fuel", day="today"),
    )
    res = client.post(
        "/api/ask",
        json={
            "message": "aaj Swiggy se 800 kamaye aur 200 ka petrol dala",
            "language": "hi",
            "today": today.isoformat(),
        },
    )
    body = res.json()
    assert res.status_code == 200
    assert body["intent"] == "log_transaction" and body["route"] == "#/"
    assert [(t["kind"], t["amount"], t["category"]) for t in body["transactions"]] == [
        ("income", 800, "earnings"),
        ("expense", 200, "fuel"),
    ]
    assert all(t["date"] == today.isoformat() for t in body["transactions"])
    assert body["dropped"] == 0
    request = fake_llm.requests[-1]
    assert request.response_schema is IntentSchema  # strict enums are sent to Gemini
    assert "aaj Swiggy se 800 kamaye" in request.prompt


def test_kal_resolves_from_client_today(client: TestClient, fake_llm: FakeLLMClient) -> None:
    today = today_ist()
    fake_llm.json_responses["IntentOutput"] = reply(item(platform="rapido", amount=150, day="yesterday"))
    body = client.post("/api/ask", json={"message": "kal Rapido se 150 kamaye", "today": today.isoformat()}).json()
    assert body["transactions"][0]["date"] == (today - timedelta(days=1)).isoformat()


def test_invalid_items_dropped_with_count(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.json_responses["IntentOutput"] = reply(item(), item(amount=None), item(platform="flipkart"))
    body = client.post("/api/ask", json={"message": "800 kamaye, kuch aur bhi"}).json()
    assert len(body["transactions"]) == 1 and body["dropped"] == 2


def test_no_amount_gives_empty_list_not_invented(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.json_responses["IntentOutput"] = reply()
    body = client.post("/api/ask", json={"message": "aaj kuch kamaya"}).json()
    assert body["transactions"] == [] and body["dropped"] == 0


def test_transactions_ignored_for_other_intents(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.json_responses["IntentOutput"] = reply(item(), intent="safety")
    body = client.post("/api/ask", json={"message": "is it safe at night?"}).json()
    assert body["transactions"] == []


def test_today_from_client_is_validated(client: TestClient) -> None:
    future = (today_ist() + timedelta(days=4)).isoformat()
    assert client.post("/api/ask", json={"message": "800 kamaye", "today": future}).status_code == 422


def test_nothing_is_persisted_by_the_server(client: TestClient, fake_llm: FakeLLMClient) -> None:
    """Proposals are only returned; the server keeps no ledger, so nothing exists until the browser saves it."""
    fake_llm.json_responses["IntentOutput"] = reply(item())
    client.post("/api/ask", json={"message": "800 kamaye"})
    client.post("/api/ask", json={"message": "800 kamaye"})
    state = vars(client.app.state)["_state"]  # type: ignore[attr-defined]
    assert not any("ledger" in key or "entries" in key for key in state)
    summary = client.post("/api/ledger/summary", json={"entries": []}).json()
    assert summary["entry_count"] == 0
