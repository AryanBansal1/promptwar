"""Deployment configuration: Vercel entrypoint, body limits, bundled assets."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.llm.prompts import SPECS
from app.services.uploads import MAX_AUDIO_BYTES, MAX_IMPORT_BYTES, UPLOAD_CEILING_BYTES

ROOT = Path(__file__).resolve().parent.parent
VERCEL_BODY_LIMIT = 4_500_000  # bytes; Vercel Functions reject larger request bodies


def test_module_level_app_for_vercel_and_factory_still_work() -> None:
    import app.main as main

    assert isinstance(main.app, FastAPI)
    assert isinstance(main.create_app(), FastAPI)  # uvicorn --factory and tests keep using the factory


def test_pyproject_points_vercel_at_the_app_and_requirements_pin_everything() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["vercel"]["entrypoint"] == "app.main:app"
    requirements = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert all("==" in pin for pin in requirements)  # every package, including transitive ones, is pinned
    assert set(pyproject["project"]["dependencies"]) <= set(requirements)  # direct deps pinned identically


def test_every_upload_fits_under_the_vercel_body_limit() -> None:
    assert UPLOAD_CEILING_BYTES == 4 * 1024 * 1024 < VERCEL_BODY_LIMIT
    assert MAX_IMPORT_BYTES <= UPLOAD_CEILING_BYTES
    assert MAX_AUDIO_BYTES <= UPLOAD_CEILING_BYTES


def test_client_limits_match_the_server() -> None:
    ledger_js = (ROOT / "static" / "js" / "ledger.js").read_text(encoding="utf-8")
    voice_js = (ROOT / "static" / "js" / "voice.js").read_text(encoding="utf-8")
    assert re.search(r"MAX_FILE_BYTES = 4 \* 1024 \* 1024", ledger_js)
    assert re.search(r"MAX_AUDIO_BYTES = 2 \* 1024 \* 1024", voice_js)
    for lang in ("en", "hi", "pa", "ta", "mr"):
        data = json.loads((ROOT / "app" / "data" / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))
        assert "4 MB" in data["import"]["file_hint"] and "5 MB" not in data["import"]["too_big"]


def test_function_duration_covers_the_slowest_gemini_call() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    slowest = max(spec.timeout_seconds or 20 for spec in SPECS.values())
    assert config["functions"]["app/main.py"]["maxDuration"] > slowest


def test_vercelignore_keeps_runtime_files_and_drops_secrets() -> None:
    patterns = (ROOT / ".vercelignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in patterns and ".venv/" in patterns
    for needed in ("static", "app", "data", "fonts", "requirements.txt", "pyproject.toml"):
        assert not any(p.strip().rstrip("/") == needed for p in patterns), needed


def test_static_fonts_and_data_are_served(client: TestClient) -> None:
    font = client.get("/static/fonts/noto-sans-devanagari.woff2")
    assert font.status_code == 200 and font.content[:4] == b"wOF2"
    assert "immutable" in font.headers["cache-control"]
    assert client.get("/static/fonts/OFL.txt").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/api/i18n/bn").json()["app"]["name"]
    assert len(client.get("/api/schemes").json()) == 6
    assert client.get("/").status_code == 200
