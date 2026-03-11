from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from assistant_app.scheduled_task_cron import validate_cron_expr
from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.normalization import (
    TIMESTAMP_FORMAT,
    normalize_datetime_text,
    normalize_optional_datetime_text,
    normalize_required_text,
)


def normalize_scheduled_task_run_limit(value: object, *, field_name: str) -> int:
    error_message = f"{field_name} must be -1 or >= 0"
    if isinstance(value, bool):
        raise ValueError(error_message)
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(error_message)
        parsed = int(value)
    else:
        normalized = normalize_required_text(value, field_name=field_name)
        if normalized == "-1":
            return -1
        if not normalized.isdigit():
            raise ValueError(error_message)
        parsed = int(normalized)
    if parsed == -1 or parsed >= 0:
        return parsed
    raise ValueError(error_message)


def normalize_scheduled_task_cron_expr(value: object, *, field_name: str) -> str:
    normalized = normalize_required_text(value, field_name=field_name)
    try:
        return validate_cron_expr(normalized, now=datetime.now())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid cron expression") from exc


class ScheduledPlannerTask(FrozenModel):
    id: int = Field(ge=1)
    task_name: str = Field(min_length=1)
    run_limit: int
    cron_expr: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    next_run_at: str | None = None
    last_run_at: str | None = None
    created_at: str
    updated_at: str

    @field_validator("task_name", "cron_expr", "prompt", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return normalize_required_text(value, field_name=field_name)

    @field_validator("run_limit", mode="before")
    @classmethod
    def normalize_run_limit(cls, value: object) -> int:
        return normalize_scheduled_task_run_limit(value, field_name="run_limit")

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def normalize_required_datetime_fields(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "datetime")
        return normalize_datetime_text(value, field_name=field_name, formats=(TIMESTAMP_FORMAT,))

    @field_validator("next_run_at", "last_run_at", mode="before")
    @classmethod
    def normalize_optional_datetime_fields(cls, value: object, info: object) -> str | None:
        field_name = getattr(info, "field_name", "datetime")
        return normalize_optional_datetime_text(value, field_name=field_name, formats=(TIMESTAMP_FORMAT,))


class ScheduledPlannerTaskCreateInput(FrozenModel):
    task_name: str = Field(min_length=1)
    run_limit: int = -1
    cron_expr: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    next_run_at: str | None = None

    @field_validator("task_name", "cron_expr", "prompt", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        if field_name == "cron_expr":
            return normalize_scheduled_task_cron_expr(value, field_name=field_name)
        return normalize_required_text(value, field_name=field_name)

    @field_validator("run_limit", mode="before")
    @classmethod
    def normalize_run_limit(cls, value: object) -> int:
        return normalize_scheduled_task_run_limit(value, field_name="run_limit")

    @field_validator("next_run_at", mode="before")
    @classmethod
    def normalize_next_run_at(cls, value: object) -> str | None:
        return normalize_optional_datetime_text(value, field_name="next_run_at", formats=(TIMESTAMP_FORMAT,))


class ScheduledPlannerTaskUpdateInput(FrozenModel):
    task_name: str = Field(min_length=1)
    run_limit: int
    cron_expr: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    next_run_at: str | None = None

    @field_validator("task_name", "cron_expr", "prompt", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        if field_name == "cron_expr":
            return normalize_scheduled_task_cron_expr(value, field_name=field_name)
        return normalize_required_text(value, field_name=field_name)

    @field_validator("run_limit", mode="before")
    @classmethod
    def normalize_run_limit(cls, value: object) -> int:
        return normalize_scheduled_task_run_limit(value, field_name="run_limit")

    @field_validator("next_run_at", mode="before")
    @classmethod
    def normalize_next_run_at(cls, value: object) -> str | None:
        return normalize_optional_datetime_text(value, field_name="next_run_at", formats=(TIMESTAMP_FORMAT,))


class ScheduledTaskResultDecision(FrozenModel):
    should_send: bool


class ScheduledTaskResultDecisionChatTurn(FrozenModel):
    user_content: str = ""
    assistant_content: str = ""
    created_at: str

    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, value: object) -> str:
        return normalize_datetime_text(value, field_name="created_at", formats=(TIMESTAMP_FORMAT,))


class ScheduledTaskResultDecisionPlanStep(FrozenModel):
    task: str = Field(min_length=1)
    completed: bool = False
    tools: list[str] = Field(default_factory=list)

    @field_validator("task", mode="before")
    @classmethod
    def normalize_task(cls, value: object) -> str:
        return normalize_required_text(value, field_name="task")


class ScheduledTaskResultDecisionCompletedSubtask(FrozenModel):
    item: str = Field(min_length=1)
    result: str = ""

    @field_validator("item", mode="before")
    @classmethod
    def normalize_item(cls, value: object) -> str:
        return normalize_required_text(value, field_name="item")


class ScheduledTaskResultDecisionObservation(FrozenModel):
    tool: str = Field(min_length=1)
    input: str = ""
    ok: bool
    result: str = ""

    @field_validator("tool", mode="before")
    @classmethod
    def normalize_tool(cls, value: object) -> str:
        return normalize_required_text(value, field_name="tool")


class ScheduledTaskResultDecisionPlanStepTrace(FrozenModel):
    goal: str = ""
    step_count: int = Field(default=0, ge=0)
    latest_plan: list[ScheduledTaskResultDecisionPlanStep] = Field(default_factory=list)
    completed_subtasks: list[ScheduledTaskResultDecisionCompletedSubtask] = Field(default_factory=list)
    observations: list[ScheduledTaskResultDecisionObservation] = Field(default_factory=list)


class ScheduledTaskResultDecisionContext(FrozenModel):
    task_name: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    final_response: str = ""
    started_at: str
    finished_at: str
    duration_seconds: int = Field(ge=0)

    @field_validator("task_name", "prompt", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return normalize_required_text(value, field_name=field_name)

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def normalize_required_datetime_fields(cls, value: object, info: object) -> str:
        field_name = getattr(info, "field_name", "datetime")
        return normalize_datetime_text(value, field_name=field_name, formats=(TIMESTAMP_FORMAT,))


class ScheduledTaskResultDoneSchemaContract(FrozenModel):
    should_send: str = "boolean"


class ScheduledTaskResultOutputContract(FrozenModel):
    terminal_action: str = "done"
    done_schema: ScheduledTaskResultDoneSchemaContract = Field(default_factory=ScheduledTaskResultDoneSchemaContract)


class ScheduledTaskResultDecisionPromptPayload(FrozenModel):
    task: str = "scheduled_task_result_delivery_decision"
    timezone: str = Field(default="local", min_length=1)
    result: ScheduledTaskResultDecisionContext
    user_profile: str | None = None
    chat_history: list[ScheduledTaskResultDecisionChatTurn] = Field(default_factory=list)
    plan_step_trace: ScheduledTaskResultDecisionPlanStepTrace = Field(
        default_factory=ScheduledTaskResultDecisionPlanStepTrace
    )
    output_contract: ScheduledTaskResultOutputContract = Field(default_factory=ScheduledTaskResultOutputContract)


__all__ = [
    "ScheduledPlannerTask",
    "ScheduledPlannerTaskCreateInput",
    "ScheduledPlannerTaskUpdateInput",
    "ScheduledTaskResultDecision",
    "ScheduledTaskResultDecisionChatTurn",
    "ScheduledTaskResultDecisionContext",
    "ScheduledTaskResultDecisionPlanStepTrace",
    "ScheduledTaskResultDecisionPromptPayload",
    "normalize_scheduled_task_cron_expr",
    "normalize_scheduled_task_run_limit",
]
