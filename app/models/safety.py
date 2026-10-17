"""Schemas for the Safety Assistant."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.common import ChatHistory, IssueType, Language, Question, StrictModel, WorkTypes


class SafetyChatRequest(StrictModel):
    language: Language = Language.EN
    message: Question
    history: ChatHistory = Field(default_factory=list)
    work_types: WorkTypes = Field(default_factory=list)  # from the worker's profile; no PII


class SafetyModelOutput(BaseModel):
    """Structured Gemini output: the reply plus an optional complaint suggestion."""

    reply: str
    suggest_complaint: bool
    issue_type: IssueType | None = None


class SafetyChatResponse(BaseModel):
    reply: str
    suggest_complaint: bool
    issue_type: IssueType | None
