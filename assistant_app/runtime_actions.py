from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from assistant_app.schemas.commands import parse_tool_command_payload
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import (
    InternetSearchArgs,
    InternetSearchFetchUrlArgs,
    coerce_history_action_payload,
    coerce_internet_search_action_payload,
    coerce_schedule_action_payload,
    coerce_system_action_payload,
    coerce_thoughts_action_payload,
    coerce_timer_action_payload,
    coerce_user_profile_action_payload,
    parse_json_object,
)

_ACTION_TOOL_BY_TOOL_NAME: dict[str, str] = {
    "schedule_add": "schedule",
    "schedule_list": "schedule",
    "schedule_view": "schedule",
    "schedule_get": "schedule",
    "schedule_update": "schedule",
    "schedule_delete": "schedule",
    "schedule_repeat": "schedule",
    "timer_add": "timer",
    "timer_list": "timer",
    "timer_get": "timer",
    "timer_update": "timer",
    "timer_delete": "timer",
    "history_list": "history",
    "history_search": "history",
    "thoughts_add": "thoughts",
    "thoughts_list": "thoughts",
    "thoughts_get": "thoughts",
    "thoughts_update": "thoughts",
    "thoughts_delete": "thoughts",
    "user_profile_get": "user_profile",
    "user_profile_overwrite": "user_profile",
    "system_date": "system",
    "internet_search_tool": "internet_search",
    "internet_search_fetch_url": "internet_search",
}

_COMPAT_ACTION_BY_TOOL_NAME: dict[str, str] = {
    "schedule_add": "add",
    "schedule_list": "list",
    "schedule_view": "view",
    "schedule_get": "get",
    "schedule_update": "update",
    "schedule_delete": "delete",
    "schedule_repeat": "repeat",
    "timer_add": "add",
    "timer_list": "list",
    "timer_get": "get",
    "timer_update": "update",
    "timer_delete": "delete",
    "history_list": "list",
    "history_search": "search",
    "thoughts_add": "add",
    "thoughts_list": "list",
    "thoughts_get": "get",
    "thoughts_update": "update",
    "thoughts_delete": "delete",
    "user_profile_get": "get",
    "user_profile_overwrite": "overwrite",
    "system_date": "date",
    "internet_search_tool": "search",
    "internet_search_fetch_url": "fetch_url",
}

_COMPAT_FIELDS_BY_TOOL_NAME: dict[str, tuple[str, ...]] = {
    "schedule_add": (
        "event_time",
        "title",
        "tag",
        "duration_minutes",
        "remind_at",
        "interval_minutes",
        "times",
        "remind_start_time",
    ),
    "schedule_list": ("tag",),
    "schedule_view": ("view", "anchor", "tag"),
    "schedule_get": ("id",),
    "schedule_update": (
        "id",
        "event_time",
        "title",
        "tag",
        "duration_minutes",
        "remind_at",
        "interval_minutes",
        "times",
        "remind_start_time",
    ),
    "schedule_delete": ("id",),
    "schedule_repeat": ("id", "enabled"),
    "timer_add": ("task_name", "cron_expr", "prompt", "run_limit"),
    "timer_list": (),
    "timer_get": ("id",),
    "timer_update": ("id", "task_name", "cron_expr", "prompt", "run_limit"),
    "timer_delete": ("id",),
    "history_list": ("limit",),
    "history_search": ("keyword", "limit"),
    "thoughts_add": ("content",),
    "thoughts_list": ("status",),
    "thoughts_get": ("id",),
    "thoughts_update": ("id", "content", "status"),
    "thoughts_delete": ("id",),
    "user_profile_get": (),
    "user_profile_overwrite": ("content",),
    "system_date": (),
    "internet_search_tool": ("query", "freshness"),
    "internet_search_fetch_url": ("url",),
}


def runtime_action_tool_for_payload(payload: RuntimePlannerActionPayload) -> str | None:
    return _ACTION_TOOL_BY_TOOL_NAME.get(payload.tool_name)


def serialize_runtime_action_input(*, action_tool: str, payload: RuntimePlannerActionPayload) -> str:
    payload_tool = runtime_action_tool_for_payload(payload)
    if payload_tool != action_tool:
        raise ValueError("payload tool does not match action tool")

    if action_tool in {"schedule", "timer", "history", "thoughts", "user_profile", "system", "internet_search"}:
        compat_action = _COMPAT_ACTION_BY_TOOL_NAME.get(payload.tool_name)
        if compat_action is None:
            raise ValueError("unsupported compat action payload")
        arguments = _payload_arguments(payload)
        compat_payload: dict[str, Any] = {"action": compat_action}
        for field_name in _COMPAT_FIELDS_BY_TOOL_NAME.get(payload.tool_name, ()):
            if field_name in arguments:
                compat_payload[field_name] = arguments[field_name]
        return json.dumps(compat_payload, ensure_ascii=False, separators=(",", ":"))

    raise ValueError("unsupported runtime action payload")


def coerce_runtime_action_payload(*, action_tool: str, raw_input: str) -> RuntimePlannerActionPayload | None:
    normalized_input = raw_input.strip()
    if not normalized_input:
        return None

    if action_tool in {"schedule", "timer", "history", "thoughts", "user_profile", "system", "internet_search"}:
        if normalized_input.startswith("/"):
            command_payload = parse_tool_command_payload(normalized_input)
            if command_payload is None:
                return None
            if runtime_action_tool_for_payload(command_payload) != action_tool:
                return None
            return command_payload

        parsed_payload = parse_json_object(normalized_input)
        if isinstance(parsed_payload, dict):
            try:
                if action_tool == "schedule":
                    return coerce_schedule_action_payload(parsed_payload)
                if action_tool == "timer":
                    return coerce_timer_action_payload(parsed_payload)
                if action_tool == "history":
                    return coerce_history_action_payload(parsed_payload)
                if action_tool == "user_profile":
                    return coerce_user_profile_action_payload(parsed_payload)
                if action_tool == "system":
                    return coerce_system_action_payload(parsed_payload)
                if action_tool == "internet_search":
                    return coerce_internet_search_action_payload(parsed_payload)
                return coerce_thoughts_action_payload(parsed_payload)
            except (ValidationError, ValueError):
                if action_tool != "internet_search":
                    return None
                if _looks_like_json_container(normalized_input):
                    return None
        if action_tool == "internet_search":
            try:
                return RuntimePlannerActionPayload(
                    tool_name="internet_search_fetch_url",
                    arguments=InternetSearchFetchUrlArgs(url=normalized_input),
                )
            except ValidationError:
                try:
                    return RuntimePlannerActionPayload(
                        tool_name="internet_search_tool",
                        arguments=InternetSearchArgs(query=normalized_input),
                    )
                except ValidationError:
                    return None

    return None


def _payload_arguments(payload: RuntimePlannerActionPayload) -> dict[str, Any]:
    model_dump = getattr(payload.arguments, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            return dumped
    if isinstance(payload.arguments, dict):
        return dict(payload.arguments)
    return {}


def _looks_like_json_container(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


__all__ = [
    "coerce_runtime_action_payload",
    "runtime_action_tool_for_payload",
    "serialize_runtime_action_input",
]
