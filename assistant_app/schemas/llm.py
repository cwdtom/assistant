from __future__ import annotations

import json
from typing import Any

from pydantic import ConfigDict, Field, field_validator

from assistant_app.schemas.base import FrozenModel


class _LLMCompatModel(FrozenModel):
    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        from_attributes=True,
        str_strip_whitespace=True,
    )


class LLMFunctionCompat(_LLMCompatModel):
    name: str = ""
    arguments: str = "{}"

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("arguments", mode="before")
    @classmethod
    def normalize_arguments(cls, value: Any) -> str:
        if value is None:
            return "{}"
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except TypeError:
            return str(value)


class LLMToolCallCompat(_LLMCompatModel):
    id: str = ""
    type: str = "function"
    function: LLMFunctionCompat | None = None

    @field_validator("id", "type", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: Any) -> str:
        return str(value or "").strip()

    def to_plain_payload(self) -> dict[str, Any]:
        function = self.function or LLMFunctionCompat()
        return {
            "id": self.id,
            "type": self.type or "function",
            "function": {
                "name": function.name,
                "arguments": function.arguments,
            },
        }


class LLMAssistantMessageCompat(_LLMCompatModel):
    role: str = "assistant"
    content: Any = None
    tool_calls: list[LLMToolCallCompat] = Field(default_factory=list)
    reasoning_content: Any = None

    @field_validator("role", mode="before")
    @classmethod
    def normalize_role(cls, value: Any) -> str:
        return str(value or "assistant").strip() or "assistant"

    @field_validator("tool_calls", mode="before")
    @classmethod
    def normalize_tool_calls(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return list(value)

    def content_text(self) -> str:
        if isinstance(self.content, str):
            return self.content.strip()
        if self.content is None:
            return ""
        return str(self.content).strip()

    def reasoning_text(self) -> str | None:
        if self.reasoning_content is None:
            return None
        if isinstance(self.reasoning_content, str):
            normalized = self.reasoning_content.strip()
        else:
            normalized = str(self.reasoning_content).strip()
        return normalized or None

    def to_plain_payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tool_calls": [item.to_plain_payload() for item in self.tool_calls],
            "reasoning_content": self.reasoning_content,
        }


class LLMChatCompletionChoiceCompat(_LLMCompatModel):
    message: LLMAssistantMessageCompat


class LLMChatCompletionResponseCompat(_LLMCompatModel):
    choices: list[LLMChatCompletionChoiceCompat] = Field(min_length=1)

    @field_validator("choices", mode="before")
    @classmethod
    def normalize_choices(cls, value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        return list(value or [])

    def first_message(self) -> LLMAssistantMessageCompat:
        return self.choices[0].message


def parse_chat_completion_response(raw_response: Any) -> LLMChatCompletionResponseCompat:
    return LLMChatCompletionResponseCompat.model_validate(raw_response)


def parse_assistant_message(raw_message: Any) -> LLMAssistantMessageCompat:
    return LLMAssistantMessageCompat.model_validate(raw_message)


__all__ = [
    "LLMAssistantMessageCompat",
    "LLMChatCompletionChoiceCompat",
    "LLMChatCompletionResponseCompat",
    "LLMFunctionCompat",
    "LLMToolCallCompat",
    "parse_assistant_message",
    "parse_chat_completion_response",
]
