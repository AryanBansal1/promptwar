"""Schemas for Document Help."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl

from app.models.common import ChatHistory, Language, Question, StrictModel

RecordId = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$")]


class TargetKind(StrEnum):
    GOAL = "goal"
    SCHEME = "scheme"


class DocumentItem(BaseModel):
    id: RecordId
    name: str
    description: str


class Goal(BaseModel):
    id: RecordId
    name: str
    documents: Annotated[list[str], Field(min_length=1)]
    steps: Annotated[list[str], Field(min_length=1)]
    official_url: HttpUrl


class Checklist(BaseModel):
    kind: TargetKind
    id: str
    title: str
    items: list[DocumentItem]
    steps: list[str]
    official_url: HttpUrl


class ExplainDocsRequest(StrictModel):
    kind: TargetKind
    id: RecordId
    language: Language = Language.EN


class AskDocsRequest(ExplainDocsRequest):
    question: Question
    history: ChatHistory = Field(default_factory=list)


class TextAnswer(BaseModel):
    text: str
