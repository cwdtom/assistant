from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Literal

from pydantic import Field, TypeAdapter, ValidationError, field_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import EVENT_TIME_FORMAT, HttpUrlValue, _validate_datetime_text

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
    tag: str | None = Field(default=None, description="标签过滤；不传/null 表示不过滤标签。")


class ScheduleViewArgs(ThoughtToolArgsBase):
    view: Literal["day", "week", "month"] = Field(
        description="日历视图：day/week/month。"
    )
    anchor: str | None = Field(
        default=None,
        description="视图锚点；day/week 用 YYYY-MM-DD，month 用 YYYY-MM；不传/null 表示当前时间。",
    )
    tag: str | None = Field(default=None, description="标签过滤；不传/null 表示不过滤标签。")


class ScheduleIdArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1, description="ID，正整数。")


class ScheduleUpdateArgs(ScheduleAddArgs):
    id: int = Field(ge=1, description="日程 ID，正整数。")


class ScheduleRepeatArgs(ThoughtToolArgsBase):
    id: int = Field(ge=1, description="日程 ID，正整数。")
    enabled: bool = Field(description="重复规则开关：true=开启，false=关闭。")


class HistoryListArgs(ThoughtToolArgsBase):
    limit: int | None = Field(default=None, ge=1, description="返回结果上限；传值时需为正整数。")


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


class ProactiveScheduleViewArgs(ProactiveToolArgsBase):
    view: Literal["day", "week", "month"] = Field(description="日历视图：day/week/month。")
    anchor: str | None = Field(default=None, description="YYYY-MM-DD 或 YYYY-MM。")
    tag: str | None = Field(default=None, description="标签过滤；不传/null 表示不过滤标签。")


class ProactiveScheduleGetArgs(ProactiveToolArgsBase):
    id: int = Field(ge=1, description="日程 ID，正整数。")


class ProactiveHistoryListArgs(ProactiveToolArgsBase):
    limit: int | None = Field(default=None, ge=1, le=200, description="返回结果上限；取值范围 1~200。")


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
    "build_function_tool_schema",
    "parse_json_object",
    "validate_proactive_tool_arguments",
    "validate_thought_tool_arguments",
]
