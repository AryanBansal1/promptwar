"""Test-only fake LLM client. Lives outside ``app/`` and is refused by the app in production."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from app.llm.client import LLMError, LLMRequest

ModelT = TypeVar("ModelT", bound=BaseModel)


class FakeLLMClient:
    """Returns queued responses and records every request it receives."""

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []
        self.text_response = "fake text"
        self.json_responses: dict[str, object] = {}
        self.error: LLMError | None = None

    async def generate_text(self, request: LLMRequest) -> str:
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.text_response

    async def generate_json(self, request: LLMRequest, schema: type[ModelT]) -> ModelT:
        self.requests.append(request)
        if self.error:
            raise self.error
        return schema.model_validate(self.json_responses[schema.__name__])
