from __future__ import annotations

import json
from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, ValidationError, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import HttpUrlValue
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.values import (
    HistoryListLimitValue,
    OptionalScheduleDateTimeValue,
    OptionalTagValue,
    PositiveIntValue,
    ScheduleDateTimeValue,
    ScheduleRepeatTimesValue,
    ScheduleViewAnchorValue,
)

_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, Any])
_THOUGHT_STATUS_VALUES = ("未完成", "完成", "删除")


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

    @field_validator("remind_at", "remind_start_time")
    @classmethod
    def validate_optional_datetime_fields(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return OptionalScheduleDateTimeValue.model_validate({"value": value}).value

    @field_validator("times")
    @classmethod
    def validate_times(cls, value: int | None) -> int | None:
        if value is None or value == -1 or value >= 2:
            return value
        raise ValueError("times must be -1 or >= 2")


class ScheduleListArgs(ThoughtToolArgsBase):
    tag: str | None = Field(default=None, description="标签过滤；不传/null 表示不过滤标签。")

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str | None:
        return OptionalTagValue.model_validate({"tag": value}).tag


class ScheduleViewArgs(ThoughtToolArgsBase):
    view: Literal["day", "week", "month"] = Field(
        description="日历视图：day/week/month。"
    )
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
            normalized = ScheduleViewAnchorValue.model_validate(
                {"view": self.view, "anchor": self.anchor}
            ).anchor
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


class ThoughtsListArgs(ThoughtToolArgsBase):
    status: Literal["未完成", "完成", "删除"] | None = Field(
        default=None,
        description="状态过滤；不传/null 时默认只看未完成与完成。",
    )


class ThoughtsIdArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1, description="想法 ID，正整数。")


class ThoughtsUpdateArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1, description="想法 ID，正整数。")
    content: str = Field(min_length=1, description="更新后的想法内容文本，不能为空。")
    status: Literal["未完成", "完成", "删除"] | None = Field(
        default=None,
        description="更新后的状态；不传/null 时保持原状态。",
    )


class InternetSearchArgs(ThoughtToolArgsBase):
    query: str = Field(min_length=1, description="搜索关键词文本。")


class InternetSearchFetchUrlArgs(ThoughtToolArgsBase):
    url: str = Field(min_length=1, description="目标网页 URL，需为 http:// 或 https:// 开头。")

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return HttpUrlValue.model_validate({"url": value}).url


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
            normalized = ScheduleViewAnchorValue.model_validate(
                {"view": self.view, "anchor": self.anchor}
            ).anchor
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
            normalized = ScheduleViewAnchorValue.model_validate(
                {"view": self.view, "anchor": self.anchor}
            ).anchor
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

    @field_validator("duration_minutes", "interval_minutes", mode="before")
    @classmethod
    def normalize_optional_positive_int(cls, value: Any) -> int | None:
        if value is None:
            return None
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
            return None
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

_HISTORY_COMPAT_ADAPTER: TypeAdapter[HistoryCompatPayload] = TypeAdapter(HistoryCompatPayload)
_SCHEDULE_COMPAT_ADAPTER: TypeAdapter[ScheduleCompatPayload] = TypeAdapter(ScheduleCompatPayload)


def coerce_history_action_payload(raw_payload: dict[str, Any]) -> RuntimePlannerActionPayload:
    compat_payload = _HISTORY_COMPAT_ADAPTER.validate_python(_normalize_action_payload(raw_payload))
    return compat_payload.to_runtime_payload()


def coerce_schedule_action_payload(raw_payload: dict[str, Any]) -> RuntimePlannerActionPayload:
    compat_payload = _SCHEDULE_COMPAT_ADAPTER.validate_python(_normalize_action_payload(raw_payload))
    return compat_payload.to_runtime_payload()


def _normalize_nullable_schema(schema: dict[str, Any]) -> dict[str, Any]:
    any_of = schema.get("anyOf")
    if not isinstance(any_of, list) or len(any_of) != 2:
        return schema
    null_schema = next(
        (item for item in any_of if isinstance(item, dict) and item.get("type") == "null" and len(item) == 1),
        None,
    )
    value_schema = next((item for item in any_of if item is not null_schema and isinstance(item, dict)), None)
    if null_schema is None or value_schema is None:
        return schema
    value_type = value_schema.get("type")
    if not isinstance(value_type, str):
        return schema
    normalized = deepcopy(value_schema)
    normalized["type"] = [value_type, "null"]
    return normalized


def _cleanup_json_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_cleanup_json_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key in {"title", "default"}:
            continue
        cleaned[key] = _cleanup_json_schema(value)
    return _normalize_nullable_schema(cleaned)


def build_function_tool_schema(
    *,
    name: str,
    description: str,
    arguments_model: type[FrozenModel],
    exclude_fields: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema = _cleanup_json_schema(arguments_model.model_json_schema())
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    else:
        properties = deepcopy(properties)
    required = schema.get("required")
    required_fields = [item for item in required if isinstance(item, str)] if isinstance(required, list) else []
    for field_name in exclude_fields:
        properties.pop(field_name, None)
        required_fields = [item for item in required_fields if item != field_name]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required_fields,
                "additionalProperties": False,
            },
        },
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
    "HistoryListCompatPayload",
    "HistorySearchArgs",
    "HistorySearchCompatPayload",
    "InternetSearchArgs",
    "InternetSearchFetchUrlArgs",
    "ProactiveHistoryListArgs",
    "ProactiveHistorySearchArgs",
    "ProactiveInternetSearchArgs",
    "ProactiveScheduleGetArgs",
    "ProactiveScheduleListArgs",
    "ProactiveScheduleViewArgs",
    "ScheduleAddArgs",
    "ScheduleAddCompatPayload",
    "ScheduleIdArgs",
    "ScheduleIdCompatPayload",
    "ScheduleListArgs",
    "ScheduleListCompatPayload",
    "ScheduleRepeatArgs",
    "ScheduleRepeatCompatPayload",
    "ScheduleUpdateArgs",
    "ScheduleUpdateCompatPayload",
    "ScheduleViewArgs",
    "ScheduleViewCompatPayload",
    "ThoughtsAddArgs",
    "ThoughtsIdArgs",
    "ThoughtsListArgs",
    "ThoughtsUpdateArgs",
    "build_function_tool_schema",
    "coerce_history_action_payload",
    "coerce_schedule_action_payload",
    "parse_json_object",
    "validate_proactive_tool_arguments",
    "validate_thought_tool_arguments",
]
