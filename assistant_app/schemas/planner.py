from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

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


class ProactiveDoneArguments(FrozenModel):
    score: int = Field(ge=0, le=100)
    message: str
    reason: str = Field(min_length=1)


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


__all__ = [
    "AssistantToolMessage",
    "PlanStepPayload",
    "PlannedDecision",
    "ProactiveDoneArguments",
    "ReplanDoneDecision",
    "ReplannedDecision",
    "ThoughtAskUserDecision",
    "ThoughtContinueDecision",
    "ThoughtDoneDecision",
    "ThoughtNextAction",
    "ToolCallPayload",
    "ToolFunctionPayload",
    "ToolReplyPayload",
    "normalize_tool_call_payload",
]
