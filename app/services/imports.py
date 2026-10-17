"""Import income & expenses from a screenshot, PDF or pasted SMS text with Gemini.

Files are processed in memory only and never logged. Every row Gemini returns is validated on its own;
invalid rows are dropped and counted so the UI can say so. Nothing is saved here: the worker reviews
and confirms each row in the browser.
"""

from __future__ import annotations

import re
from datetime import date

from pydantic import ValidationError

from app.llm.client import Attachment, LLMClient
from app.llm.prompts import import_request
from app.models.common import Language, Platform
from app.models.imports import (
    LOW_CONFIDENCE,
    MAX_IMPORT_ROWS,
    Direction,
    ImportResponse,
    ImportRow,
    RawImportOutput,
    RawImportRow,
)
from app.models.ledger import Category, EntryKind, categories_for
from app.security import cap_text, clean_text
from app.services.uploads import ValidUpload

_LONG_NUMBER = re.compile(r"\d[\d\s-]{8,}\d")  # phone, account and reference numbers (10+ characters)
_MASK = "XXXX"


def mask_long_numbers(text: str) -> str:
    """Hide long digit runs (phone/account/UPI reference numbers) before text leaves the server."""
    return _LONG_NUMBER.sub(lambda m: _MASK if sum(c.isdigit() for c in m.group()) >= 9 else m.group(), text)


def _category(raw: str | None, kind: EntryKind) -> Category:
    allowed = categories_for(kind)
    if raw and raw in {c.value for c in allowed}:
        return Category(raw)
    return Category.EARNINGS if kind is EntryKind.INCOME else Category.OTHER


def _platform(raw: str | None) -> Platform | None:
    return Platform(raw) if raw in {p.value for p in Platform} else None


def to_row(raw: RawImportRow) -> ImportRow | None:
    """Validate one proposed row; ``None`` means it is dropped (bad amount, date or confidence)."""
    kind = EntryKind.INCOME if raw.direction is Direction.CREDIT else EntryKind.EXPENSE
    confidence = raw.confidence if raw.confidence is not None else 0.0
    try:
        return ImportRow.model_validate(
            {
                "date": raw.date or None,
                "amount": raw.amount,
                "direction": raw.direction,
                "kind": kind,
                "category": _category(raw.suggested_category, kind),
                "platform": _platform(raw.suggested_platform),
                "counterparty": cap_text(mask_long_numbers(clean_text(raw.counterparty or "")), 60),
                "confidence": confidence,
                "low_confidence": confidence < LOW_CONFIDENCE,
                "needs_date": not raw.date,
            }
        )
    except ValidationError:
        return None


def validate_rows(raw_rows: list[RawImportRow]) -> ImportResponse:
    """Keep valid rows (up to the cap) and count the rest as dropped."""
    rows = [row for row in map(to_row, raw_rows) if row is not None]
    kept = rows[:MAX_IMPORT_ROWS]
    return ImportResponse(rows=kept, found=len(raw_rows), dropped=len(raw_rows) - len(kept))


async def import_from_file(llm: LLMClient, upload: ValidUpload, today: date, language: Language) -> ImportResponse:
    """Read transactions from an image or PDF held in memory."""
    request = import_request(today, language, attachment=Attachment(upload.data, upload.mime_type))
    output = await llm.generate_json(request, RawImportOutput)
    return validate_rows(output.transactions)


async def import_from_text(llm: LLMClient, text: str, today: date, language: Language) -> ImportResponse:
    """Read transactions from pasted SMS / statement text (long numbers masked first)."""
    request = import_request(today, language, text=mask_long_numbers(text))
    output = await llm.generate_json(request, RawImportOutput)
    return validate_rows(output.transactions)
