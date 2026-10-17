"""Loads and validates every curated dataset once at startup."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, HttpUrl, StringConstraints, model_validator

from app.models.common import Language, Platform
from app.models.documents import DocumentItem, Goal
from app.models.schemes import Scheme

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _https(value: HttpUrl) -> HttpUrl:
    if value.scheme != "https":
        raise ValueError("contact URLs must be https")
    return value


HttpsUrl = Annotated[HttpUrl, AfterValidator(_https)]
Email = Annotated[str, StringConstraints(pattern=r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")]


class _Sourced(BaseModel):
    """Every published contact cites where it was confirmed, and when."""

    source_url: HttpsUrl | None = None
    last_verified: date | None = None

    @model_validator(mode="after")
    def _cited(self) -> _Sourced:
        has_contact = any(getattr(self, f, None) for f in ("email", "help_url", "website", "url"))
        if has_contact and (self.source_url is None or self.last_verified is None):
            raise ValueError("contacts with an email or URL need source_url and last_verified")
        return self


class PlatformSupport(_Sourced):
    """Official platform contacts. Emails are listed only when confirmed on the platform's own site."""

    name: str
    website: HttpsUrl | None = None
    help_url: HttpsUrl | None = None
    email: Email | None = None


class Channel(_Sourced):
    kind: str
    name: str
    url: HttpsUrl | None
    phone: str | None


class EscalationData(BaseModel):
    version: int
    platform_support: dict[Platform, PlatformSupport]
    channels: dict[str, Channel]
    routes: dict[str, list[str]]


@dataclass(frozen=True)
class DataStore:
    """Immutable, validated in-memory copy of all curated data."""

    schemes: dict[str, Scheme]
    documents: dict[str, DocumentItem]
    goals: dict[str, Goal]
    escalation: EscalationData
    i18n: dict[Language, dict[str, Any]]
    i18n_json: dict[Language, bytes]  # pre-serialised once, served as-is


def _read(data_dir: Path, name: str) -> Any:
    with (data_dir / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def load_data_store(data_dir: Path = DATA_DIR) -> DataStore:
    """Read and validate all JSON files. Raises on any schema or reference error."""
    schemes = [Scheme.model_validate(s) for s in _read(data_dir, "schemes.json")["schemes"]]
    docs_raw = _read(data_dir, "documents.json")
    documents = [DocumentItem.model_validate(d) for d in docs_raw["documents"]]
    goals = [Goal.model_validate(g) for g in docs_raw["goals"]]
    escalation = EscalationData.model_validate(_read(data_dir, "escalation.json"))
    i18n = {lang: _read(data_dir, f"i18n/{lang.value}.json") for lang in Language}

    store = DataStore(
        schemes={s.id: s for s in schemes},
        documents={d.id: d for d in documents},
        goals={g.id: g for g in goals},
        escalation=escalation,
        i18n=i18n,
        i18n_json={lang: json.dumps(d, ensure_ascii=False, separators=(",", ":")).encode() for lang, d in i18n.items()},
    )
    _check_references(store)
    return store


def _check_references(store: DataStore) -> None:
    """Every cross-file id must resolve."""
    for scheme in store.schemes.values():
        for doc_id in scheme.required_documents:
            if doc_id not in store.documents:
                raise ValueError(f"scheme {scheme.id} references unknown document {doc_id}")
        if scheme.document_goal and scheme.document_goal not in store.goals:
            raise ValueError(f"scheme {scheme.id} references unknown goal {scheme.document_goal}")
    for goal in store.goals.values():
        for doc_id in goal.documents:
            if doc_id not in store.documents:
                raise ValueError(f"goal {goal.id} references unknown document {doc_id}")
    for issue, channel_ids in store.escalation.routes.items():
        for channel_id in channel_ids:
            if channel_id != "platform" and channel_id not in store.escalation.channels:
                raise ValueError(f"route {issue} references unknown channel {channel_id}")


@lru_cache(maxsize=1)
def get_data_store() -> DataStore:
    """Process-wide cached data store."""
    return load_data_store()
