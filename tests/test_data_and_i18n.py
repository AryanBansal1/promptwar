"""Curated data integrity, i18n completeness, and prompt templates."""

from __future__ import annotations

import json
import re
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.llm import prompts
from app.llm.client import Attachment
from app.models.ask import DocumentGoalId
from app.models.common import ChatRole, ChatTurn, IssueType, Language, Platform, WorkType
from app.services.data_store import DATA_DIR, get_data_store, load_data_store

STORE = get_data_store()


def flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{path}."))
        else:
            out[path] = value
    return out


# ---------- Data integrity ----------


def test_required_schemes_present() -> None:
    assert set(STORE.schemes) == {"eshram", "pm_sym", "pmjjby", "pmsby", "pm_jay", "code_social_security_2020"}


def test_every_url_is_https() -> None:
    urls = [str(s.official_url) for s in STORE.schemes.values()]
    urls += [str(g.official_url) for g in STORE.goals.values()]
    urls += [str(c.url) for c in STORE.escalation.channels.values() if c.url]
    for p in STORE.escalation.platform_support.values():
        urls += [str(u) for u in (p.website, p.help_url, p.source_url) if u]
    assert urls and all(u.startswith("https://") for u in urls)


def test_scheme_document_references_exist() -> None:
    for scheme in STORE.schemes.values():
        assert scheme.required_documents
        assert all(d in STORE.documents for d in scheme.required_documents)
        assert scheme.document_goal is None or scheme.document_goal in STORE.goals


def test_goals_match_ask_enum_and_reference_documents() -> None:
    assert set(STORE.goals) == {g.value for g in DocumentGoalId}
    for goal in STORE.goals.values():
        assert all(d in STORE.documents for d in goal.documents)


def test_escalation_covers_every_platform_and_issue() -> None:
    assert set(STORE.escalation.platform_support) == set(Platform)
    assert set(STORE.escalation.routes) == {i.value for i in IssueType}
    assert all(r[0] == "platform" for r in STORE.escalation.routes.values())


EMAIL = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")


def test_every_escalation_contact_is_sourced_with_https_and_valid_email() -> None:
    contacts = [*STORE.escalation.platform_support.values(), *STORE.escalation.channels.values()]
    for contact in contacts:
        published = [getattr(contact, f, None) for f in ("email", "help_url", "website", "url")]
        if any(published):
            assert contact.source_url is not None and str(contact.source_url).startswith("https://"), contact
            assert contact.last_verified is not None, contact
        email = getattr(contact, "email", None)
        if email:
            assert EMAIL.match(email), email


def test_emails_only_where_confirmed_on_the_platforms_own_site() -> None:
    with_email = {p.value: s.email for p, s in STORE.escalation.platform_support.items() if s.email}
    assert with_email == {
        "swiggy": "grievances@swiggy.in",
        "zomato": "grievance@zomato.com",
        "porter": "grievance.officer@theporter.in",
    }
    for platform, support in STORE.escalation.platform_support.items():
        if support.email:
            domain = support.email.split("@")[1]
            source_host = str(support.source_url).split("/")[2]
            assert domain.split(".")[0] in source_host or platform.value in source_host


def test_bad_contact_email_fails_validation(tmp_path: Path) -> None:
    shutil.copytree(DATA_DIR, tmp_path / "data")
    path = tmp_path / "data" / "escalation.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["platform_support"]["zomato"]["email"] = "not-an-email"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_data_store(tmp_path / "data")


def test_unsourced_contact_fails_validation(tmp_path: Path) -> None:
    shutil.copytree(DATA_DIR, tmp_path / "data")
    path = tmp_path / "data" / "escalation.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["platform_support"]["uber"]["source_url"] = None
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="source_url"):
        load_data_store(tmp_path / "data")


def test_escalation_phone_numbers_are_known_helplines() -> None:
    phones = {c.phone for c in STORE.escalation.channels.values() if c.phone}
    assert phones == {"1915", "112"}


def test_rule_work_types_are_valid() -> None:
    for scheme in STORE.schemes.values():
        for rule in scheme.eligibility_any_of:
            assert rule.work_types is None or set(rule.work_types) <= set(WorkType)


def test_broken_reference_fails_loading(tmp_path: Path) -> None:
    shutil.copytree(DATA_DIR, tmp_path / "data")
    path = tmp_path / "data" / "schemes.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schemes"][0]["required_documents"].append("does_not_exist")
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown document"):
        load_data_store(tmp_path / "data")


def test_http_url_fails_validation(tmp_path: Path) -> None:
    shutil.copytree(DATA_DIR, tmp_path / "data")
    path = tmp_path / "data" / "schemes.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schemes"][0]["official_url"] = "http://eshram.gov.in"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="https"):
        load_data_store(tmp_path / "data")


# ---------- i18n ----------

EN_KEYS = flatten(STORE.i18n[Language.EN])


@pytest.mark.parametrize("lang", list(Language))
def test_every_key_exists_in_every_language(lang: Language) -> None:
    keys = flatten(STORE.i18n[lang])
    assert set(keys) == set(EN_KEYS)
    assert all(isinstance(v, str) and v.strip() for v in keys.values())


@pytest.mark.parametrize("lang", list(Language))
def test_placeholders_preserved(lang: Language) -> None:
    keys = flatten(STORE.i18n[lang])
    for key, english in EN_KEYS.items():
        assert set(re.findall(r"\{\w+\}", english)) == set(re.findall(r"\{\w+\}", keys[key])), key


def test_i18n_covers_enums_and_data() -> None:
    assert set(STORE.i18n[Language.EN]["platforms"]) == {p.value for p in Platform}
    assert set(STORE.i18n[Language.EN]["issues"]) == {i.value for i in IssueType}
    assert set(STORE.i18n[Language.EN]["work_types"]) == {w.value for w in WorkType}
    assert set(STORE.i18n[Language.EN]["goals"]) == set(STORE.goals)
    assert set(STORE.i18n[Language.EN]["doc_items"]) == set(STORE.documents)
    kinds = {c.kind for c in STORE.escalation.channels.values()} | {"platform_support"}
    assert kinds == set(STORE.i18n[Language.EN]["escalation"])


def test_frontend_keys_exist() -> None:
    static = Path(__file__).resolve().parent.parent / "static"
    html = (static / "index.html").read_text(encoding="utf-8")
    js = "\n".join(p.read_text(encoding="utf-8") for p in (static / "js").glob("*.js"))
    used = set(re.findall(r'data-i18n(?:-placeholder)?="([\w.]+)"', html))
    used |= set(re.findall(r'\bt\("([a-z_]+\.[\w.]+)"', js))
    missing = sorted(k for k in used if k not in EN_KEYS)
    assert not missing


def test_i18n_endpoint(client) -> None:  # type: ignore[no-untyped-def]
    res = client.get("/api/i18n/ta")
    assert res.status_code == 200
    assert res.json()["app"]["name"] == "சாத்தி"
    assert client.get("/api/i18n/xx").status_code == 422


# ---------- Prompts ----------

SUMMARY = {"total_net_rs": 950}
TURNS = [ChatTurn(role=ChatRole.USER, text="hi"), ChatTurn(role=ChatRole.ASSISTANT, text="hello")]


def all_requests(lang: Language) -> dict[str, prompts.LLMRequest]:
    return {
        "ledger_advice": prompts.advice_request(SUMMARY, lang),
        "safety_chat": prompts.safety_chat_request("Is it safe?", TURNS, lang),
        "schemes_explain": prompts.schemes_explain_request([{"name": "PMSBY"}], lang),
        "documents_explain": prompts.documents_explain_request({"goal": "PAN"}, lang),
        "documents_ask": prompts.documents_ask_request({"goal": "PAN"}, "How?", TURNS, lang),
        "grievance_draft": prompts.grievance_request({"issue": "Unpaid"}, "Not paid", lang),
        "intent": prompts.intent_request("Swiggy did not pay", lang),
        "ledger_import": prompts.import_request(date(2026, 10, 17), lang, text="Rs 500 credited"),
        "transcribe": prompts.transcribe_request(lang, Attachment(b"OggS", "audio/ogg")),
    }


@pytest.mark.parametrize("lang", list(Language))
def test_every_template_renders_in_every_language(lang: Language) -> None:
    requests = all_requests(lang)
    assert set(requests) == set(prompts.SPECS) == set(prompts.TASKS)
    for feature, request in requests.items():
        assert prompts.LANGUAGE_NAMES[lang] in request.system, feature
        assert "<user_input>" in request.system and "Never write phone numbers" in request.system
        assert "{" not in request.system.replace("{language}", "")  # no unrendered placeholders
        assert request.max_output_tokens == prompts.SPECS[feature].max_output_tokens
        if feature != "transcribe":  # speech-to-text has no curated records, only the audio attachment
            assert "<records>" in request.prompt


def test_prompts_include_required_fields() -> None:
    r = all_requests(Language.HI)
    assert "950" in r["ledger_advice"].prompt
    assert "<user_input>\nIs it safe?\n</user_input>" in r["safety_chat"].prompt
    assert "<history>" in r["safety_chat"].prompt and "Saathi: hello" in r["safety_chat"].prompt
    assert "unpaid_earnings" in r["safety_chat"].prompt  # issue enum offered
    assert "PMSBY" in r["schemes_explain"].prompt
    assert "Unpaid" in r["grievance_draft"].prompt and "Not paid" in r["grievance_draft"].prompt
    assert "letter_english must be written in English" in r["grievance_draft"].system
    assert "[Your Name]" in r["grievance_draft"].system
    assert "rapido" in r["intent"].prompt
