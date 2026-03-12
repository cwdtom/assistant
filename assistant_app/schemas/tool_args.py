from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import HttpUrlValue
from assistant_app.schemas.scheduled_tasks import (
    normalize_scheduled_task_cron_expr,
    normalize_scheduled_task_run_limit,
)
from assistant_app.schemas.search import normalize_bocha_freshness
from assistant_app.schemas.values import (
    HistoryListLimitValue,
    OptionalScheduleDateTimeValue,
    OptionalTagValue,
    OptionalThoughtStatusValue,
    PositiveIntValue,
    ScheduleDateTimeValue,
    ScheduleDurationValue,
    ScheduleRepeatTimesValue,
    ScheduleViewAnchorValue,
    ThoughtContentValue,
)


class ThoughtToolArgsBase(FrozenModel):
    current_step: str = Field(default="", description="当前步骤说明文本（可选）。")


class ScheduleAddArgs(ThoughtToolArgsBase):
    event_time: str = Field(description="日程开始时间，格式 YYYY-MM-DD HH:MM（本地时间）。")
    title: str = Field(min_length=1, description="日程标题文本。")
    tag: str | None = Field(default=None, description="日程标签；空值会回落到 default。")
    duration_minutes: int | None = Field(default=None, ge=1, description="日程时长，单位分钟，>=1。")
    remind_at: str | None = Field(
        default=None,
        description="提醒时间，格式 YYYY-MM-DD HH:MM（本地时间）；null 表示不设置。",
    )
    interval_minutes: int | None = Field(default=None, ge=1, description="重复间隔，单位分钟，>=1。")
    times: int | None = Field(default=None, description="重复次数：-1 表示无限重复，或 >=2 的有限重复次数。")
    remind_start_time: str | None = Field(
        default=None,
        description="重复提醒起始时间，格式 YYYY-MM-DD HH:MM（本地时间）；null 表示不设置。",
    )

    @field_validator("event_time")
    @classmethod
    def validate_event_time(cls, value: str) -> str:
        return ScheduleDateTimeValue.model_validate({"value": value}).value

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str | None:
        return OptionalTagValue.model_validate({"tag": value}).tag

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def normalize_duration_minutes(cls, value: Any) -> int | None:
        if value is None:
            raise ValueError("duration_minutes must be >= 1")
        return ScheduleDurationValue.model_validate({"duration_minutes": value}).duration_minutes

    @field_validator("remind_at", "remind_start_time")
    @classmethod
    def validate_optional_datetime_fields(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return OptionalScheduleDateTimeValue.model_validate({"value": value}).value

    @field_validator("interval_minutes", mode="before")
    @classmethod
    def normalize_interval_minutes(cls, value: Any) -> int | None:
        if value is None:
            raise ValueError("interval_minutes must be >= 1")
        return PositiveIntValue.model_validate({"value": value}).value

    @field_validator("times", mode="before")
    @classmethod
    def normalize_times(cls, value: Any) -> int | None:
        if value is None:
            raise ValueError("times must be -1 or >= 2")
        return ScheduleRepeatTimesValue.model_validate({"value": value}).value


class ScheduleListArgs(ThoughtToolArgsBase):
    tag: str | None = Field(default=None, description="标签过滤；不传/null 表示不过滤标签。")

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str | None:
        return OptionalTagValue.model_validate({"tag": value}).tag


class ScheduleViewArgs(ThoughtToolArgsBase):
    view: Literal["day", "week", "month"] = Field(description="日历视图：day/week/month。")
    anchor: str | None = Field(
        default=None,
        description="视图锚点；day/week 用 YYYY-MM-DD，month 用 YYYY-MM；不传/null 表示当前时间。",
    )
    tag: str | None = Field(default=None, description="标签过滤；不传/null 表示不过滤标签。")

    @field_validator("view", mode="before")
    @classmethod
    def normalize_view(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str | None:
        return OptionalTagValue.model_validate({"tag": value}).tag

    @model_validator(mode="after")
    def validate_anchor(self) -> ScheduleViewArgs:
        try:
            normalized = ScheduleViewAnchorValue.model_validate({"view": self.view, "anchor": self.anchor}).anchor
        except ValidationError as exc:
            raise ValueError("anchor must match view") from exc
        object.__setattr__(self, "anchor", normalized)
        return self


class ScheduleIdArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1, description="ID，正整数。")


class ScheduleUpdateArgs(ScheduleAddArgs):
    id: int = Field(ge=1, description="日程 ID，正整数。")


class ScheduleRepeatArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1, description="日程 ID，正整数。")
    enabled: bool = Field(description="重复规则开关：true=开启，false=关闭。")


class TimerListArgs(ThoughtToolArgsBase):
    pass


class TimerIdArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1, description="定时任务 ID，正整数。")


class TimerAddArgs(ThoughtToolArgsBase):
    task_name: str = Field(min_length=1, description="通用定时任务名称，不能为空且应唯一。")
    cron_expr: str = Field(min_length=1, description="cron 表达式，需可被 croniter 解析。")
    prompt: str = Field(min_length=1, description="到点后送入 planner 的任务提示词。")
    run_limit: int = Field(default=-1, description="剩余执行次数；-1 表示无限，0 表示禁用，>0 表示有限次。")

    @field_validator("task_name", "prompt", mode="before")
    @classmethod
    def normalize_required_text_fields(cls, value: Any, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{field_name} is required")
        return text

    @field_validator("cron_expr", mode="before")
    @classmethod
    def normalize_cron_expr(cls, value: Any) -> str:
        return normalize_scheduled_task_cron_expr(value, field_name="cron_expr")

    @field_validator("run_limit", mode="before")
    @classmethod
    def normalize_run_limit(cls, value: Any) -> int:
        if value is None:
            raise ValueError("run_limit must be -1 or >= 0")
        return normalize_scheduled_task_run_limit(value, field_name="run_limit")


class TimerUpdateArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1, description="定时任务 ID，正整数。")
    task_name: str | None = Field(default=None, description="更新后的通用定时任务名称。")
    cron_expr: str | None = Field(default=None, description="更新后的 cron 表达式。")
    prompt: str | None = Field(default=None, description="更新后的 planner 提示词。")
    run_limit: int | None = Field(
        default=None,
        description="更新后的剩余执行次数；-1 表示无限，0 表示禁用，>0 表示有限次。",
    )

    @field_validator("task_name", "prompt", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: Any, info: object) -> str | None:
        if value is None:
            raise ValueError(f"{getattr(info, 'field_name', 'text')} cannot be null")
        text = str(value).strip()
        if not text:
            raise ValueError(f"{getattr(info, 'field_name', 'text')} is required")
        return text

    @field_validator("cron_expr", mode="before")
    @classmethod
    def normalize_optional_cron_expr(cls, value: Any) -> str | None:
        if value is None:
            raise ValueError("cron_expr cannot be null")
        return normalize_scheduled_task_cron_expr(value, field_name="cron_expr")

    @field_validator("run_limit", mode="before")
    @classmethod
    def normalize_optional_run_limit(cls, value: Any) -> int | None:
        if value is None:
            raise ValueError("run_limit must be -1 or >= 0")
        return normalize_scheduled_task_run_limit(value, field_name="run_limit")

    @model_validator(mode="after")
    def validate_has_mutation_fields(self) -> TimerUpdateArgs:
        mutable_fields = {"task_name", "cron_expr", "prompt", "run_limit"}
        if not (mutable_fields & self.model_fields_set):
            raise ValueError("timer.update 至少需要提供一个可更新字段。")
        return self


class HistoryListArgs(ThoughtToolArgsBase):
    limit: int | None = Field(default=None, ge=1, description="返回结果上限；传值时需为正整数。")

    @field_validator("limit", mode="before")
    @classmethod
    def normalize_limit(cls, value: Any) -> int | None:
        if value is None:
            return None
        return HistoryListLimitValue.model_validate({"limit": value}).limit


class HistorySearchArgs(HistoryListArgs):
    keyword: str = Field(min_length=1, description="检索关键词文本。")


class ThoughtsAddArgs(ThoughtToolArgsBase):
    content: str = Field(min_length=1, description="想法内容文本，不能为空。")

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> str:
        return ThoughtContentValue.model_validate({"content": value}).content


class ThoughtsListArgs(ThoughtToolArgsBase):
    status: Literal["未完成", "完成", "删除"] | None = Field(
        default=None,
        description="状态过滤；不传/null 时默认只看未完成与完成。",
    )

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> str | None:
        return OptionalThoughtStatusValue.model_validate({"status": value}).status


class ThoughtsIdArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1, description="想法 ID，正整数。")


class ThoughtsUpdateArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1, description="想法 ID，正整数。")
    content: str = Field(min_length=1, description="更新后的想法内容文本，不能为空。")
    status: Literal["未完成", "完成", "删除"] | None = Field(
        default=None,
        description="更新后的状态；不传/null 时保持原状态。",
    )

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> str:
        return ThoughtContentValue.model_validate({"content": value}).content

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> str | None:
        if value is None:
            raise ValueError("status must be one of 未完成, 完成, 删除")
        return OptionalThoughtStatusValue.model_validate({"status": value}).status


class UserProfileGetArgs(ThoughtToolArgsBase):
    pass


class UserProfileOverwriteArgs(ThoughtToolArgsBase):
    content: str = Field(description="完整 user_profile 文本；允许空字符串，表示清空文件。")

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> str:
        if value is None:
            raise ValueError("content is required")
        return str(value)


class InternetSearchArgs(ThoughtToolArgsBase):
    query: str = Field(min_length=1, description="搜索关键词文本。")
    freshness: str | None = Field(
        default=None,
        description=(
            "可选时效过滤。支持 noLimit|oneYear|oneMonth|oneWeek|oneDay，"
            "或 YYYY-MM-DD、YYYY-MM-DD..YYYY-MM-DD。"
        ),
    )

    @field_validator("freshness", mode="before")
    @classmethod
    def normalize_freshness(cls, value: Any) -> str | None:
        return normalize_bocha_freshness(value, field_name="freshness")


class InternetSearchFetchUrlArgs(ThoughtToolArgsBase):
    url: str = Field(min_length=1, description="目标网页 URL，需为 http:// 或 https:// 开头。")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return HttpUrlValue.model_validate({"url": value}).url


class SystemDateArgs(ThoughtToolArgsBase):
    pass


class AskUserArgs(ThoughtToolArgsBase):
    question: str = Field(min_length=1, description="单个澄清问题文本。")


class DoneArgs(ThoughtToolArgsBase):
    response: str = Field(min_length=1, description="当前子任务结论文本。")

THOUGHT_TOOL_ARGS_MODELS: dict[str, type[ThoughtToolArgsBase]] = {
    "schedule_add": ScheduleAddArgs,
    "schedule_list": ScheduleListArgs,
    "schedule_view": ScheduleViewArgs,
    "schedule_get": ScheduleIdArgs,
    "schedule_update": ScheduleUpdateArgs,
    "schedule_delete": ScheduleIdArgs,
    "schedule_repeat": ScheduleRepeatArgs,
    "timer_add": TimerAddArgs,
    "timer_list": TimerListArgs,
    "timer_get": TimerIdArgs,
    "timer_update": TimerUpdateArgs,
    "timer_delete": TimerIdArgs,
    "history_list": HistoryListArgs,
    "history_search": HistorySearchArgs,
    "thoughts_add": ThoughtsAddArgs,
    "thoughts_list": ThoughtsListArgs,
    "thoughts_get": ThoughtsIdArgs,
    "thoughts_update": ThoughtsUpdateArgs,
    "thoughts_delete": ThoughtsIdArgs,
    "user_profile_get": UserProfileGetArgs,
    "user_profile_overwrite": UserProfileOverwriteArgs,
    "internet_search_tool": InternetSearchArgs,
    "internet_search_fetch_url": InternetSearchFetchUrlArgs,
    "system_date": SystemDateArgs,
    "ask_user": AskUserArgs,
    "done": DoneArgs,
}


def validate_thought_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> ThoughtToolArgsBase | None:
    model_cls = THOUGHT_TOOL_ARGS_MODELS.get(tool_name)
    if model_cls is None:
        return None
    try:
        return model_cls.model_validate(arguments)
    except ValidationError:
        return None


__all__ = [
    "AskUserArgs",
    "DoneArgs",
    "HistoryListArgs",
    "HistorySearchArgs",
    "InternetSearchArgs",
    "InternetSearchFetchUrlArgs",
    "ScheduleAddArgs",
    "ScheduleIdArgs",
    "ScheduleListArgs",
    "ScheduleRepeatArgs",
    "ScheduleUpdateArgs",
    "ScheduleViewArgs",
    "SystemDateArgs",
    "TimerAddArgs",
    "TimerIdArgs",
    "TimerListArgs",
    "TimerUpdateArgs",
    "THOUGHT_TOOL_ARGS_MODELS",
    "ThoughtToolArgsBase",
    "ThoughtsAddArgs",
    "ThoughtsIdArgs",
    "ThoughtsListArgs",
    "ThoughtsUpdateArgs",
    "UserProfileGetArgs",
    "UserProfileOverwriteArgs",
    "validate_thought_tool_arguments",
]
