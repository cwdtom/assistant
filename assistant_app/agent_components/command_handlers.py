from __future__ import annotations

from typing import Any

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


def help_text() -> str:
    return (
        "可用命令:\n"
        "/help\n"
        "/version\n"
        "/date\n"
        "/profile refresh\n"
        "/history list [--limit <>=1>]\n"
        "/history search <关键词> [--limit <>=1>]\n"
        "/thoughts add <内容>\n"
        "/thoughts list [--status <未完成|完成|删除>]\n"
        "/thoughts get <id>\n"
        "/thoughts update <id> <内容> [--status <未完成|完成|删除>]\n"
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
    if command == "/version":
        if agent._app_version == UNKNOWN_APP_VERSION:
            return "当前版本：unknown"
        return f"当前版本：v{agent._app_version}"
    if command.split(maxsplit=1)[0] == "/version":
        return "用法: /version"
    if command == "/date":
        date_command = parse_date_command(command)
        if date_command is None:
            return "用法: /date"
        return _execute_system_cli_command(agent, parsed_command=date_command, raw_input=command)
    if command.split(maxsplit=1)[0] == "/date":
        return "用法: /date"

    if command == "/profile refresh":
        runner = agent._user_profile_refresh_runner
        if runner is None:
            return (
                "当前未启用 user_profile 刷新服务。"
                "请检查 USER_PROFILE_REFRESH_ENABLED、USER_PROFILE_PATH 与 LLM 配置。"
            )
        try:
            return runner()
        except Exception as exc:  # noqa: BLE001
            agent._app_logger.warning(
                "manual user profile refresh failed",
                extra={
                    "event": "user_profile_manual_refresh_failed",
                    "context": {"error": repr(exc)},
                },
            )
            return f"刷新 user_profile 失败: {exc}"
    if command == "/history list" or command.startswith("/history list "):
        history_list_command = parse_history_list_command(command)
        if history_list_command is None:
            return "用法: /history list [--limit <>=1>]"
        return _execute_history_cli_command(agent, parsed_command=history_list_command, raw_input=command)

    if command.startswith("/history search "):
        history_search_command = parse_history_search_command(command)
        if history_search_command is None:
            return "用法: /history search <关键词> [--limit <>=1>]"
        return _execute_history_cli_command(agent, parsed_command=history_search_command, raw_input=command)

    if command == "/thoughts add" or command.startswith("/thoughts add "):
        thoughts_add_command = parse_thoughts_add_command(command)
        return _execute_thoughts_cli_command(
            agent,
            action="add",
            parsed_command=thoughts_add_command,
            raw_input=command,
            usage_text="用法: /thoughts add <内容>",
        )

    if command == "/thoughts list" or command.startswith("/thoughts list "):
        thoughts_list_command = parse_thoughts_list_command(command)
        return _execute_thoughts_cli_command(
            agent,
            action="list",
            parsed_command=thoughts_list_command,
            raw_input=command,
            usage_text="用法: /thoughts list [--status <未完成|完成|删除>]",
        )

    if command.startswith("/thoughts get "):
        thoughts_get_command = parse_thoughts_get_command(command)
        return _execute_thoughts_cli_command(
            agent,
            action="get",
            parsed_command=thoughts_get_command,
            raw_input=command,
            usage_text="用法: /thoughts get <id>",
        )

    if command.startswith("/thoughts update "):
        thoughts_update_command = parse_thoughts_update_command(command)
        return _execute_thoughts_cli_command(
            agent,
            action="update",
            parsed_command=thoughts_update_command,
            raw_input=command,
            usage_text="用法: /thoughts update <id> <内容> [--status <未完成|完成|删除>]",
        )

    if command.startswith("/thoughts delete "):
        thoughts_delete_command = parse_thoughts_delete_command(command)
        return _execute_thoughts_cli_command(
            agent,
            action="delete",
            parsed_command=thoughts_delete_command,
            raw_input=command,
            usage_text="用法: /thoughts delete <id>",
        )

    if command == "/schedule list" or command.startswith("/schedule list "):
        schedule_list_command = parse_schedule_list_command(command)
        if schedule_list_command is None:
            return "用法: /schedule list [--tag <标签>]"
        return _execute_schedule_cli_command(agent, parsed_command=schedule_list_command, raw_input=command)

    if command.startswith("/schedule view "):
        schedule_view_command = parse_schedule_view_command(command)
        if schedule_view_command is None:
            return "用法: /schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM] [--tag <标签>]"
        return _execute_schedule_cli_command(agent, parsed_command=schedule_view_command, raw_input=command)

    if command.startswith("/schedule get "):
        schedule_get_command = parse_schedule_get_command(command)
        if schedule_get_command is None:
            return "用法: /schedule get <id>"
        return _execute_schedule_cli_command(agent, parsed_command=schedule_get_command, raw_input=command)

    if command.startswith("/schedule add"):
        schedule_add_command = parse_schedule_add_command(command)
        if schedule_add_command is None:
            return (
                "用法: /schedule add <YYYY-MM-DD HH:MM> <标题> "
                "[--tag <标签>] "
                "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
                "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
            )
        return _execute_schedule_cli_command(agent, parsed_command=schedule_add_command, raw_input=command)

    if command.startswith("/schedule update "):
        schedule_update_command = parse_schedule_update_command(command)
        if schedule_update_command is None:
            return (
                "用法: /schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
                "[--tag <标签>] "
                "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
                "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
            )
        return _execute_schedule_cli_command(agent, parsed_command=schedule_update_command, raw_input=command)

    if command.startswith("/schedule delete "):
        schedule_delete_command = parse_schedule_delete_command(command)
        if schedule_delete_command is None:
            return "用法: /schedule delete <id>"
        return _execute_schedule_cli_command(agent, parsed_command=schedule_delete_command, raw_input=command)

    if command.startswith("/schedule repeat "):
        schedule_repeat_command = parse_schedule_repeat_command(command)
        if schedule_repeat_command is None:
            return "用法: /schedule repeat <id> <on|off>"
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
