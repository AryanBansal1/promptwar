"""Validation for raw uploads (images, PDFs, audio). Files are only ever held in memory."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.errors import AppError

# Vercel Functions reject request bodies above ~4.5 MB, so no upload may exceed 4 MiB.
UPLOAD_CEILING_BYTES = 4 * 1024 * 1024
MAX_IMPORT_BYTES = UPLOAD_CEILING_BYTES
MAX_AUDIO_BYTES = 2 * 1024 * 1024


def _riff(form: bytes) -> Callable[[bytes], bool]:
    return lambda data: data[:4] == b"RIFF" and data[8:12] == form


def _mp4(data: bytes) -> bool:
    return data[4:8] == b"ftyp"


def _mpeg_audio(data: bytes) -> bool:
    # ID3-tagged MP3, or an MPEG/ADTS frame sync (MP3 and AAC).
    return data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)


SNIFFERS: dict[str, Callable[[bytes], bool]] = {
    "image/png": lambda d: d[:8] == b"\x89PNG\r\n\x1a\n",
    "image/jpeg": lambda d: d[:3] == b"\xff\xd8\xff",
    "image/webp": _riff(b"WEBP"),
    "application/pdf": lambda d: d[:5] == b"%PDF-",
    "audio/webm": lambda d: d[:4] == b"\x1a\x45\xdf\xa3",
    "audio/ogg": lambda d: d[:4] == b"OggS",
    "audio/wav": _riff(b"WAVE"),
    "audio/x-wav": _riff(b"WAVE"),
    "audio/mp4": _mp4,
    "audio/x-m4a": _mp4,
    "audio/aac": _mpeg_audio,
    "audio/mpeg": _mpeg_audio,
    "audio/flac": lambda d: d[:4] == b"fLaC",
}

IMPORT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "application/pdf"})
AUDIO_TYPES = frozenset(
    {
        "audio/webm",
        "audio/ogg",
        "audio/wav",
        "audio/x-wav",
        "audio/mp4",
        "audio/x-m4a",
        "audio/aac",
        "audio/mpeg",
        "audio/flac",
    }
)


@dataclass(frozen=True)
class ValidUpload:
    data: bytes
    mime_type: str


def base_mime(content_type: str | None) -> str:
    """``audio/webm;codecs=opus`` -> ``audio/webm``."""
    return (content_type or "").split(";", 1)[0].strip().lower()


def validate_upload(
    content_type: str | None,
    data: bytes,
    allowed: frozenset[str],
    max_bytes: int,
    type_error: str = "unsupported_file_type",
) -> ValidUpload:
    """Check type (declared and actual bytes), emptiness and size. Raises 415 / 422 / 413."""
    mime = base_mime(content_type)
    if mime not in allowed:
        raise AppError(type_error, "This file type is not supported.", 415)
    if not data:
        raise AppError("empty_file", "The file is empty.", 422)
    if len(data) > max_bytes:
        raise AppError("payload_too_large", "The file is too large.", 413)
    if not SNIFFERS[mime](data):
        raise AppError(type_error, "The file content does not match its type.", 415)
    return ValidUpload(data=data, mime_type=mime)
