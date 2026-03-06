from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, TypeAdapter, ValidationError, field_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import EVENT_TIME_FORMAT, _validate_datetime_text, _validate_http_url_text

_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, Any])
_THOUGHT_STATUS_VALUES = ("未完成", "完成", "删除")


class ThoughtToolArgsBase(FrozenModel):
    current_step: str = ""


class ScheduleAddArgs(ThoughtToolArgsBase):
    event_time: str
    title: str = Field(min_length=1)
    tag: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    remind_at: str | None = None
    interval_minutes: int | None = Field(default=None, ge=1)
    times: int | None = None
    remind_start_time: str | None = None

    @field_validator("event_time")
    @classmethod
    def validate_event_time(cls, value: str) -> str:
        return _validate_datetime_text(value, field_name="event_time", formats=(EVENT_TIME_FORMAT,))

    @field_validator("remind_at", "remind_start_time")
    @classmethod
    def validate_optional_datetime_fields(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "datetime")
        return _validate_datetime_text(value, field_name=field_name, formats=(EVENT_TIME_FORMAT,))

    @field_validator("times")
    @classmethod
    def validate_times(cls, value: int | None) -> int | None:
        if value is None or value == -1 or value >= 2:
            return value
        raise ValueError("times must be -1 or >= 2")


class ScheduleListArgs(ThoughtToolArgsBase):
    tag: str | None = None


class ScheduleViewArgs(ThoughtToolArgsBase):
    view: Literal["day", "week", "month"]
    anchor: str | None = None
    tag: str | None = None


class ScheduleIdArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1)


class ScheduleUpdateArgs(ScheduleAddArgs):
    id: int = Field(ge=1)


class ScheduleRepeatArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1)
    enabled: bool


class HistoryListArgs(ThoughtToolArgsBase):
    limit: int | None = Field(default=None, ge=1)


class HistorySearchArgs(HistoryListArgs):
    keyword: str = Field(min_length=1)


class ThoughtsAddArgs(ThoughtToolArgsBase):
    content: str = Field(min_length=1)


class ThoughtsListArgs(ThoughtToolArgsBase):
    status: Literal["未完成", "完成", "删除"] | None = None


class ThoughtsIdArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1)


class ThoughtsUpdateArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1)
    content: str = Field(min_length=1)
    status: Literal["未完成", "完成", "删除"] | None = None


class InternetSearchArgs(ThoughtToolArgsBase):
    query: str = Field(min_length=1)


class InternetSearchFetchUrlArgs(ThoughtToolArgsBase):
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_http_url_text(value, field_name="url")


class AskUserArgs(ThoughtToolArgsBase):
    question: str = Field(min_length=1)


class DoneArgs(ThoughtToolArgsBase):
    response: str = Field(min_length=1)


class ProactiveToolArgsBase(FrozenModel):
    pass


class ProactiveScheduleListArgs(ProactiveToolArgsBase):
    tag: str | None = None


class ProactiveScheduleViewArgs(ProactiveToolArgsBase):
    view: Literal["day", "week", "month"]
    anchor: str | None = None
    tag: str | None = None


class ProactiveScheduleGetArgs(ProactiveToolArgsBase):
    id: int = Field(ge=1)


class ProactiveHistoryListArgs(ProactiveToolArgsBase):
    limit: int | None = Field(default=None, ge=1, le=200)


class ProactiveHistorySearchArgs(ProactiveHistoryListArgs):
    keyword: str = Field(min_length=1)


class ProactiveInternetSearchArgs(ProactiveToolArgsBase):
    query: str = Field(min_length=1)


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


def parse_json_object(raw_arguments: Any) -> dict[str, Any] | None:
    if isinstance(raw_arguments, dict):
        payload = raw_arguments
    elif isinstance(raw_arguments, str):
        text = raw_arguments.strip()
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None
    else:
        return None
    try:
        return _JSON_OBJECT_ADAPTER.validate_python(payload)
    except ValidationError:
        return None


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
    "ProactiveHistoryListArgs",
    "ProactiveHistorySearchArgs",
    "ProactiveInternetSearchArgs",
    "ProactiveScheduleGetArgs",
    "ProactiveScheduleListArgs",
    "ProactiveScheduleViewArgs",
    "ScheduleAddArgs",
    "ScheduleIdArgs",
    "ScheduleListArgs",
    "ScheduleRepeatArgs",
    "ScheduleUpdateArgs",
    "ScheduleViewArgs",
    "ThoughtsAddArgs",
    "ThoughtsIdArgs",
    "ThoughtsListArgs",
    "ThoughtsUpdateArgs",
    "parse_json_object",
    "validate_proactive_tool_arguments",
    "validate_thought_tool_arguments",
]
