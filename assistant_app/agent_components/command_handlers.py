from __future__ import annotations

from typing import Any

from assistant_app.agent_components.parsing_utils import _matches_command_prefix
from assistant_app.agent_components.render_helpers import (
    _is_planner_command_success,
)
from assistant_app.agent_components.tools.history import execute_history_system_action
from assistant_app.agent_components.tools.schedule import execute_schedule_system_action
from assistant_app.agent_components.tools.thoughts import execute_thoughts_system_action
from assistant_app.schemas.commands import (
    CliCommandBase,
    ThoughtsAddCommand,
    ThoughtsDeleteCommand,
    ThoughtsGetCommand,
    ThoughtsListCommand,
    ThoughtsUpdateCommand,
    parse_date_command,
    parse_history_list_command,
    parse_history_search_command,
    parse_schedule_add_command,
    parse_schedule_delete_command,
    parse_schedule_get_command,
    parse_schedule_list_command,
    parse_schedule_repeat_command,
    parse_schedule_update_command,
    parse_schedule_view_command,
    parse_thoughts_add_command,
    parse_thoughts_delete_command,
    parse_thoughts_get_command,
    parse_thoughts_list_command,
    parse_thoughts_update_command,
)

UNKNOWN_APP_VERSION = "unknown"
USAGE_HISTORY_LIST = "用法: /history list [--limit <>=1>]"
USAGE_HISTORY_SEARCH = "用法: /history search <关键词> [--limit <>=1>]"
USAGE_THOUGHTS_ADD = "用法: /thoughts add <内容>"
USAGE_THOUGHTS_LIST = "用法: /thoughts list [--status <pending|completed|deleted>]"
USAGE_THOUGHTS_GET = "用法: /thoughts get <id>"
USAGE_THOUGHTS_UPDATE = "用法: /thoughts update <id> <内容> [--status <pending|completed|deleted>]"
USAGE_THOUGHTS_DELETE = "用法: /thoughts delete <id>"
USAGE_SCHEDULE_LIST = "用法: /schedule list [--tag <标签>]"
USAGE_SCHEDULE_VIEW = "用法: /schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM] [--tag <标签>]"
USAGE_SCHEDULE_GET = "用法: /schedule get <id>"
USAGE_SCHEDULE_ADD = (
    "用法: /schedule add <YYYY-MM-DD HH:MM> <标题> "
    "[--tag <标签>] "
    "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
    "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
)
USAGE_SCHEDULE_UPDATE = (
    "用法: /schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
    "[--tag <标签>] "
    "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
    "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
)
USAGE_SCHEDULE_DELETE = "用法: /schedule delete <id>"
USAGE_SCHEDULE_REPEAT = "用法: /schedule repeat <id> <on|off>"


def help_text() -> str:
    return (
        "可用命令:\n"
        "/help\n"
        "/version\n"
        "/date\n"
        "/history list [--limit <>=1>]\n"
        "/history search <关键词> [--limit <>=1>]\n"
        "/thoughts add <内容>\n"
        "/thoughts list [--status <pending|completed|deleted>]\n"
        "/thoughts get <id>\n"
        "/thoughts update <id> <内容> [--status <pending|completed|deleted>]\n"
        "/thoughts delete <id>\n"
        "/schedule add <YYYY-MM-DD HH:MM> <标题> "
        "[--tag <标签>] "
        "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
        "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]\n"
        "/schedule get <id>\n"
        "/schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM] [--tag <标签>]\n"
        "/schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
        "[--tag <标签>] "
        "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
        "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]\n"
        "/schedule repeat <id> <on|off>\n"
        "/schedule delete <id>\n"
        "/schedule list [--tag <标签>]\n"
        "你也可以直接说自然语言（会走 plan -> thought -> act -> observe -> replan 循环）。\n"
        "当前版本仅支持计划链路，不再走 chat 直聊分支。"
    )


def handle_command(agent: Any, command: str) -> str:
    if command == "/help":
        return help_text()
    if _matches_command_prefix(command, "/version"):
        if command == "/version":
            if agent._app_version == UNKNOWN_APP_VERSION:
                return "当前版本：unknown"
            return f"当前版本：v{agent._app_version}"
        return "用法: /version"
    if _matches_command_prefix(command, "/date"):
        if command == "/date":
            date_command = parse_date_command(command)
            if date_command is None:
                return "用法: /date"
            return _execute_system_cli_command(agent, parsed_command=date_command, raw_input=command)
        return "用法: /date"
    if _matches_command_prefix(command, "/history list"):
        history_list_command = parse_history_list_command(command)
        if history_list_command is None:
            return USAGE_HISTORY_LIST
        return _execute_history_cli_command(agent, parsed_command=history_list_command, raw_input=command)

    if _matches_command_prefix(command, "/history search"):
        history_search_command = parse_history_search_command(command)
        if history_search_command is None:
            return USAGE_HISTORY_SEARCH
        return _execute_history_cli_command(agent, parsed_command=history_search_command, raw_input=command)

    if _matches_command_prefix(command, "/thoughts add"):
        thoughts_add_command = parse_thoughts_add_command(command)
        return _execute_thoughts_cli_command(
            agent,
            action="add",
            parsed_command=thoughts_add_command,
            raw_input=command,
            usage_text=USAGE_THOUGHTS_ADD,
        )

    if _matches_command_prefix(command, "/thoughts list"):
        thoughts_list_command = parse_thoughts_list_command(command)
        return _execute_thoughts_cli_command(
            agent,
            action="list",
            parsed_command=thoughts_list_command,
            raw_input=command,
            usage_text=USAGE_THOUGHTS_LIST,
        )

    if _matches_command_prefix(command, "/thoughts get"):
        thoughts_get_command = parse_thoughts_get_command(command)
        return _execute_thoughts_cli_command(
            agent,
            action="get",
            parsed_command=thoughts_get_command,
            raw_input=command,
            usage_text=USAGE_THOUGHTS_GET,
        )

    if _matches_command_prefix(command, "/thoughts update"):
        thoughts_update_command = parse_thoughts_update_command(command)
        return _execute_thoughts_cli_command(
            agent,
            action="update",
            parsed_command=thoughts_update_command,
            raw_input=command,
            usage_text=USAGE_THOUGHTS_UPDATE,
        )

    if _matches_command_prefix(command, "/thoughts delete"):
        thoughts_delete_command = parse_thoughts_delete_command(command)
        return _execute_thoughts_cli_command(
            agent,
            action="delete",
            parsed_command=thoughts_delete_command,
            raw_input=command,
            usage_text=USAGE_THOUGHTS_DELETE,
        )

    if _matches_command_prefix(command, "/schedule list"):
        schedule_list_command = parse_schedule_list_command(command)
        if schedule_list_command is None:
            return USAGE_SCHEDULE_LIST
        return _execute_schedule_cli_command(agent, parsed_command=schedule_list_command, raw_input=command)

    if _matches_command_prefix(command, "/schedule view"):
        schedule_view_command = parse_schedule_view_command(command)
        if schedule_view_command is None:
            return USAGE_SCHEDULE_VIEW
        return _execute_schedule_cli_command(agent, parsed_command=schedule_view_command, raw_input=command)

    if _matches_command_prefix(command, "/schedule get"):
        schedule_get_command = parse_schedule_get_command(command)
        if schedule_get_command is None:
            return USAGE_SCHEDULE_GET
        return _execute_schedule_cli_command(agent, parsed_command=schedule_get_command, raw_input=command)

    if _matches_command_prefix(command, "/schedule add"):
        schedule_add_command = parse_schedule_add_command(command)
        if schedule_add_command is None:
            return USAGE_SCHEDULE_ADD
        return _execute_schedule_cli_command(agent, parsed_command=schedule_add_command, raw_input=command)

    if _matches_command_prefix(command, "/schedule update"):
        schedule_update_command = parse_schedule_update_command(command)
        if schedule_update_command is None:
            return USAGE_SCHEDULE_UPDATE
        return _execute_schedule_cli_command(agent, parsed_command=schedule_update_command, raw_input=command)

    if _matches_command_prefix(command, "/schedule delete"):
        schedule_delete_command = parse_schedule_delete_command(command)
        if schedule_delete_command is None:
            return USAGE_SCHEDULE_DELETE
        return _execute_schedule_cli_command(agent, parsed_command=schedule_delete_command, raw_input=command)

    if _matches_command_prefix(command, "/schedule repeat"):
        schedule_repeat_command = parse_schedule_repeat_command(command)
        if schedule_repeat_command is None:
            return USAGE_SCHEDULE_REPEAT
        return _execute_schedule_cli_command(agent, parsed_command=schedule_repeat_command, raw_input=command)

    return "未知命令。输入 /help 查看可用命令。"


def _execute_history_cli_command(agent: Any, *, parsed_command: CliCommandBase, raw_input: str) -> str:
    return execute_history_system_action(
        agent,
        payload=parsed_command.to_runtime_payload(),
        raw_input=raw_input,
    ).result


def _execute_schedule_cli_command(agent: Any, *, parsed_command: CliCommandBase, raw_input: str) -> str:
    return execute_schedule_system_action(
        agent,
        payload=parsed_command.to_runtime_payload(),
        raw_input=raw_input,
    ).result


def _execute_system_cli_command(agent: Any, *, parsed_command: CliCommandBase, raw_input: str) -> str:
    return agent._execute_system_system_action(
        parsed_command.to_runtime_payload(),
        raw_input=raw_input,
        source="cli",
    ).result


def _execute_thoughts_cli_command(
    agent: Any,
    *,
    action: str,
    parsed_command: (
        ThoughtsAddCommand
        | ThoughtsListCommand
        | ThoughtsGetCommand
        | ThoughtsUpdateCommand
        | ThoughtsDeleteCommand
        | None
    ),
    raw_input: str,
    usage_text: str,
) -> str:
    _log_thoughts_command_start(agent, action=action)
    try:
        if parsed_command is None:
            return _finalize_thoughts_command(agent, action=action, result=usage_text)
        result = execute_thoughts_system_action(
            agent,
            payload=parsed_command.to_runtime_payload(),
            raw_input=raw_input,
        ).result
        return _finalize_thoughts_command(agent, action=action, result=result)
    except Exception as exc:  # noqa: BLE001
        return _fail_thoughts_command(agent, action=action, exc=exc)


def _log_thoughts_command_start(agent: Any, *, action: str) -> None:
    agent._app_logger.info(
        "thoughts_command_start",
        extra={"event": "thoughts_command_start", "context": {"action": action}},
    )


def _finalize_thoughts_command(agent: Any, *, action: str, result: str) -> str:
    agent._app_logger.info(
        "thoughts_command_done",
        extra={
            "event": "thoughts_command_done",
            "context": {"action": action, "ok": _is_planner_command_success(result, tool="thoughts")},
        },
    )
    return result


def _fail_thoughts_command(agent: Any, *, action: str, exc: Exception) -> str:
    agent._app_logger.warning(
        "thoughts_command_failed",
        extra={
            "event": "thoughts_command_failed",
            "context": {"action": action, "error": repr(exc)},
        },
    )
    return f"thoughts 命令执行失败: {exc}"
