from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from assistant_app.planner_common import THOUGHT_EXECUTION_TOOL_NAMES, normalize_plan_items, normalize_tool_names
from assistant_app.runtime_actions import coerce_runtime_action_payload, serialize_runtime_action_input
from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.normalization import EVENT_TIME_FORMAT, validate_datetime_text
from assistant_app.schemas.routing import RuntimePlannerActionPayload


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
    input: str = ""
    payload: RuntimePlannerActionPayload | None = Field(default=None, exclude=True)

    @field_validator("tool")
    @classmethod
    def validate_tool(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in THOUGHT_EXECUTION_TOOL_NAMES:
            raise ValueError("tool must be one of the execution tool names")
        return normalized

    @field_validator("input")
    @classmethod
    def normalize_input(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def normalize_runtime_payload(self) -> ThoughtNextAction:
        if self.payload is not None:
            serialized_input = serialize_runtime_action_input(action_tool=self.tool, payload=self.payload)
            object.__setattr__(self, "input", serialized_input)
            return self
        if not self.input:
            raise ValueError("next_action requires input")
        derived_payload = coerce_runtime_action_payload(action_tool=self.tool, raw_input=self.input)
        if derived_payload is not None:
            object.__setattr__(self, "payload", derived_payload)
        return self


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


class ClarificationTurnPayload(FrozenModel):
    role: str = Field(min_length=1)
    content: str = Field(min_length=1)


class CompletedSubtaskPayload(FrozenModel):
    item: str = Field(min_length=1)
    result: str


class ObservationPayload(FrozenModel):
    tool: str = Field(min_length=1)
    input: str
    ok: bool
    result: str


class PlannerContextPayload(FrozenModel):
    goal: str = Field(min_length=1)
    clarification_history: list[ClarificationTurnPayload] = Field(default_factory=list)
    step_count: int = Field(ge=0)
    max_steps: int = Field(ge=1)
    latest_plan: list[PlanStepPayload] = Field(default_factory=list)
    current_plan_index: int = Field(ge=0)
    completed_subtasks: list[CompletedSubtaskPayload] = Field(default_factory=list)
    user_profile: str | None = None
    time: str

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return validate_datetime_text(value, field_name="time", formats=(EVENT_TIME_FORMAT,))


class ThoughtCurrentSubtaskPayload(FrozenModel):
    item: str = ""
    index: int | None = Field(default=None, ge=1)
    total: int | None = Field(default=None, ge=1)
    tools: list[str] = Field(default_factory=list)

    @field_validator("tools")
    @classmethod
    def normalize_tools(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            name = str(item or "").strip().lower()
            if not name:
                raise ValueError("tools must not contain empty values")
            if name not in normalized:
                normalized.append(name)
        return normalized


class ThoughtContextPayload(FrozenModel):
    clarification_history: list[ClarificationTurnPayload] = Field(default_factory=list)
    step_count: int = Field(ge=0)
    max_steps: int = Field(ge=1)
    current_subtask: ThoughtCurrentSubtaskPayload = Field(default_factory=ThoughtCurrentSubtaskPayload)
    completed_subtasks: list[CompletedSubtaskPayload] = Field(default_factory=list)
    current_subtask_observations: list[ObservationPayload] = Field(default_factory=list)
    user_profile: str | None = None
    time: str

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return validate_datetime_text(value, field_name="time", formats=(EVENT_TIME_FORMAT,))


class PlanPromptPayload(FrozenModel):
    phase: Literal["plan"]
    goal: str = Field(min_length=1)
    clarification_history: list[ClarificationTurnPayload] = Field(default_factory=list)
    step_count: int = Field(ge=0)
    max_steps: int = Field(ge=1)
    latest_plan: list[PlanStepPayload] = Field(default_factory=list)
    current_plan_index: int = Field(ge=0)
    completed_subtasks: list[CompletedSubtaskPayload] = Field(default_factory=list)
    user_profile: str | None = None
    time: str

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return validate_datetime_text(value, field_name="time", formats=(EVENT_TIME_FORMAT,))


class ReplanPromptPayload(FrozenModel):
    phase: Literal["replan"]
    goal: str = Field(min_length=1)
    clarification_history: list[ClarificationTurnPayload] = Field(default_factory=list)
    step_count: int = Field(ge=0)
    max_steps: int = Field(ge=1)
    latest_plan: list[PlanStepPayload] = Field(default_factory=list)
    current_plan_index: int = Field(ge=0)
    completed_subtasks: list[CompletedSubtaskPayload] = Field(default_factory=list)
    user_profile: str | None = None
    time: str
    current_plan_item: str = ""

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return validate_datetime_text(value, field_name="time", formats=(EVENT_TIME_FORMAT,))


class ThoughtPromptPayload(FrozenModel):
    phase: Literal["thought"]
    current_plan_item: str = ""
    clarification_history: list[ClarificationTurnPayload] = Field(default_factory=list)
    step_count: int = Field(ge=0)
    max_steps: int = Field(ge=1)
    current_subtask: ThoughtCurrentSubtaskPayload = Field(default_factory=ThoughtCurrentSubtaskPayload)
    completed_subtasks: list[CompletedSubtaskPayload] = Field(default_factory=list)
    current_subtask_observations: list[ObservationPayload] = Field(default_factory=list)
    user_profile: str | None = None
    time: str

    @field_validator("time")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return validate_datetime_text(value, field_name="time", formats=(EVENT_TIME_FORMAT,))


class ThoughtDecisionMessagePayload(FrozenModel):
    phase: Literal["thought_decision"]
    decision: ThoughtDecision


class ThoughtObservationMessagePayload(FrozenModel):
    phase: Literal["thought_observation"]
    observation: ObservationPayload


class _CompatPayloadModel:
    model_config = ConfigDict(extra="ignore", strict=True, str_strip_whitespace=True)


def _normalize_required_text(value: Any, *, lowercase: bool = False) -> str:
    text = str(value or "").strip()
    return text.lower() if lowercase else text


def _normalize_optional_text(value: Any, *, lowercase: bool = False) -> str | None:
    text = _normalize_required_text(value, lowercase=lowercase)
    return text or None


class PlannedDecisionCompatPayload(_CompatPayloadModel, FrozenModel):
    status: str
    goal: str
    plan: list[Any] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_root_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            "status": _normalize_required_text(value.get("status"), lowercase=True),
            "goal": _normalize_required_text(value.get("goal")),
            "plan": [] if value.get("plan") is None else value.get("plan"),
        }


class ReplanDecisionCompatPayload(_CompatPayloadModel, FrozenModel):
    status: str
    response: str | None = None
    plan: list[Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_root_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {
            "status": _normalize_required_text(value.get("status"), lowercase=True),
            "response": _normalize_optional_text(value.get("response")),
            "plan": [] if value.get("plan") is None else value.get("plan"),
        }


class ThoughtNextActionCompatPayload(_CompatPayloadModel, FrozenModel):
    tool: str
    input: str

    @field_validator("tool", mode="before")
    @classmethod
    def normalize_tool(cls, value: Any) -> str:
        return _normalize_required_text(value, lowercase=True)

    @field_validator("input", mode="before")
    @classmethod
    def normalize_input(cls, value: Any) -> str:
        return _normalize_required_text(value)


class ThoughtDecisionCompatPayload(_CompatPayloadModel, FrozenModel):
    status: str
    current_step: str = ""
    next_action: ThoughtNextActionCompatPayload | None = None
    question: str | None = None
    response: str | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_root_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        current_step = _normalize_required_text(value.get("current_step"))
        if not current_step:
            plan_items = normalize_plan_items(value)
            if plan_items:
                current_step = plan_items[0]
        return {
            "status": _normalize_required_text(value.get("status"), lowercase=True),
            "current_step": current_step,
            "next_action": value.get("next_action"),
            "question": _normalize_optional_text(value.get("question")),
            "response": _normalize_optional_text(value.get("response")),
        }


_TOOL_CALL_LIST_ADAPTER: TypeAdapter[list[ToolCallPayload]] = TypeAdapter(list[ToolCallPayload])
_THOUGHT_DECISION_ADAPTER: TypeAdapter[ThoughtDecision] = TypeAdapter(ThoughtDecision)


def _to_plain_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dumped
    return None


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


def parse_tool_reply_payload(
    raw_payload: Any,
    *,
    plain_tool_call_converter: Callable[[Any], Any] | None = None,
) -> ToolReplyPayload | None:
    if isinstance(raw_payload, ToolReplyPayload):
        return raw_payload
    payload = _to_plain_mapping(raw_payload)
    if payload is None:
        return None
    assistant_message = normalize_assistant_tool_message(
        payload.get("assistant_message"),
        plain_tool_call_converter=plain_tool_call_converter,
    )
    if assistant_message is None:
        assistant_message = normalize_assistant_tool_message(
            {"role": "assistant", "content": None, "tool_calls": []}
        )
    if assistant_message is None:
        return None

    reasoning = payload.get("reasoning_content")
    if reasoning is None:
        reasoning_text: str | None = None
    elif isinstance(reasoning, str):
        reasoning_text = reasoning
    else:
        reasoning_text = str(reasoning)
    try:
        return ToolReplyPayload.model_validate(
            {
                "assistant_message": assistant_message,
                "reasoning_content": reasoning_text,
            }
        )
    except ValidationError:
        return None


def parse_planned_decision(raw_payload: Any) -> PlannedDecision | None:
    try:
        compat_payload = PlannedDecisionCompatPayload.model_validate(raw_payload)
        return PlannedDecision.model_validate(compat_payload.model_dump())
    except ValidationError:
        return None


def parse_replan_decision(raw_payload: Any) -> ReplanDecision | None:
    try:
        compat_payload = ReplanDecisionCompatPayload.model_validate(raw_payload)
        if compat_payload.status == "done":
            return ReplanDoneDecision.model_validate(
                {
                    "status": "done",
                    "response": compat_payload.response or "",
                }
            )
        return ReplannedDecision.model_validate(
            {
                "status": compat_payload.status,
                "plan": compat_payload.plan,
            }
        )
    except ValidationError:
        return None


def parse_thought_decision(raw_payload: Any) -> ThoughtDecision | None:
    try:
        compat_payload = ThoughtDecisionCompatPayload.model_validate(raw_payload)
        return _THOUGHT_DECISION_ADAPTER.validate_python(compat_payload.model_dump())
    except ValidationError:
        return None


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
    "ClarificationTurnPayload",
    "CompletedSubtaskPayload",
    "ObservationPayload",
    "PlannerContextPayload",
    "normalize_assistant_tool_message",
    "parse_tool_reply_payload",
    "parse_planned_decision",
    "parse_replan_decision",
    "parse_thought_decision",
    "PlanPromptPayload",
    "PlanResponsePayload",
    "PlanStepPayload",
    "PlannedDecision",
    "ReplanPromptPayload",
    "ReplanDecision",
    "ReplanDoneDecision",
    "ReplanResponsePayload",
    "ReplannedDecision",
    "ThoughtContextPayload",
    "ThoughtAskUserDecision",
    "ThoughtContinueDecision",
    "ThoughtDecision",
    "ThoughtCurrentSubtaskPayload",
    "ThoughtDecisionMessagePayload",
    "ThoughtDoneDecision",
    "ThoughtNextAction",
    "ThoughtObservationMessagePayload",
    "ThoughtPromptPayload",
    "ThoughtResponsePayload",
    "ToolCallPayload",
    "ToolFunctionPayload",
    "ToolReplyPayload",
    "normalize_tool_call_payload",
    "normalize_tool_call_payloads",
]
