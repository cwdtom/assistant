from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class TaskInterruptedError(RuntimeError):
    """Raised when an in-flight planner task is interrupted by a newer input."""


class ThoughtToolCallingError(RuntimeError):
    """Raised when thought tool-calling cannot proceed."""


@dataclass
class PlannerObservation:
    tool: str
    input_text: str
    ok: bool
    result: str


@dataclass
class CompletedSubtask:
    item: str
    result: str


@dataclass
class ClarificationTurn:
    role: str
    content: str


@dataclass
class PlanStep:
    item: str
    completed: bool = False
    tools: list[str] = field(default_factory=list)


@dataclass
class OuterPlanContext:
    goal: str
    clarification_history: list[ClarificationTurn] = field(default_factory=list)
    latest_plan: list[PlanStep] = field(default_factory=list)
    current_plan_index: int = 0
    completed_subtasks: list[CompletedSubtask] = field(default_factory=list)
    outer_messages: list[dict[str, str]] | None = None


@dataclass
class InnerReActContext:
    current_subtask: str = ""
    completed_subtasks: list[CompletedSubtask] = field(default_factory=list)
    observations: list[PlannerObservation] = field(default_factory=list)
    thought_messages: list[dict[str, Any]] = field(default_factory=list)
    response: str | None = None


@dataclass
class PendingPlanTask:
    goal: str
    outer_context: OuterPlanContext | None = None
    inner_context: InnerReActContext | None = None
    observations: list[PlannerObservation] = field(default_factory=list)
    step_count: int = 0
    plan_initialized: bool = False
    plan_ack_only: bool = False
    awaiting_clarification: bool = False
    # True means outer loop should run replan before next thought cycle.
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
