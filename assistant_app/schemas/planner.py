from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator

from assistant_app.planner_common import THOUGHT_EXECUTION_TOOL_NAMES, normalize_tool_names
from assistant_app.schemas.base import FrozenModel


class PlanStepPayload(FrozenModel):
    task: str = Field(min_length=1)
    completed: bool
    tools: list[str]

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, value: list[str]) -> list[str]:
        normalized = normalize_tool_names(value, allowed_tools=THOUGHT_EXECUTION_TOOL_NAMES)
        if normalized is None:
            raise ValueError("tools must be a valid execution tool list")
        return normalized


class PlannedDecision(FrozenModel):
    status: Literal["planned"]
    goal: str = Field(min_length=1)
    plan: list[PlanStepPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_plan(self) -> PlannedDecision:
        if any(item.completed for item in self.plan):
            raise ValueError("planned decision cannot contain completed steps")
        return self


class ReplannedDecision(FrozenModel):
    status: Literal["replanned"]
    plan: list[PlanStepPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pending_step(self) -> ReplannedDecision:
        if not any(not item.completed for item in self.plan):
            raise ValueError("replanned decision must include at least one pending step")
        return self


class ReplanDoneDecision(FrozenModel):
    status: Literal["done"]
    response: str = Field(min_length=1)


class ThoughtNextAction(FrozenModel):
    tool: str = Field(min_length=1)
    input: str = Field(min_length=1)

    @field_validator("tool")
    @classmethod
    def validate_tool(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in THOUGHT_EXECUTION_TOOL_NAMES:
            raise ValueError("tool must be one of the execution tool names")
        return normalized


class ThoughtContinueDecision(FrozenModel):
    status: Literal["continue"]
    current_step: str = ""
    next_action: ThoughtNextAction
    question: None = None
    response: None = None


class ThoughtAskUserDecision(FrozenModel):
    status: Literal["ask_user"]
    current_step: str = ""
    next_action: None = None
    question: str = Field(min_length=1)
    response: None = None


class ThoughtDoneDecision(FrozenModel):
    status: Literal["done"]
    current_step: str = ""
    next_action: None = None
    question: None = None
    response: str | None = None


ThoughtDecision: TypeAlias = Annotated[
    ThoughtContinueDecision | ThoughtAskUserDecision | ThoughtDoneDecision,
    Field(discriminator="status"),
]
ReplanDecision: TypeAlias = Annotated[
    ReplannedDecision | ReplanDoneDecision,
    Field(discriminator="status"),
]


class ToolFunctionPayload(FrozenModel):
    name: str = Field(min_length=1)
    arguments: str = "{}"


class ToolCallPayload(FrozenModel):
    id: str = ""
    type: str = Field(min_length=1)
    function: ToolFunctionPayload


class AssistantToolMessage(FrozenModel):
    role: str = Field(min_length=1)
    content: str | None = None
    tool_calls: list[ToolCallPayload] = Field(default_factory=list)


class ToolReplyPayload(FrozenModel):
    assistant_message: AssistantToolMessage
    reasoning_content: str | None = None


class PlanResponsePayload(FrozenModel):
    decision: PlannedDecision
    raw_response: str


class ReplanResponsePayload(FrozenModel):
    decision: ReplanDecision
    raw_response: str


class ThoughtResponsePayload(FrozenModel):
    decision: ThoughtDecision
    assistant_message: AssistantToolMessage | None = None
    tool_call_id: str | None = None

    @field_validator("tool_call_id")
    @classmethod
    def normalize_tool_call_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_tool_message(self) -> ThoughtResponsePayload:
        if self.tool_call_id and self.assistant_message is None:
            raise ValueError("tool_call_id requires assistant_message")
        return self


class ProactiveDoneArguments(FrozenModel):
    score: int = Field(ge=0, le=100)
    message: str
    reason: str = Field(min_length=1)


_TOOL_CALL_LIST_ADAPTER = TypeAdapter(list[ToolCallPayload])


def normalize_tool_call_payload(raw_tool_call: Any) -> ToolCallPayload | None:
    if not isinstance(raw_tool_call, dict):
        return None
    function = raw_tool_call.get("function")
    if not isinstance(function, dict):
        return None
    arguments = function.get("arguments")
    if arguments is None:
        normalized_arguments = "{}"
    elif isinstance(arguments, str):
        normalized_arguments = arguments
    else:
        normalized_arguments = str(arguments)
    try:
        return ToolCallPayload.model_validate(
            {
                "id": str(raw_tool_call.get("id") or ""),
                "type": str(raw_tool_call.get("type") or "function"),
                "function": {
                    "name": str(function.get("name") or "").strip(),
                    "arguments": normalized_arguments,
                },
            }
        )
    except Exception:
        return None


def normalize_tool_call_payloads(
    raw_tool_calls: Any,
    *,
    plain_converter: Callable[[Any], Any] | None = None,
) -> list[ToolCallPayload]:
    if not isinstance(raw_tool_calls, list):
        return []
    normalized_payloads: list[dict[str, Any]] = []
    for raw_item in raw_tool_calls:
        candidate = plain_converter(raw_item) if plain_converter is not None else raw_item
        tool_call = normalize_tool_call_payload(candidate)
        if tool_call is not None:
            normalized_payloads.append(tool_call.model_dump())
    try:
        return _TOOL_CALL_LIST_ADAPTER.validate_python(normalized_payloads)
    except ValidationError:
        return []


def normalize_assistant_tool_message(
    raw_message: Any,
    *,
    plain_tool_call_converter: Callable[[Any], Any] | None = None,
    default_role: str = "assistant",
) -> AssistantToolMessage | None:
    if not isinstance(raw_message, dict):
        return None
    content = raw_message.get("content")
    if content is None:
        content_text: str | None = None
    elif isinstance(content, str):
        content_text = content
    else:
        content_text = str(content)
    try:
        return AssistantToolMessage.model_validate(
            {
                "role": str(raw_message.get("role") or default_role),
                "content": content_text,
                "tool_calls": normalize_tool_call_payloads(
                    raw_message.get("tool_calls"),
                    plain_converter=plain_tool_call_converter,
                ),
            }
        )
    except ValidationError:
        return None


__all__ = [
    "AssistantToolMessage",
    "normalize_assistant_tool_message",
    "PlanResponsePayload",
    "PlanStepPayload",
    "PlannedDecision",
    "ProactiveDoneArguments",
    "ReplanDecision",
    "ReplanDoneDecision",
    "ReplanResponsePayload",
    "ReplannedDecision",
    "ThoughtAskUserDecision",
    "ThoughtContinueDecision",
    "ThoughtDecision",
    "ThoughtDoneDecision",
    "ThoughtNextAction",
    "ThoughtResponsePayload",
    "ToolCallPayload",
    "ToolFunctionPayload",
    "ToolReplyPayload",
    "normalize_tool_call_payload",
    "normalize_tool_call_payloads",
]
