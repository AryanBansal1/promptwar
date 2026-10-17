"""Schemas for the Grievance / Dispute Drafter."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints, model_validator

from app.models.common import IssueType, Language, Money, PastDate, Platform, StateCode, StrictModel


class GrievanceRequest(StrictModel):
    """Guided-form fields. No name, phone or ID is collected; the letter uses placeholders."""

    language: Language = Language.EN
    issue_type: IssueType
    platform: Platform
    employer_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] = ""
    state: StateCode | None = None
    start_date: PastDate
    end_date: PastDate | None = None
    amount: Money | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=2000)]

    @model_validator(mode="after")
    def _dates_in_order(self) -> GrievanceRequest:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class GrievanceDraft(BaseModel):
    """Structured Gemini output."""

    letter_english: str
    letter_local: str
    next_steps: list[str]


class EscalationContact(BaseModel):
    """A curated place to send the complaint (from escalation.json, never from the LLM)."""

    kind: str
    name: str
    url: str | None = None
    phone: str | None = None
    email: str | None = None  # only for platforms whose grievance email is confirmed on their own site
    help_url: str | None = None


class GrievanceResponse(BaseModel):
    letter_english: str
    letter_local: str | None
    next_steps: list[str]
    send_to: list[EscalationContact]
    language: Language
