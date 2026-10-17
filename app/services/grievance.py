"""Grievance drafter: Gemini writes the letter; where to send it comes only from escalation.json."""

from __future__ import annotations

from app.llm.client import LLMClient, ai_empty
from app.llm.prompts import SPECS, grievance_request
from app.models.common import ISSUE_NAMES, PLATFORM_NAMES, Language, Platform
from app.models.grievance import EscalationContact, GrievanceDraft, GrievanceRequest, GrievanceResponse
from app.security import cap_text
from app.services.ai import cap_items
from app.services.data_store import EscalationData

_GIG_PLATFORMS = frozenset(p for p in Platform if p is not Platform.OTHER)


def escalation_contacts(data: EscalationData, payload: GrievanceRequest) -> list[EscalationContact]:
    """Deterministic list of where to send the complaint, by issue type and platform."""
    contacts: list[EscalationContact] = []
    for channel_id in data.routes[payload.issue_type.value]:
        if channel_id == "platform":
            support = data.platform_support[payload.platform]
            contacts.append(
                EscalationContact(
                    kind="platform_support",
                    name=support.name,
                    url=str(support.website) if support.website else None,
                    email=support.email,
                    help_url=str(support.help_url) if support.help_url else None,
                )
            )
            continue
        # Consumer channels apply to app-based platforms, not to contractors or other employers.
        if channel_id in ("nch", "consumer_commission") and payload.platform not in _GIG_PLATFORMS:
            continue
        channel = data.channels[channel_id]
        contacts.append(
            EscalationContact(
                kind=channel.kind,
                name=channel.name,
                url=str(channel.url) if channel.url else None,
                phone=channel.phone,
            )
        )
    return contacts


def grievance_facts(payload: GrievanceRequest) -> dict[str, object]:
    """Structured, enum-derived facts. Free text (description, employer name) goes in <user_input>."""
    return {
        "issue": ISSUE_NAMES[payload.issue_type],
        "platform_or_employer_type": PLATFORM_NAMES[payload.platform],
        "start_date": payload.start_date.isoformat(),
        "end_date": payload.end_date.isoformat() if payload.end_date else None,
        "amount_rs": payload.amount,
        "state": payload.state.value if payload.state else None,
    }


def _user_text(payload: GrievanceRequest) -> str:
    employer = f"Employer / company name: {payload.employer_name}\n" if payload.employer_name else ""
    return f"{employer}What happened: {payload.description}"


async def draft_grievance(llm: LLMClient, payload: GrievanceRequest, escalation: EscalationData) -> GrievanceResponse:
    """Draft English + local-language letters and next steps."""
    request = grievance_request(grievance_facts(payload), _user_text(payload), payload.language)
    draft = await llm.generate_json(request, GrievanceDraft)
    max_chars = SPECS["grievance_draft"].max_chars
    letter_english = cap_text(draft.letter_english, max_chars)
    letter_local = None if payload.language is Language.EN else cap_text(draft.letter_local, max_chars)
    if not letter_english or (letter_local is not None and not letter_local):
        raise ai_empty()
    return GrievanceResponse(
        letter_english=letter_english,
        letter_local=letter_local,
        next_steps=cap_items(draft.next_steps, "grievance_draft", 5),
        send_to=escalation_contacts(escalation, payload),
        language=payload.language,
    )
