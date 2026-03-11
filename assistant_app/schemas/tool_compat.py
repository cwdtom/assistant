from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tool_args import (
    HistoryListArgs,
    HistorySearchArgs,
    InternetSearchArgs,
    InternetSearchFetchUrlArgs,
    ScheduleAddArgs,
    ScheduleIdArgs,
    ScheduleListArgs,
    ScheduleRepeatArgs,
    ScheduleUpdateArgs,
    ScheduleViewArgs,
    SystemDateArgs,
    ThoughtsAddArgs,
    ThoughtsIdArgs,
    ThoughtsListArgs,
    ThoughtsUpdateArgs,
    TimerAddArgs,
    TimerIdArgs,
    TimerListArgs,
    TimerUpdateArgs,
    UserProfileGetArgs,
    UserProfileOverwriteArgs,
)
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

_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, Any])


def _normalize_action_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw_payload)
    action = payload.get("action")
    if isinstance(action, str):
        payload["action"] = action.strip().lower()
    return payload


def _normalize_required_text(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


class HistoryListCompatPayload(FrozenModel):
    action: Literal["list"]
    limit: int | None = None

    @field_validator("limit", mode="before")
    @classmethod
    def normalize_limit(cls, value: Any) -> int | None:
        if value is None:
            return None
        return HistoryListLimitValue.model_validate({"limit": value}).limit

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        arguments: dict[str, Any] = {}
        if "limit" in self.model_fields_set:
            arguments["limit"] = self.limit
        return RuntimePlannerActionPayload(
            tool_name="history_list",
            arguments=HistoryListArgs.model_validate(arguments),
        )


class HistorySearchCompatPayload(FrozenModel):
    action: Literal["search"]
    keyword: str = Field(min_length=1)
    limit: int | None = None

    @field_validator("keyword", mode="before")
    @classmethod
    def normalize_keyword(cls, value: Any) -> str:
        return _normalize_required_text(value, field_name="keyword")

    @field_validator("limit", mode="before")
    @classmethod
    def normalize_limit(cls, value: Any) -> int | None:
        if value is None:
            return None
        return HistoryListLimitValue.model_validate({"limit": value}).limit

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        arguments: dict[str, Any] = {"keyword": self.keyword}
        if "limit" in self.model_fields_set:
            arguments["limit"] = self.limit
        return RuntimePlannerActionPayload(
            tool_name="history_search",
            arguments=HistorySearchArgs.model_validate(arguments),
        )


class ScheduleListCompatPayload(FrozenModel):
    action: Literal["list"]
    tag: str | None = None

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str | None:
        return OptionalTagValue.model_validate({"tag": value}).tag

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        arguments: dict[str, Any] = {}
        if "tag" in self.model_fields_set:
            arguments["tag"] = self.tag
        return RuntimePlannerActionPayload(
            tool_name="schedule_list",
            arguments=ScheduleListArgs.model_validate(arguments),
        )


class ScheduleViewCompatPayload(FrozenModel):
    action: Literal["view"]
    view: Literal["day", "week", "month"]
    anchor: str | None = None
    tag: str | None = None

    @field_validator("view", mode="before")
    @classmethod
    def normalize_view(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("anchor", mode="before")
    @classmethod
    def normalize_anchor(cls, value: Any) -> str | None:
        if value is None:
            return None
        return str(value).strip() or None

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str | None:
        return OptionalTagValue.model_validate({"tag": value}).tag

    @model_validator(mode="after")
    def validate_anchor(self) -> ScheduleViewCompatPayload:
        try:
            normalized = ScheduleViewAnchorValue.model_validate({"view": self.view, "anchor": self.anchor}).anchor
        except ValidationError as exc:
            raise ValueError("anchor must match view") from exc
        object.__setattr__(self, "anchor", normalized)
        return self

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        arguments: dict[str, Any] = {"view": self.view}
        if "anchor" in self.model_fields_set:
            arguments["anchor"] = self.anchor
        if "tag" in self.model_fields_set:
            arguments["tag"] = self.tag
        return RuntimePlannerActionPayload(
            tool_name="schedule_view",
            arguments=ScheduleViewArgs.model_validate(arguments),
        )


class ScheduleIdCompatPayload(FrozenModel):
    action: Literal["get", "delete"]
    id: int = Field(ge=1)

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: Any) -> int:
        return PositiveIntValue.model_validate({"value": value}).value

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        tool_name = "schedule_get" if self.action == "get" else "schedule_delete"
        return RuntimePlannerActionPayload(
            tool_name=tool_name,
            arguments=ScheduleIdArgs(id=self.id),
        )


class _ScheduleMutationCompatPayloadBase(FrozenModel):
    event_time: str
    title: str = Field(min_length=1)
    tag: str | None = None
    duration_minutes: int | None = None
    remind_at: str | None = None
    interval_minutes: int | None = None
    times: int | None = None
    remind_start_time: str | None = None

    @field_validator("event_time", mode="before")
    @classmethod
    def normalize_event_time(cls, value: Any) -> str:
        return ScheduleDateTimeValue.model_validate({"value": value}).value

    @field_validator("title", mode="before")
    @classmethod
    def normalize_title(cls, value: Any) -> str:
        return _normalize_required_text(value, field_name="title")

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

    @field_validator("interval_minutes", mode="before")
    @classmethod
    def normalize_interval_minutes(cls, value: Any) -> int | None:
        if value is None:
            raise ValueError("interval_minutes must be >= 1")
        return PositiveIntValue.model_validate({"value": value}).value

    @field_validator("remind_at", "remind_start_time", mode="before")
    @classmethod
    def normalize_optional_datetime(cls, value: Any) -> str | None:
        if value is None:
            return None
        return OptionalScheduleDateTimeValue.model_validate({"value": value}).value

    @field_validator("times", mode="before")
    @classmethod
    def normalize_times(cls, value: Any) -> int | None:
        if value is None:
            raise ValueError("times must be -1 or >= 2")
        return ScheduleRepeatTimesValue.model_validate({"value": value}).value

    def _build_arguments(self) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "event_time": self.event_time,
            "title": self.title,
        }
        for field_name in (
            "tag",
            "duration_minutes",
            "remind_at",
            "interval_minutes",
            "times",
            "remind_start_time",
        ):
            if field_name in self.model_fields_set:
                arguments[field_name] = getattr(self, field_name)
        return arguments


class ScheduleAddCompatPayload(_ScheduleMutationCompatPayloadBase):
    action: Literal["add"]

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(
            tool_name="schedule_add",
            arguments=ScheduleAddArgs.model_validate(self._build_arguments()),
        )


class ScheduleUpdateCompatPayload(_ScheduleMutationCompatPayloadBase):
    action: Literal["update"]
    id: int = Field(ge=1)

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: Any) -> int:
        return PositiveIntValue.model_validate({"value": value}).value

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        arguments = {"id": self.id, **self._build_arguments()}
        return RuntimePlannerActionPayload(
            tool_name="schedule_update",
            arguments=ScheduleUpdateArgs.model_validate(arguments),
        )


class ScheduleRepeatCompatPayload(FrozenModel):
    action: Literal["repeat"]
    id: int = Field(ge=1)
    enabled: bool

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: Any) -> int:
        return PositiveIntValue.model_validate({"value": value}).value

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(
            tool_name="schedule_repeat",
            arguments=ScheduleRepeatArgs(id=self.id, enabled=self.enabled),
        )


class TimerListCompatPayload(FrozenModel):
    action: Literal["list"]

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(
            tool_name="timer_list",
            arguments=TimerListArgs(),
        )


class TimerIdCompatPayload(FrozenModel):
    action: Literal["get", "delete"]
    id: int = Field(ge=1)

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: Any) -> int:
        return PositiveIntValue.model_validate({"value": value}).value

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        tool_name = "timer_get" if self.action == "get" else "timer_delete"
        return RuntimePlannerActionPayload(
            tool_name=tool_name,
            arguments=TimerIdArgs(id=self.id),
        )


class TimerAddCompatPayload(FrozenModel):
    action: Literal["add"]
    task_name: Any
    cron_expr: Any
    prompt: Any
    run_limit: Any = -1

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(
            tool_name="timer_add",
            arguments=TimerAddArgs.model_validate(
                {
                    "task_name": self.task_name,
                    "cron_expr": self.cron_expr,
                    "prompt": self.prompt,
                    "run_limit": self.run_limit,
                }
            ),
        )


class TimerUpdateCompatPayload(FrozenModel):
    action: Literal["update"]
    id: int = Field(ge=1)
    task_name: Any | None = None
    cron_expr: Any | None = None
    prompt: Any | None = None
    run_limit: Any | None = None

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: Any) -> int:
        return PositiveIntValue.model_validate({"value": value}).value

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        arguments: dict[str, Any] = {"id": self.id}
        for field_name in ("task_name", "cron_expr", "prompt", "run_limit"):
            if field_name in self.model_fields_set:
                arguments[field_name] = getattr(self, field_name)
        return RuntimePlannerActionPayload(
            tool_name="timer_update",
            arguments=TimerUpdateArgs.model_validate(arguments),
        )


class ThoughtsAddCompatPayload(FrozenModel):
    action: Literal["add"]
    content: str = Field(min_length=1)

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("thoughts.add content 不能为空。")
        return ThoughtContentValue.model_validate({"content": text}).content

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(
            tool_name="thoughts_add",
            arguments=ThoughtsAddArgs(content=self.content),
        )


class ThoughtsListCompatPayload(FrozenModel):
    action: Literal["list"]
    status: Literal["未完成", "完成", "删除"] | None = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> str | None:
        if value is None:
            return None
        try:
            return OptionalThoughtStatusValue.model_validate({"status": value}).status
        except ValidationError as exc:
            raise ValueError("thoughts.list status 必须为 未完成|完成|删除。") from exc

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        arguments: dict[str, Any] = {}
        if "status" in self.model_fields_set and self.status is not None:
            arguments["status"] = self.status
        return RuntimePlannerActionPayload(
            tool_name="thoughts_list",
            arguments=ThoughtsListArgs.model_validate(arguments),
        )


class ThoughtsIdCompatPayload(FrozenModel):
    action: Literal["get", "delete"]
    id: int = Field(ge=1)

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: Any) -> int:
        try:
            return PositiveIntValue.model_validate({"value": value}).value
        except ValidationError as exc:
            raise ValueError("thoughts.id 必须为正整数。") from exc

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        tool_name = "thoughts_get" if self.action == "get" else "thoughts_delete"
        return RuntimePlannerActionPayload(
            tool_name=tool_name,
            arguments=ThoughtsIdArgs(id=self.id),
        )


class ThoughtsUpdateCompatPayload(FrozenModel):
    action: Literal["update"]
    id: int = Field(ge=1)
    content: str = Field(min_length=1)
    status: Literal["未完成", "完成", "删除"] | None = None

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: Any) -> int:
        try:
            return PositiveIntValue.model_validate({"value": value}).value
        except ValidationError as exc:
            raise ValueError("thoughts.id 必须为正整数。") from exc

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("thoughts.update content 不能为空。")
        return ThoughtContentValue.model_validate({"content": text}).content

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> str | None:
        if value is None:
            raise ValueError("thoughts.update status 必须为 未完成|完成|删除。")
        try:
            return OptionalThoughtStatusValue.model_validate({"status": value}).status
        except ValidationError as exc:
            raise ValueError("thoughts.update status 必须为 未完成|完成|删除。") from exc

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        arguments: dict[str, Any] = {
            "id": self.id,
            "content": self.content,
        }
        if "status" in self.model_fields_set:
            arguments["status"] = self.status
        return RuntimePlannerActionPayload(
            tool_name="thoughts_update",
            arguments=ThoughtsUpdateArgs.model_validate(arguments),
        )


class UserProfileGetCompatPayload(FrozenModel):
    action: Literal["get"]

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(
            tool_name="user_profile_get",
            arguments=UserProfileGetArgs(),
        )


class UserProfileOverwriteCompatPayload(FrozenModel):
    action: Literal["overwrite"]
    content: Any

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> str:
        if value is None:
            raise ValueError("content is required")
        return str(value)

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(
            tool_name="user_profile_overwrite",
            arguments=UserProfileOverwriteArgs.model_validate({"content": self.content}),
        )


class InternetSearchCompatPayload(FrozenModel):
    action: Literal["search"]
    query: str = Field(min_length=1)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: Any) -> str:
        return _normalize_required_text(value, field_name="query")

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(
            tool_name="internet_search_tool",
            arguments=InternetSearchArgs.model_validate({"query": self.query}),
        )


class InternetSearchFetchUrlCompatPayload(FrozenModel):
    action: Literal["fetch_url"]
    url: str = Field(min_length=1)

    @field_validator("url", mode="before")
    @classmethod
    def normalize_url(cls, value: Any) -> str:
        return _normalize_required_text(value, field_name="url")

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(
            tool_name="internet_search_fetch_url",
            arguments=InternetSearchFetchUrlArgs.model_validate({"url": self.url}),
        )


class SystemDateCompatPayload(FrozenModel):
    action: Literal["date"]

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(
            tool_name="system_date",
            arguments=SystemDateArgs(),
        )


HistoryCompatPayload = Annotated[
    HistoryListCompatPayload | HistorySearchCompatPayload,
    Field(discriminator="action"),
]
ScheduleCompatPayload = Annotated[
    ScheduleListCompatPayload
    | ScheduleViewCompatPayload
    | ScheduleIdCompatPayload
    | ScheduleAddCompatPayload
    | ScheduleUpdateCompatPayload
    | ScheduleRepeatCompatPayload,
    Field(discriminator="action"),
]
TimerCompatPayload = Annotated[
    TimerListCompatPayload
    | TimerIdCompatPayload
    | TimerAddCompatPayload
    | TimerUpdateCompatPayload,
    Field(discriminator="action"),
]
ThoughtsCompatPayload = Annotated[
    ThoughtsAddCompatPayload | ThoughtsListCompatPayload | ThoughtsIdCompatPayload | ThoughtsUpdateCompatPayload,
    Field(discriminator="action"),
]
UserProfileCompatPayload = Annotated[
    UserProfileGetCompatPayload | UserProfileOverwriteCompatPayload,
    Field(discriminator="action"),
]
InternetSearchCompatPayloadUnion = Annotated[
    InternetSearchCompatPayload | InternetSearchFetchUrlCompatPayload,
    Field(discriminator="action"),
]
SystemCompatPayload = Annotated[
    SystemDateCompatPayload,
    Field(discriminator="action"),
]

_HISTORY_COMPAT_ADAPTER: TypeAdapter[HistoryCompatPayload] = TypeAdapter(HistoryCompatPayload)
_SCHEDULE_COMPAT_ADAPTER: TypeAdapter[ScheduleCompatPayload] = TypeAdapter(ScheduleCompatPayload)
_TIMER_COMPAT_ADAPTER: TypeAdapter[TimerCompatPayload] = TypeAdapter(TimerCompatPayload)
_THOUGHTS_COMPAT_ADAPTER: TypeAdapter[ThoughtsCompatPayload] = TypeAdapter(ThoughtsCompatPayload)
_USER_PROFILE_COMPAT_ADAPTER: TypeAdapter[UserProfileCompatPayload] = TypeAdapter(UserProfileCompatPayload)
_INTERNET_SEARCH_COMPAT_ADAPTER: TypeAdapter[InternetSearchCompatPayloadUnion] = TypeAdapter(
    InternetSearchCompatPayloadUnion
)
_SYSTEM_COMPAT_ADAPTER: TypeAdapter[SystemCompatPayload] = TypeAdapter(SystemCompatPayload)


def coerce_history_action_payload(raw_payload: dict[str, Any]) -> RuntimePlannerActionPayload:
    compat_payload = _HISTORY_COMPAT_ADAPTER.validate_python(_normalize_action_payload(raw_payload))
    return compat_payload.to_runtime_payload()


def coerce_schedule_action_payload(raw_payload: dict[str, Any]) -> RuntimePlannerActionPayload:
    compat_payload = _SCHEDULE_COMPAT_ADAPTER.validate_python(_normalize_action_payload(raw_payload))
    return compat_payload.to_runtime_payload()


def coerce_timer_action_payload(raw_payload: dict[str, Any]) -> RuntimePlannerActionPayload:
    normalized_payload = _normalize_action_payload(raw_payload)
    action = normalized_payload.get("action")
    if action not in {"add", "list", "get", "update", "delete"}:
        raise ValueError("timer.action 非法。")
    compat_payload = _TIMER_COMPAT_ADAPTER.validate_python(normalized_payload)
    return compat_payload.to_runtime_payload()


def coerce_thoughts_action_payload(raw_payload: dict[str, Any]) -> RuntimePlannerActionPayload:
    normalized_payload = _normalize_action_payload(raw_payload)
    action = normalized_payload.get("action")
    if action not in {"add", "list", "get", "update", "delete"}:
        raise ValueError("thoughts.action 非法。")
    compat_payload = _THOUGHTS_COMPAT_ADAPTER.validate_python(normalized_payload)
    return compat_payload.to_runtime_payload()


def coerce_user_profile_action_payload(raw_payload: dict[str, Any]) -> RuntimePlannerActionPayload:
    normalized_payload = _normalize_action_payload(raw_payload)
    action = normalized_payload.get("action")
    if action not in {"get", "overwrite"}:
        raise ValueError("user_profile.action 非法。")
    compat_payload = _USER_PROFILE_COMPAT_ADAPTER.validate_python(normalized_payload)
    return compat_payload.to_runtime_payload()


def coerce_internet_search_action_payload(raw_payload: dict[str, Any]) -> RuntimePlannerActionPayload:
    normalized_payload = _normalize_action_payload(raw_payload)
    action = normalized_payload.get("action")
    if action not in {"search", "fetch_url"}:
        raise ValueError("internet_search.action 非法。")
    compat_payload = _INTERNET_SEARCH_COMPAT_ADAPTER.validate_python(normalized_payload)
    return compat_payload.to_runtime_payload()


def coerce_system_action_payload(raw_payload: dict[str, Any]) -> RuntimePlannerActionPayload:
    normalized_payload = _normalize_action_payload(raw_payload)
    action = normalized_payload.get("action")
    if action != "date":
        raise ValueError("system.action 非法。")
    compat_payload = _SYSTEM_COMPAT_ADAPTER.validate_python(normalized_payload)
    return compat_payload.to_runtime_payload()


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


__all__ = [
    "HistoryListCompatPayload",
    "HistorySearchCompatPayload",
    "InternetSearchCompatPayload",
    "InternetSearchFetchUrlCompatPayload",
    "ScheduleAddCompatPayload",
    "ScheduleIdCompatPayload",
    "ScheduleListCompatPayload",
    "ScheduleRepeatCompatPayload",
    "ScheduleUpdateCompatPayload",
    "ScheduleViewCompatPayload",
    "SystemDateCompatPayload",
    "TimerAddCompatPayload",
    "TimerIdCompatPayload",
    "TimerListCompatPayload",
    "TimerUpdateCompatPayload",
    "ThoughtsAddCompatPayload",
    "ThoughtsIdCompatPayload",
    "ThoughtsListCompatPayload",
    "ThoughtsUpdateCompatPayload",
    "UserProfileGetCompatPayload",
    "UserProfileOverwriteCompatPayload",
    "coerce_history_action_payload",
    "coerce_internet_search_action_payload",
    "coerce_schedule_action_payload",
    "coerce_system_action_payload",
    "coerce_timer_action_payload",
    "coerce_thoughts_action_payload",
    "coerce_user_profile_action_payload",
    "parse_json_object",
]
