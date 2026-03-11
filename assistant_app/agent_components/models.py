from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import Field, field_validator

from assistant_app.planner_common import normalize_tool_names
from assistant_app.schemas.base import StrictModel
from assistant_app.schemas.planner import AssistantToolMessage


class TaskInterruptedError(RuntimeError):
    """Raised when an in-flight planner task is interrupted by a newer input."""


class ThoughtToolCallingError(RuntimeError):
    """Raised when thought tool-calling cannot proceed."""


class PlannerObservation(StrictModel):
    tool: str = Field(min_length=1)
    input_text: str
    ok: bool
    result: str


class CompletedSubtask(StrictModel):
    item: str = Field(min_length=1)
    result: str


class ClarificationTurn(StrictModel):
    role: str = Field(min_length=1)
    content: str = Field(min_length=1)


class PlanStep(StrictModel):
    item: str = Field(min_length=1)
    completed: bool = False
    tools: list[str] = Field(default_factory=list)

    @field_validator("tools")
    @classmethod
    def validate_tools(cls, value: list[str]) -> list[str]:
        normalized = normalize_tool_names(value)
        if normalized is None:
            raise ValueError("tools must be a valid tool list")
        return normalized


class PlannerTextMessage(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str


class PlannerToolMessage(StrictModel):
    role: Literal["tool"] = "tool"
    tool_call_id: str = Field(min_length=1)
    content: str


ThoughtMessage: TypeAlias = PlannerTextMessage | AssistantToolMessage | PlannerToolMessage


class OuterPlanContext(StrictModel):
    goal: str = Field(min_length=1)
    clarification_history: list[ClarificationTurn] = Field(default_factory=list)
    latest_plan: list[PlanStep] = Field(default_factory=list)
    current_plan_index: int = 0
    completed_subtasks: list[CompletedSubtask] = Field(default_factory=list)
    outer_messages: list[PlannerTextMessage] | None = None


class InnerReActContext(StrictModel):
    current_subtask: str = ""
    completed_subtasks: list[CompletedSubtask] = Field(default_factory=list)
    observations: list[PlannerObservation] = Field(default_factory=list)
    thought_messages: list[ThoughtMessage] = Field(default_factory=list)
    response: str | None = None


class PendingPlanTask(StrictModel):
    goal: str = Field(min_length=1)
    source: Literal["interactive", "scheduled"] = "interactive"
    outer_context: OuterPlanContext | None = None
    inner_context: InnerReActContext | None = None
    observations: list[PlannerObservation] = Field(default_factory=list)
    step_count: int = 0
    plan_initialized: bool = False
    plan_ack_only: bool = False
    awaiting_clarification: bool = False
    needs_replan: bool = False
    planner_failure_rounds: int = 0
    last_ask_user_question: str | None = None
    last_ask_user_clarification_len: int = 0
    ask_user_repeat_count: int = 0
    successful_steps: int = 0
    failed_steps: int = 0
    plan_goal_notified: bool = False
    last_reported_plan_signature: tuple[tuple[str, bool, tuple[str, ...]], ...] | None = None
    last_notified_completed_subtask_count: int = 0
