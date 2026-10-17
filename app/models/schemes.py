"""Schemas for Rights & Welfare Schemes."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, HttpUrl, field_validator

from app.models.common import Language, StateCode, StrictModel, WorkType, unique_work_types


class EligibilityRule(BaseModel):
    """All present constraints must hold for the rule to match."""

    min_age: int | None = None
    max_age: int | None = None
    max_monthly_income: float | None = None
    work_types: list[WorkType] | None = None
    requires_bank_account: bool = False
    states: list[StateCode] | None = None
    note: str


class Scheme(BaseModel):
    """A curated, real government scheme or legal provision."""

    id: Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]
    name: str
    kind: Annotated[str, Field(pattern=r"^(scheme|law)$")]
    summary: str
    eligibility_any_of: Annotated[list[EligibilityRule], Field(min_length=1)]
    benefits: Annotated[list[str], Field(min_length=1)]
    how_to_apply: Annotated[list[str], Field(min_length=1)]
    official_url: HttpUrl
    required_documents: list[str]
    document_goal: str | None = None
    last_verified: date

    @field_validator("official_url")
    @classmethod
    def _https_only(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("official_url must be https")
        return value


class EligibilityProfile(StrictModel):
    """Questionnaire answers. Used only for rule matching; never sent to the LLM."""

    age: Annotated[int, Field(ge=14, le=100)]
    monthly_income: Annotated[float, Field(ge=0, le=10_000_000)]
    work_types: Annotated[
        list[WorkType], Field(min_length=1, max_length=len(WorkType)), AfterValidator(unique_work_types)
    ]
    state: StateCode
    has_bank_account: bool


class ExplainRequest(EligibilityProfile):
    language: Language = Language.EN


class SchemeMatch(BaseModel):
    scheme: Scheme
    reason: str


class MatchResponse(BaseModel):
    matches: list[SchemeMatch]
    unmatched_needs_bank: list[str]


class ExplainResponse(BaseModel):
    scheme_ids: list[str]
    explanation: str
