from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import Field, ValidationError

from assistant_app.agent_components.parsing_utils import (
    _INVALID_OPTION_VALUE,
    _parse_history_list_limit,
    _parse_history_search_input,
    _parse_positive_int,
    _parse_schedule_add_input,
    _parse_schedule_list_tag_input,
    _parse_schedule_repeat_toggle_input,
    _parse_schedule_update_input,
    _parse_schedule_view_command_input,
    _parse_thoughts_list_status_input,
    _parse_thoughts_update_input,
)
from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import (
    HistoryListArgs,
    HistorySearchArgs,
    ScheduleAddArgs,
    ScheduleIdArgs,
    ScheduleListArgs,
    ScheduleRepeatArgs,
    ScheduleUpdateArgs,
    ScheduleViewArgs,
    ThoughtsAddArgs,
    ThoughtsIdArgs,
    ThoughtsListArgs,
    ThoughtsUpdateArgs,
    coerce_history_action_payload,
    coerce_schedule_action_payload,
    coerce_thoughts_action_payload,
)


class CliCommandBase(FrozenModel):
    action_tool: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: Any

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(tool_name=self.tool_name, arguments=self.arguments)


class HistoryListCommand(CliCommandBase):
    action_tool: Literal["history"] = "history"
    tool_name: Literal["history_list"] = "history_list"
    arguments: HistoryListArgs = Field(default_factory=HistoryListArgs)


class HistorySearchCommand(CliCommandBase):
    action_tool: Literal["history"] = "history"
    tool_name: Literal["history_search"] = "history_search"
    arguments: HistorySearchArgs


class ThoughtsAddCommand(CliCommandBase):
    action_tool: Literal["thoughts"] = "thoughts"
    tool_name: Literal["thoughts_add"] = "thoughts_add"
    arguments: ThoughtsAddArgs


class ThoughtsListCommand(CliCommandBase):
    action_tool: Literal["thoughts"] = "thoughts"
    tool_name: Literal["thoughts_list"] = "thoughts_list"
    arguments: ThoughtsListArgs = Field(default_factory=ThoughtsListArgs)


class ThoughtsGetCommand(CliCommandBase):
    action_tool: Literal["thoughts"] = "thoughts"
    tool_name: Literal["thoughts_get"] = "thoughts_get"
    arguments: ThoughtsIdArgs


class ThoughtsUpdateCommand(CliCommandBase):
    action_tool: Literal["thoughts"] = "thoughts"
    tool_name: Literal["thoughts_update"] = "thoughts_update"
    arguments: ThoughtsUpdateArgs


class ThoughtsDeleteCommand(CliCommandBase):
    action_tool: Literal["thoughts"] = "thoughts"
    tool_name: Literal["thoughts_delete"] = "thoughts_delete"
    arguments: ThoughtsIdArgs


class ScheduleListCommand(CliCommandBase):
    action_tool: Literal["schedule"] = "schedule"
    tool_name: Literal["schedule_list"] = "schedule_list"
    arguments: ScheduleListArgs = Field(default_factory=ScheduleListArgs)


class ScheduleViewCommand(CliCommandBase):
    action_tool: Literal["schedule"] = "schedule"
    tool_name: Literal["schedule_view"] = "schedule_view"
    arguments: ScheduleViewArgs


class ScheduleGetCommand(CliCommandBase):
    action_tool: Literal["schedule"] = "schedule"
    tool_name: Literal["schedule_get"] = "schedule_get"
    arguments: ScheduleIdArgs


class ScheduleAddCommand(CliCommandBase):
    action_tool: Literal["schedule"] = "schedule"
    tool_name: Literal["schedule_add"] = "schedule_add"
    arguments: ScheduleAddArgs


class ScheduleUpdateCommand(CliCommandBase):
    action_tool: Literal["schedule"] = "schedule"
    tool_name: Literal["schedule_update"] = "schedule_update"
    arguments: ScheduleUpdateArgs


class ScheduleDeleteCommand(CliCommandBase):
    action_tool: Literal["schedule"] = "schedule"
    tool_name: Literal["schedule_delete"] = "schedule_delete"
    arguments: ScheduleIdArgs


class ScheduleRepeatCommand(CliCommandBase):
    action_tool: Literal["schedule"] = "schedule"
    tool_name: Literal["schedule_repeat"] = "schedule_repeat"
    arguments: ScheduleRepeatArgs


def _coerce_history_command_arguments(raw_payload: dict[str, object]) -> HistoryListArgs | HistorySearchArgs | None:
    try:
        runtime_payload = coerce_history_action_payload(raw_payload)
    except ValidationError:
        return None
    arguments = runtime_payload.arguments
    if isinstance(arguments, (HistoryListArgs, HistorySearchArgs)):
        return arguments
    return None


def _coerce_schedule_command_arguments(
    raw_payload: dict[str, object],
) -> (
    ScheduleListArgs
    | ScheduleViewArgs
    | ScheduleIdArgs
    | ScheduleAddArgs
    | ScheduleUpdateArgs
    | ScheduleRepeatArgs
    | None
):
    try:
        runtime_payload = coerce_schedule_action_payload(raw_payload)
    except ValidationError:
        return None
    arguments = runtime_payload.arguments
    if isinstance(
        arguments,
        (ScheduleListArgs, ScheduleViewArgs, ScheduleIdArgs, ScheduleAddArgs, ScheduleUpdateArgs, ScheduleRepeatArgs),
    ):
        return arguments
    return None


def _coerce_thoughts_command_arguments(
    raw_payload: dict[str, object],
) -> ThoughtsAddArgs | ThoughtsListArgs | ThoughtsIdArgs | ThoughtsUpdateArgs | None:
    try:
        runtime_payload = coerce_thoughts_action_payload(raw_payload)
    except (ValidationError, ValueError):
        return None
    arguments = runtime_payload.arguments
    if isinstance(arguments, (ThoughtsAddArgs, ThoughtsListArgs, ThoughtsIdArgs, ThoughtsUpdateArgs)):
        return arguments
    return None


def parse_history_list_command(command: str) -> HistoryListCommand | None:
    history_limit = _parse_history_list_limit(command)
    if history_limit is None:
        return None
    raw_payload: dict[str, object] = {"action": "list"}
    if command != "/history list":
        raw_payload["limit"] = history_limit
    arguments = _coerce_history_command_arguments(raw_payload)
    if not isinstance(arguments, HistoryListArgs):
        return None
    return HistoryListCommand(arguments=arguments)


def parse_history_search_command(command: str) -> HistorySearchCommand | None:
    raw = command.removeprefix("/history search ").strip()
    parsed = _parse_history_search_input(raw)
    if parsed is None:
        return None
    keyword, history_limit = parsed
    arguments = _coerce_history_command_arguments(
        {"action": "search", "keyword": keyword, "limit": history_limit}
    )
    if not isinstance(arguments, HistorySearchArgs):
        return None
    return HistorySearchCommand(arguments=arguments)


def parse_thoughts_add_command(command: str) -> ThoughtsAddCommand | None:
    content = command.removeprefix("/thoughts add").strip()
    if not content:
        return None
    arguments = _coerce_thoughts_command_arguments({"action": "add", "content": content})
    if not isinstance(arguments, ThoughtsAddArgs):
        return None
    return ThoughtsAddCommand(arguments=arguments)


def parse_thoughts_list_command(command: str) -> ThoughtsListCommand | None:
    parsed_status = _parse_thoughts_list_status_input(command.removeprefix("/thoughts list").strip())
    if parsed_status is _INVALID_OPTION_VALUE:
        return None
    raw_payload: dict[str, object] = {"action": "list"}
    if isinstance(parsed_status, str):
        normalized_status = cast(Literal["未完成", "完成", "删除"], parsed_status)
        raw_payload["status"] = normalized_status
    arguments = _coerce_thoughts_command_arguments(raw_payload)
    if not isinstance(arguments, ThoughtsListArgs):
        return None
    return ThoughtsListCommand(arguments=arguments)


def parse_thoughts_get_command(command: str) -> ThoughtsGetCommand | None:
    thought_id = _parse_positive_int(command.removeprefix("/thoughts get ").strip())
    if thought_id is None:
        return None
    arguments = _coerce_thoughts_command_arguments({"action": "get", "id": thought_id})
    if not isinstance(arguments, ThoughtsIdArgs):
        return None
    return ThoughtsGetCommand(arguments=arguments)


def parse_thoughts_update_command(command: str) -> ThoughtsUpdateCommand | None:
    parsed_update = _parse_thoughts_update_input(command.removeprefix("/thoughts update ").strip())
    if parsed_update is None:
        return None
    thought_id, content, status, has_status = parsed_update
    arguments: dict[str, object] = {
        "id": thought_id,
        "content": content,
    }
    if has_status:
        arguments["status"] = status
    coerced_arguments = _coerce_thoughts_command_arguments(arguments | {"action": "update"})
    if not isinstance(coerced_arguments, ThoughtsUpdateArgs):
        return None
    return ThoughtsUpdateCommand(arguments=coerced_arguments)


def parse_thoughts_delete_command(command: str) -> ThoughtsDeleteCommand | None:
    thought_id = _parse_positive_int(command.removeprefix("/thoughts delete ").strip())
    if thought_id is None:
        return None
    arguments = _coerce_thoughts_command_arguments({"action": "delete", "id": thought_id})
    if not isinstance(arguments, ThoughtsIdArgs):
        return None
    return ThoughtsDeleteCommand(arguments=arguments)


def parse_tool_command_payload(command: str) -> RuntimePlannerActionPayload | None:
    parsed_command: CliCommandBase | None = None
    if command == "/history list" or command.startswith("/history list "):
        parsed_command = parse_history_list_command(command)
    elif command.startswith("/history search "):
        parsed_command = parse_history_search_command(command)
    elif command == "/thoughts add" or command.startswith("/thoughts add "):
        parsed_command = parse_thoughts_add_command(command)
    elif command == "/thoughts list" or command.startswith("/thoughts list "):
        parsed_command = parse_thoughts_list_command(command)
    elif command.startswith("/thoughts get "):
        parsed_command = parse_thoughts_get_command(command)
    elif command.startswith("/thoughts update "):
        parsed_command = parse_thoughts_update_command(command)
    elif command.startswith("/thoughts delete "):
        parsed_command = parse_thoughts_delete_command(command)
    elif command == "/schedule list" or command.startswith("/schedule list "):
        parsed_command = parse_schedule_list_command(command)
    elif command.startswith("/schedule view "):
        parsed_command = parse_schedule_view_command(command)
    elif command.startswith("/schedule get "):
        parsed_command = parse_schedule_get_command(command)
    elif command.startswith("/schedule add"):
        parsed_command = parse_schedule_add_command(command)
    elif command.startswith("/schedule update "):
        parsed_command = parse_schedule_update_command(command)
    elif command.startswith("/schedule delete "):
        parsed_command = parse_schedule_delete_command(command)
    elif command.startswith("/schedule repeat "):
        parsed_command = parse_schedule_repeat_command(command)
    if parsed_command is None:
        return None
    return parsed_command.to_runtime_payload()


def parse_schedule_list_command(command: str) -> ScheduleListCommand | None:
    parsed_tag = _parse_schedule_list_tag_input(command.removeprefix("/schedule list").strip())
    if parsed_tag is _INVALID_OPTION_VALUE:
        return None
    raw_payload: dict[str, object] = {"action": "list"}
    if isinstance(parsed_tag, str):
        raw_payload["tag"] = parsed_tag
    arguments = _coerce_schedule_command_arguments(raw_payload)
    if not isinstance(arguments, ScheduleListArgs):
        return None
    return ScheduleListCommand(arguments=arguments)


def parse_schedule_view_command(command: str) -> ScheduleViewCommand | None:
    parsed_view = _parse_schedule_view_command_input(command.removeprefix("/schedule view ").strip())
    if parsed_view is None:
        return None
    view_name, anchor, tag = parsed_view
    raw_payload: dict[str, object] = {"action": "view", "view": view_name}
    if anchor is not None:
        raw_payload["anchor"] = anchor
    if tag is not None:
        raw_payload["tag"] = tag
    arguments = _coerce_schedule_command_arguments(raw_payload)
    if not isinstance(arguments, ScheduleViewArgs):
        return None
    return ScheduleViewCommand(arguments=arguments)


def parse_schedule_get_command(command: str) -> ScheduleGetCommand | None:
    schedule_id = _parse_positive_int(command.removeprefix("/schedule get ").strip())
    if schedule_id is None:
        return None
    arguments = _coerce_schedule_command_arguments({"action": "get", "id": schedule_id})
    if not isinstance(arguments, ScheduleIdArgs):
        return None
    return ScheduleGetCommand(arguments=arguments)


def parse_schedule_add_command(command: str) -> ScheduleAddCommand | None:
    parsed_add = _parse_schedule_add_input(command.removeprefix("/schedule add").strip())
    if parsed_add is None:
        return None
    (
        event_time,
        title,
        tag,
        duration_minutes,
        remind_at,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
    ) = parsed_add
    raw_payload: dict[str, object] = {
        "action": "add",
        "event_time": event_time,
        "title": title,
        "tag": tag,
        "duration_minutes": duration_minutes,
    }
    if remind_at is not None:
        raw_payload["remind_at"] = remind_at
    if repeat_interval_minutes is not None:
        raw_payload["interval_minutes"] = repeat_interval_minutes
        raw_payload["times"] = repeat_times
    if repeat_remind_start_time is not None:
        raw_payload["remind_start_time"] = repeat_remind_start_time
    arguments = _coerce_schedule_command_arguments(raw_payload)
    if not isinstance(arguments, ScheduleAddArgs):
        return None
    return ScheduleAddCommand(arguments=arguments)


def parse_schedule_update_command(command: str) -> ScheduleUpdateCommand | None:
    parsed_update = _parse_schedule_update_input(command.removeprefix("/schedule update ").strip())
    if parsed_update is None:
        return None
    (
        schedule_id,
        event_time,
        title,
        tag,
        has_tag,
        duration_minutes,
        remind_at,
        has_remind,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
        has_repeat_remind_start_time,
    ) = parsed_update
    raw_payload: dict[str, object] = {
        "action": "update",
        "id": schedule_id,
        "event_time": event_time,
        "title": title,
    }
    if has_tag:
        raw_payload["tag"] = tag
    if duration_minutes is not None:
        raw_payload["duration_minutes"] = duration_minutes
    if has_remind:
        raw_payload["remind_at"] = remind_at
    if repeat_interval_minutes is not None:
        raw_payload["interval_minutes"] = repeat_interval_minutes
        raw_payload["times"] = repeat_times
    if has_repeat_remind_start_time:
        raw_payload["remind_start_time"] = repeat_remind_start_time
    arguments = _coerce_schedule_command_arguments(raw_payload)
    if not isinstance(arguments, ScheduleUpdateArgs):
        return None
    return ScheduleUpdateCommand(arguments=arguments)


def parse_schedule_delete_command(command: str) -> ScheduleDeleteCommand | None:
    schedule_id = _parse_positive_int(command.removeprefix("/schedule delete ").strip())
    if schedule_id is None:
        return None
    arguments = _coerce_schedule_command_arguments({"action": "delete", "id": schedule_id})
    if not isinstance(arguments, ScheduleIdArgs):
        return None
    return ScheduleDeleteCommand(arguments=arguments)


def parse_schedule_repeat_command(command: str) -> ScheduleRepeatCommand | None:
    parsed_toggle = _parse_schedule_repeat_toggle_input(command.removeprefix("/schedule repeat ").strip())
    if parsed_toggle is None:
        return None
    schedule_id, enabled = parsed_toggle
    arguments = _coerce_schedule_command_arguments({"action": "repeat", "id": schedule_id, "enabled": enabled})
    if not isinstance(arguments, ScheduleRepeatArgs):
        return None
    return ScheduleRepeatCommand(arguments=arguments)


__all__ = [
    "CliCommandBase",
    "HistoryListCommand",
    "HistorySearchCommand",
    "ScheduleAddCommand",
    "ScheduleDeleteCommand",
    "ScheduleGetCommand",
    "ScheduleListCommand",
    "ScheduleRepeatCommand",
    "ScheduleUpdateCommand",
    "ScheduleViewCommand",
    "ThoughtsAddCommand",
    "ThoughtsDeleteCommand",
    "ThoughtsGetCommand",
    "ThoughtsListCommand",
    "ThoughtsUpdateCommand",
    "parse_history_list_command",
    "parse_history_search_command",
    "parse_schedule_add_command",
    "parse_schedule_delete_command",
    "parse_schedule_get_command",
    "parse_schedule_list_command",
    "parse_schedule_repeat_command",
    "parse_schedule_update_command",
    "parse_schedule_view_command",
    "parse_thoughts_add_command",
    "parse_tool_command_payload",
    "parse_thoughts_delete_command",
    "parse_thoughts_get_command",
    "parse_thoughts_list_command",
    "parse_thoughts_update_command",
]
