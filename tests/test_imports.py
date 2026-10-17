"""Importing income & expenses from screenshots, PDFs and pasted SMS text."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.models.common import today_ist
from app.models.imports import Direction, RawImportRow
from app.services.imports import mask_long_numbers, to_row, validate_rows
from app.services.uploads import IMPORT_TYPES, MAX_IMPORT_BYTES, validate_upload
from tests.fakes import FakeLLMClient

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
TODAY = today_ist().isoformat()


def raw(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "date": "2026-10-12",
        "amount": 650,
        "direction": "credit",
        "counterparty": "ZOMATO MEDIA",
        "suggested_platform": "zomato",
        "suggested_category": "earnings",
        "confidence": 0.95,
    }
    base.update(overrides)
    return base


# ---------- Upload validation ----------


@pytest.mark.parametrize(
    ("mime", "data"),
    [("image/png", PNG), ("application/pdf", PDF), ("image/jpeg", JPEG), ("image/webp", WEBP)],
    ids=["png", "pdf", "jpeg", "webp"],
)
def test_valid_uploads(mime: str, data: bytes) -> None:
    assert validate_upload(f"{mime}; charset=binary", data, IMPORT_TYPES, MAX_IMPORT_BYTES).mime_type == mime


@pytest.mark.parametrize(
    ("mime", "data", "status"),
    [
        ("image/gif", b"GIF89a....", 415),  # type not allowed
        ("image/png", PDF, 415),  # declared type does not match the bytes
        ("application/pdf", b"", 422),  # empty
        ("image/png", PNG + b"\x00" * MAX_IMPORT_BYTES, 413),  # too large
        (None, PNG, 415),  # no content type
    ],
    ids=["gif", "mismatch", "empty", "too-large", "no-type"],
)
def test_invalid_uploads(mime: str | None, data: bytes, status: int) -> None:
    with pytest.raises(AppError) as exc:
        validate_upload(mime, data, IMPORT_TYPES, MAX_IMPORT_BYTES)
    assert exc.value.status_code == status


# ---------- Row validation ----------


def test_valid_row_maps_direction_category_platform() -> None:
    row = to_row(RawImportRow.model_validate(raw()))
    assert row is not None
    assert (row.kind, row.category, row.platform) == ("income", "earnings", "zomato")
    assert not row.low_confidence and not row.needs_date


def test_debit_row_with_income_category_falls_back_to_other() -> None:
    row = to_row(
        RawImportRow.model_validate(raw(direction="debit", suggested_category="tips", suggested_platform="paytm"))
    )
    assert row is not None
    assert (row.kind, row.category, row.platform) == ("expense", "other", None)


def test_missing_date_and_low_confidence_are_flagged_not_invented() -> None:
    row = to_row(RawImportRow.model_validate(raw(date=None, confidence=0.3)))
    assert row is not None
    assert row.date is None and row.needs_date and row.low_confidence


@pytest.mark.parametrize(
    "bad",
    [
        {"amount": None},
        {"amount": 0},
        {"amount": -50},
        {"amount": 2_000_000},
        {"date": "17/10/2026"},
        {"date": (today_ist() + timedelta(days=3)).isoformat()},
        {"confidence": 7},
    ],
)
def test_invalid_rows_are_dropped(bad: dict[str, object]) -> None:
    assert to_row(RawImportRow.model_validate(raw(**bad))) is None


def test_validate_rows_counts_dropped() -> None:
    result = validate_rows([RawImportRow.model_validate(raw()), RawImportRow.model_validate(raw(amount=None))])
    assert (len(result.rows), result.found, result.dropped) == (1, 2, 1)


def test_counterparty_long_numbers_masked() -> None:
    row = to_row(RawImportRow.model_validate(raw(counterparty="Paid to 98765 43210 via UPI")))
    assert row is not None and "98765" not in row.counterparty


def test_mask_long_numbers_keeps_amounts_and_dates() -> None:
    text = "Rs 1,250.00 credited on 2026-10-12 to A/c 123456789012 from 9876543210"
    masked = mask_long_numbers(text)
    assert "1,250.00" in masked and "2026-10-12" in masked
    assert "123456789012" not in masked and "9876543210" not in masked


def test_direction_enum() -> None:
    assert {d.value for d in Direction} == {"credit", "debit"}


# ---------- Endpoints ----------


def test_file_import_sends_image_in_memory(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.json_responses["RawImportOutput"] = {
        "transactions": [raw(), raw(amount=None), raw(date=None, confidence=0.2)]
    }
    res = client.post(
        f"/api/ledger/import/file?language=hi&today={TODAY}", content=PNG, headers={"Content-Type": "image/png"}
    )
    assert res.status_code == 200
    body = res.json()
    assert (body["found"], body["dropped"], len(body["rows"])) == (3, 1, 2)
    assert body["rows"][1]["needs_date"] and body["rows"][1]["low_confidence"]
    request = fake_llm.requests[-1]
    assert request.attachments[0].mime_type == "image/png" and request.attachments[0].data == PNG
    assert request.timeout_seconds == 45
    assert f'"today":"{TODAY}"' in request.prompt


def test_file_import_nothing_found(client: TestClient, fake_llm: FakeLLMClient) -> None:
    fake_llm.json_responses["RawImportOutput"] = {"transactions": []}
    res = client.post(
        f"/api/ledger/import/file?today={TODAY}", content=PDF, headers={"Content-Type": "application/pdf"}
    )
    assert res.json() == {"rows": [], "found": 0, "dropped": 0}


@pytest.mark.parametrize(
    ("data", "mime", "status"),
    [(b"GIF89a", "image/gif", 415), (b"", "image/png", 422), (PDF, "image/png", 415)],
    ids=["gif", "empty", "mismatch"],
)
def test_file_import_rejects_bad_files(client: TestClient, data: bytes, mime: str, status: int) -> None:
    res = client.post(f"/api/ledger/import/file?today={TODAY}", content=data, headers={"Content-Type": mime})
    assert res.status_code == status


def test_file_import_oversized_is_413(client: TestClient) -> None:
    big = PNG + b"\x00" * MAX_IMPORT_BYTES
    res = client.post(f"/api/ledger/import/file?today={TODAY}", content=big, headers={"Content-Type": "image/png"})
    assert res.status_code == 413


def test_file_import_requires_valid_query(client: TestClient) -> None:
    assert client.post("/api/ledger/import/file", content=PNG, headers={"Content-Type": "image/png"}).status_code == 422
    future = (today_ist() + timedelta(days=5)).isoformat()
    res = client.post(f"/api/ledger/import/file?today={future}", content=PNG, headers={"Content-Type": "image/png"})
    assert res.status_code == 422


def test_other_endpoints_keep_small_body_cap(client: TestClient) -> None:
    res = client.post("/api/ask", content=b"x" * 1_500_000, headers={"Content-Type": "application/json"})
    assert res.status_code == 413


def test_text_import_masks_numbers_and_never_logs_content(
    client: TestClient, fake_llm: FakeLLMClient, caplog: pytest.LogCaptureFixture
) -> None:
    fake_llm.json_responses["RawImportOutput"] = {"transactions": [raw(direction="debit", suggested_category="fuel")]}
    sms = "Rs 200 debited from A/c XX1234 at INDIAN OIL. Call 9876543210 if not you."
    with caplog.at_level(logging.DEBUG):
        res = client.post("/api/ledger/import/text", json={"text": sms, "today": TODAY, "language": "pa"})
    assert res.status_code == 200
    assert res.json()["rows"][0]["category"] == "fuel"
    prompt = fake_llm.requests[-1].prompt
    assert "INDIAN OIL" in prompt and "9876543210" not in prompt
    assert "INDIAN OIL" not in caplog.text and "9876543210" not in caplog.text


@pytest.mark.parametrize("text", ["", "x" * 5001], ids=["empty", "too-long"])
def test_text_import_length_limits(client: TestClient, text: str) -> None:
    assert client.post("/api/ledger/import/text", json={"text": text, "today": TODAY}).status_code == 422
