"""Scheme eligibility rules and the schemes endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models.common import WorkType
from app.models.schemes import EligibilityProfile
from app.services.data_store import get_data_store
from app.services.schemes import match_schemes
from tests.fakes import FakeLLMClient

SCHEMES = get_data_store().schemes


def profile(**overrides: object) -> EligibilityProfile:
    base: dict[str, object] = {
        "age": 30,
        "monthly_income": 12000,
        "work_types": ["delivery"],
        "state": "PB",
        "has_bank_account": True,
    }
    base.update(overrides)
    return EligibilityProfile.model_validate(base)


def matched_ids(p: EligibilityProfile) -> set[str]:
    return {m.scheme.id for m in match_schemes(SCHEMES, p)[0]}


def test_typical_delivery_worker_matches_everything() -> None:
    assert matched_ids(profile()) == {"eshram", "pm_sym", "pmjjby", "pmsby", "pm_jay", "code_social_security_2020"}


@pytest.mark.parametrize(("age", "expected"), [(15, False), (16, True), (59, True), (60, False)])
def test_eshram_age_edges(age: int, expected: bool) -> None:
    assert ("eshram" in matched_ids(profile(age=age))) is expected


@pytest.mark.parametrize(
    ("age", "income", "expected"),
    [(17, 10000, False), (18, 10000, True), (40, 15000, True), (41, 10000, False), (30, 15001, False)],
)
def test_pm_sym_age_and_income_edges(age: int, income: float, expected: bool) -> None:
    assert ("pm_sym" in matched_ids(profile(age=age, monthly_income=income))) is expected


@pytest.mark.parametrize(("age", "expected"), [(17, False), (18, True), (50, True), (51, False)])
def test_pmjjby_age_edges(age: int, expected: bool) -> None:
    assert ("pmjjby" in matched_ids(profile(age=age))) is expected


@pytest.mark.parametrize(("age", "expected"), [(18, True), (70, True), (71, False)])
def test_pmsby_age_edges(age: int, expected: bool) -> None:
    assert ("pmsby" in matched_ids(profile(age=age))) is expected


def test_bank_account_required_for_insurance_and_pension() -> None:
    p = profile(has_bank_account=False)
    ids = matched_ids(p)
    assert {"pm_sym", "pmjjby", "pmsby"}.isdisjoint(ids)
    assert set(match_schemes(SCHEMES, p)[1]) == {"pm_sym", "pmjjby", "pmsby"}


def test_pm_jay_routes() -> None:
    assert "pm_jay" in matched_ids(profile(work_types=["ride_hailing"], monthly_income=50000))  # platform worker
    assert "pm_jay" in matched_ids(profile(work_types=["other"], age=72, monthly_income=90000))  # 70+
    assert "pm_jay" in matched_ids(profile(work_types=["domestic_work"], monthly_income=9000))  # low income
    assert "pm_jay" not in matched_ids(profile(work_types=["domestic_work"], monthly_income=20000))


def test_code_on_social_security_only_for_platform_workers() -> None:
    platform_work = {WorkType.DELIVERY, WorkType.RIDE_HAILING, WorkType.BIKE_TAXI, WorkType.HOME_SERVICES}
    for work in WorkType:
        expected = work in platform_work
        assert ("code_social_security_2020" in matched_ids(profile(work_types=[work]))) is expected


def test_match_reason_comes_from_rule_note() -> None:
    matches, _ = match_schemes(SCHEMES, profile(work_types=["other"], age=75, monthly_income=50000))
    pm_jay = next(m for m in matches if m.scheme.id == "pm_jay")
    assert "70" in pm_jay.reason


def test_match_endpoint(client: TestClient) -> None:
    res = client.post("/api/schemes/match", json=profile().model_dump())
    assert res.status_code == 200
    assert len(res.json()["matches"]) == 6


@pytest.mark.parametrize(
    "bad",
    [{"age": 5}, {"age": 101}, {"monthly_income": -1}, {"state": "XX"}, {"work_types": ["pilot"]}, {"work_types": []}],
)
def test_match_endpoint_validation(client: TestClient, bad: dict[str, object]) -> None:
    res = client.post("/api/schemes/match", json={**profile().model_dump(), **bad})
    assert res.status_code == 422


def test_list_endpoint(client: TestClient) -> None:
    assert len(client.get("/api/schemes").json()) == len(SCHEMES)


def test_explain_is_grounded_and_cached(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.text_response = "Simple explanation"
    body = {**profile(work_types=["other"], age=45, monthly_income=50000).model_dump(), "language": "pa"}
    first = client.post("/api/schemes/explain", json=body)
    second = client.post("/api/schemes/explain", json=body)
    assert first.status_code == 200
    assert first.json()["explanation"] == "Simple explanation"
    assert second.json() == first.json()
    assert len(fake_llm.requests) == 1  # second call served from cache
    prompt = fake_llm.requests[0].prompt
    assert "PMSBY" in prompt and "Shram Yogi" not in prompt  # only matched records
    assert "50000" not in prompt  # profile values are not sent
    assert "Punjabi" in fake_llm.requests[0].system


def test_explain_with_no_matches(client: TestClient) -> None:
    body = profile(age=65, work_types=["other"], monthly_income=50000, has_bank_account=False).model_dump()
    res = client.post("/api/schemes/explain", json=body)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "no_matches"


def test_multiple_work_types_match_if_any_qualifies() -> None:
    domestic_only = matched_ids(profile(work_types=["domestic_work"], monthly_income=20000))
    with_delivery = matched_ids(profile(work_types=["domestic_work", "delivery"], monthly_income=20000))
    assert "code_social_security_2020" not in domestic_only
    assert "code_social_security_2020" in with_delivery
    assert "pm_jay" in with_delivery


def test_duplicate_work_types_are_collapsed() -> None:
    assert profile(work_types=["delivery", "delivery"]).work_types == [WorkType.DELIVERY]
