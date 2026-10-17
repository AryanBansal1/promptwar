"""Safety Assistant: Gemini chat plus hardcoded, verified emergency contacts."""

from __future__ import annotations

from dataclasses import dataclass

from app.llm.client import LLMClient, ai_empty
from app.llm.prompts import SPECS, safety_chat_request
from app.models.safety import SafetyChatRequest, SafetyChatResponse, SafetyModelOutput
from app.security import cap_text, strip_phone_numbers


@dataclass(frozen=True)
class EmergencyContact:
    """A national helpline. Constants only; the LLM never produces phone numbers."""

    key: str
    number: str


EMERGENCY_CONTACTS: tuple[EmergencyContact, ...] = (
    EmergencyContact("emergency", "112"),
    EmergencyContact("ambulance", "108"),
    EmergencyContact("women_1091", "1091"),
    EmergencyContact("women_181", "181"),
)


async def safety_reply(llm: LLMClient, payload: SafetyChatRequest) -> SafetyChatResponse:
    """Answer a safety question and decide whether to offer a complaint draft."""
    request = safety_chat_request(payload.message, payload.history, payload.language, payload.work_types)
    output = await llm.generate_json(request, SafetyModelOutput)
    reply = cap_text(strip_phone_numbers(output.reply), SPECS["safety_chat"].max_chars)
    if not reply:
        raise ai_empty()
    suggest = output.suggest_complaint and output.issue_type is not None
    return SafetyChatResponse(
        reply=reply,
        suggest_complaint=suggest,
        issue_type=output.issue_type if suggest else None,
    )
