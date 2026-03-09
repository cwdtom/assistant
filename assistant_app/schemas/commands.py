from __future__ import annotations

from typing import Literal

from pydantic import Field

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
)


class CliCommandBase(FrozenModel):
    action_tool: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)

    def to_runtime_payload(self) -> RuntimePlannerActionPayload:
        return RuntimePlannerActionPayload(tool_name=self.tool_name, arguments=getattr(self, "arguments"))


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


def parse_history_list_command(command: str) -> HistoryListCommand | None:
    history_limit = _parse_history_list_limit(command)
    if history_limit is None:
        return None
    if command == "/history list":
        return HistoryListCommand()
    return HistoryListCommand(arguments=HistoryListArgs(limit=history_limit))


def parse_history_search_command(command: str) -> HistorySearchCommand | None:
    raw = command.removeprefix("/history search ").strip()
    parsed = _parse_history_search_input(raw)
    if parsed is None:
        return None
    keyword, history_limit = parsed
    return HistorySearchCommand(arguments=HistorySearchArgs(keyword=keyword, limit=history_limit))


def parse_thoughts_add_command(command: str) -> ThoughtsAddCommand | None:
    content = command.removeprefix("/thoughts add").strip()
    if not content:
        return None
    return ThoughtsAddCommand(arguments=ThoughtsAddArgs(content=content))


def parse_thoughts_list_command(command: str) -> ThoughtsListCommand | None:
    parsed_status = _parse_thoughts_list_status_input(command.removeprefix("/thoughts list").strip())
    if parsed_status is _INVALID_OPTION_VALUE:
        return None
    if isinstance(parsed_status, str):
        return ThoughtsListCommand(arguments=ThoughtsListArgs(status=parsed_status))
    return ThoughtsListCommand()


def parse_thoughts_get_command(command: str) -> ThoughtsGetCommand | None:
    thought_id = _parse_positive_int(command.removeprefix("/thoughts get ").strip())
    if thought_id is None:
        return None
    return ThoughtsGetCommand(arguments=ThoughtsIdArgs(id=thought_id))


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
    return ThoughtsUpdateCommand(arguments=ThoughtsUpdateArgs.model_validate(arguments))


def parse_thoughts_delete_command(command: str) -> ThoughtsDeleteCommand | None:
    thought_id = _parse_positive_int(command.removeprefix("/thoughts delete ").strip())
    if thought_id is None:
        return None
    return ThoughtsDeleteCommand(arguments=ThoughtsIdArgs(id=thought_id))


def parse_schedule_list_command(command: str) -> ScheduleListCommand | None:
    parsed_tag = _parse_schedule_list_tag_input(command.removeprefix("/schedule list").strip())
    if parsed_tag is _INVALID_OPTION_VALUE:
        return None
    if isinstance(parsed_tag, str):
        return ScheduleListCommand(arguments=ScheduleListArgs(tag=parsed_tag))
    return ScheduleListCommand()


def parse_schedule_view_command(command: str) -> ScheduleViewCommand | None:
    parsed_view = _parse_schedule_view_command_input(command.removeprefix("/schedule view ").strip())
    if parsed_view is None:
        return None
    view_name, anchor, tag = parsed_view
    arguments: dict[str, object] = {"view": view_name}
    if anchor is not None:
        arguments["anchor"] = anchor
    if tag is not None:
        arguments["tag"] = tag
    return ScheduleViewCommand(arguments=ScheduleViewArgs.model_validate(arguments))


def parse_schedule_get_command(command: str) -> ScheduleGetCommand | None:
    schedule_id = _parse_positive_int(command.removeprefix("/schedule get ").strip())
    if schedule_id is None:
        return None
    return ScheduleGetCommand(arguments=ScheduleIdArgs(id=schedule_id))


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
    arguments: dict[str, object] = {
        "event_time": event_time,
        "title": title,
        "tag": tag,
        "duration_minutes": duration_minutes,
    }
    if remind_at is not None:
        arguments["remind_at"] = remind_at
    if repeat_interval_minutes is not None:
        arguments["interval_minutes"] = repeat_interval_minutes
        arguments["times"] = repeat_times
    if repeat_remind_start_time is not None:
        arguments["remind_start_time"] = repeat_remind_start_time
    return ScheduleAddCommand(arguments=ScheduleAddArgs.model_validate(arguments))


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
    arguments: dict[str, object] = {
        "id": schedule_id,
        "event_time": event_time,
        "title": title,
    }
    if has_tag:
        arguments["tag"] = tag
    if duration_minutes is not None:
        arguments["duration_minutes"] = duration_minutes
    if has_remind:
        arguments["remind_at"] = remind_at
    if repeat_interval_minutes is not None:
        arguments["interval_minutes"] = repeat_interval_minutes
        arguments["times"] = repeat_times
    if has_repeat_remind_start_time:
        arguments["remind_start_time"] = repeat_remind_start_time
    return ScheduleUpdateCommand(arguments=ScheduleUpdateArgs.model_validate(arguments))


def parse_schedule_delete_command(command: str) -> ScheduleDeleteCommand | None:
    schedule_id = _parse_positive_int(command.removeprefix("/schedule delete ").strip())
    if schedule_id is None:
        return None
    return ScheduleDeleteCommand(arguments=ScheduleIdArgs(id=schedule_id))


def parse_schedule_repeat_command(command: str) -> ScheduleRepeatCommand | None:
    parsed_toggle = _parse_schedule_repeat_toggle_input(command.removeprefix("/schedule repeat ").strip())
    if parsed_toggle is None:
        return None
    schedule_id, enabled = parsed_toggle
    return ScheduleRepeatCommand(arguments=ScheduleRepeatArgs(id=schedule_id, enabled=enabled))


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
    "parse_thoughts_delete_command",
    "parse_thoughts_get_command",
    "parse_thoughts_list_command",
    "parse_thoughts_update_command",
]
