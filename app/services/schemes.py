"""Deterministic scheme eligibility matching and grounded Gemini explanations."""

from __future__ import annotations

from app.cache import TTLCache
from app.llm.client import LLMClient
from app.llm.prompts import schemes_explain_request
from app.models.common import Language
from app.models.schemes import EligibilityProfile, EligibilityRule, Scheme, SchemeMatch
from app.services.ai import generate_capped_text


def rule_matches(rule: EligibilityRule, profile: EligibilityProfile) -> bool:
    """True when every constraint present on ``rule`` holds for ``profile``."""
    checks = (
        rule.min_age is None or profile.age >= rule.min_age,
        rule.max_age is None or profile.age <= rule.max_age,
        rule.max_monthly_income is None or profile.monthly_income <= rule.max_monthly_income,
        rule.work_types is None or not set(rule.work_types).isdisjoint(profile.work_types),
        not rule.requires_bank_account or profile.has_bank_account,
        rule.states is None or profile.state in rule.states,
    )
    return all(checks)


def _without_bank_requirement(rule: EligibilityRule) -> EligibilityRule:
    return rule.model_copy(update={"requires_bank_account": False})


def match_schemes(schemes: dict[str, Scheme], profile: EligibilityProfile) -> tuple[list[SchemeMatch], list[str]]:
    """Return matched schemes (with the rule note that matched) and schemes blocked only by no bank account."""
    matches: list[SchemeMatch] = []
    needs_bank: list[str] = []
    for scheme in schemes.values():
        rule = next((r for r in scheme.eligibility_any_of if rule_matches(r, profile)), None)
        if rule is not None:
            matches.append(SchemeMatch(scheme=scheme, reason=rule.note))
        elif not profile.has_bank_account and any(
            rule_matches(_without_bank_requirement(r), profile) for r in scheme.eligibility_any_of
        ):
            needs_bank.append(scheme.id)
    return matches, needs_bank


def scheme_records(matches: list[SchemeMatch]) -> list[dict[str, object]]:
    """Only the fields Gemini needs, taken verbatim from the curated dataset."""
    return [
        {
            "name": m.scheme.name,
            "summary": m.scheme.summary,
            "why_matched": m.reason,
            "benefits": m.scheme.benefits,
            "how_to_apply": m.scheme.how_to_apply,
        }
        for m in sorted(matches, key=lambda m: m.scheme.id)
    ]


async def explain_schemes(llm: LLMClient, matches: list[SchemeMatch], language: Language, cache: TTLCache[str]) -> str:
    """Explain matched schemes, grounded only on their records. Cached by scheme set + language."""
    request = schemes_explain_request(scheme_records(matches), language)
    return await generate_capped_text(llm, "schemes_explain", request, cache)
