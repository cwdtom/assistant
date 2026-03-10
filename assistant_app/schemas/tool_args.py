from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import HttpUrlValue
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


class InternetSearchArgs(ThoughtToolArgsBase):
    query: str = Field(min_length=1, description="搜索关键词文本。")


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


class ProactiveToolArgsBase(FrozenModel):
    pass


class ProactiveScheduleListArgs(ProactiveToolArgsBase):
    tag: str | None = Field(default=None, description="标签过滤；不传/null 表示不过滤标签。")

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str | None:
        return OptionalTagValue.model_validate({"tag": value}).tag


class ProactiveScheduleViewArgs(ProactiveToolArgsBase):
    view: Literal["day", "week", "month"] = Field(description="日历视图：day/week/month。")
    anchor: str | None = Field(default=None, description="YYYY-MM-DD 或 YYYY-MM。")
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
    def validate_anchor(self) -> ProactiveScheduleViewArgs:
        try:
            normalized = ScheduleViewAnchorValue.model_validate({"view": self.view, "anchor": self.anchor}).anchor
        except ValidationError as exc:
            raise ValueError("anchor must match view") from exc
        object.__setattr__(self, "anchor", normalized)
        return self


class ProactiveScheduleGetArgs(ProactiveToolArgsBase):
    id: int = Field(ge=1, description="日程 ID，正整数。")


class ProactiveHistoryListArgs(ProactiveToolArgsBase):
    limit: int | None = Field(default=None, ge=1, le=200, description="返回结果上限；取值范围 1~200。")

    @field_validator("limit", mode="before")
    @classmethod
    def normalize_limit(cls, value: Any) -> int | None:
        if value is None:
            return None
        return HistoryListLimitValue.model_validate({"limit": value}).limit


class ProactiveHistorySearchArgs(ProactiveHistoryListArgs):
    keyword: str = Field(min_length=1, description="检索关键词文本。")


class ProactiveInternetSearchArgs(ProactiveToolArgsBase):
    query: str = Field(min_length=1, description="搜索关键词文本。")


THOUGHT_TOOL_ARGS_MODELS: dict[str, type[ThoughtToolArgsBase]] = {
    "schedule_add": ScheduleAddArgs,
    "schedule_list": ScheduleListArgs,
    "schedule_view": ScheduleViewArgs,
    "schedule_get": ScheduleIdArgs,
    "schedule_update": ScheduleUpdateArgs,
    "schedule_delete": ScheduleIdArgs,
    "schedule_repeat": ScheduleRepeatArgs,
    "history_list": HistoryListArgs,
    "history_search": HistorySearchArgs,
    "thoughts_add": ThoughtsAddArgs,
    "thoughts_list": ThoughtsListArgs,
    "thoughts_get": ThoughtsIdArgs,
    "thoughts_update": ThoughtsUpdateArgs,
    "thoughts_delete": ThoughtsIdArgs,
    "internet_search_tool": InternetSearchArgs,
    "internet_search_fetch_url": InternetSearchFetchUrlArgs,
    "system_date": SystemDateArgs,
    "ask_user": AskUserArgs,
    "done": DoneArgs,
}

PROACTIVE_TOOL_ARGS_MODELS: dict[str, type[ProactiveToolArgsBase]] = {
    "schedule_list": ProactiveScheduleListArgs,
    "schedule_view": ProactiveScheduleViewArgs,
    "schedule_get": ProactiveScheduleGetArgs,
    "history_list": ProactiveHistoryListArgs,
    "history_search": ProactiveHistorySearchArgs,
    "internet_search": ProactiveInternetSearchArgs,
}


def validate_thought_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> ThoughtToolArgsBase | None:
    model_cls = THOUGHT_TOOL_ARGS_MODELS.get(tool_name)
    if model_cls is None:
        return None
    try:
        return model_cls.model_validate(arguments)
    except ValidationError:
        return None


def validate_proactive_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> ProactiveToolArgsBase | None:
    model_cls = PROACTIVE_TOOL_ARGS_MODELS.get(tool_name)
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
    "PROACTIVE_TOOL_ARGS_MODELS",
    "ProactiveHistoryListArgs",
    "ProactiveHistorySearchArgs",
    "ProactiveInternetSearchArgs",
    "ProactiveScheduleGetArgs",
    "ProactiveScheduleListArgs",
    "ProactiveScheduleViewArgs",
    "ProactiveToolArgsBase",
    "ScheduleAddArgs",
    "ScheduleIdArgs",
    "ScheduleListArgs",
    "ScheduleRepeatArgs",
    "ScheduleUpdateArgs",
    "ScheduleViewArgs",
    "SystemDateArgs",
    "THOUGHT_TOOL_ARGS_MODELS",
    "ThoughtToolArgsBase",
    "ThoughtsAddArgs",
    "ThoughtsIdArgs",
    "ThoughtsListArgs",
    "ThoughtsUpdateArgs",
    "validate_proactive_tool_arguments",
    "validate_thought_tool_arguments",
]
