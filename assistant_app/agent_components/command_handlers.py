from __future__ import annotations

from typing import Any

from assistant_app.agent_components.render_helpers import (
    _format_thought_detail_result,
    _format_thoughts_list_result,
    _is_planner_command_success,
)
from assistant_app.agent_components.tools.history import execute_history_system_action
from assistant_app.agent_components.tools.schedule import execute_schedule_system_action
from assistant_app.schemas.commands import (
    CliCommandBase,
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
        parsed_command = parse_history_list_command(command)
        if parsed_command is None:
            return "用法: /history list [--limit <>=1>]"
        return _execute_history_cli_command(agent, parsed_command=parsed_command, raw_input=command)

    if command.startswith("/history search "):
        parsed_command = parse_history_search_command(command)
        if parsed_command is None:
            return "用法: /history search <关键词> [--limit <>=1>]"
        return _execute_history_cli_command(agent, parsed_command=parsed_command, raw_input=command)

    if command == "/thoughts add" or command.startswith("/thoughts add "):
        action = "add"
        _log_thoughts_command_start(agent, action=action)
        try:
            parsed_command = parse_thoughts_add_command(command)
            if parsed_command is None:
                return _finalize_thoughts_command(agent, action=action, result="用法: /thoughts add <内容>")
            thought_id = agent.db.add_thought(content=parsed_command.arguments.content)
            return _finalize_thoughts_command(
                agent,
                action=action,
                result=f"已记录想法 #{thought_id}: {parsed_command.arguments.content}",
            )
        except Exception as exc:  # noqa: BLE001
            return _fail_thoughts_command(agent, action=action, exc=exc)

    if command == "/thoughts list" or command.startswith("/thoughts list "):
        action = "list"
        _log_thoughts_command_start(agent, action=action)
        try:
            parsed_command = parse_thoughts_list_command(command)
            if parsed_command is None:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result="用法: /thoughts list [--status <未完成|完成|删除>]",
                )
            list_status = parsed_command.arguments.status
            items = agent.db.list_thoughts(status=list_status)
            if not items:
                if list_status:
                    return _finalize_thoughts_command(
                        agent,
                        action=action,
                        result=f"暂无状态为“{list_status}”的想法。",
                    )
                return _finalize_thoughts_command(agent, action=action, result="暂无想法记录。")
            return _finalize_thoughts_command(
                agent,
                action=action,
                result=_format_thoughts_list_result(items=items, status=list_status),
            )
        except Exception as exc:  # noqa: BLE001
            return _fail_thoughts_command(agent, action=action, exc=exc)

    if command.startswith("/thoughts get "):
        action = "get"
        _log_thoughts_command_start(agent, action=action)
        try:
            parsed_command = parse_thoughts_get_command(command)
            if parsed_command is None:
                return _finalize_thoughts_command(agent, action=action, result="用法: /thoughts get <id>")
            item = agent.db.get_thought(parsed_command.arguments.id)
            if item is None:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result=f"未找到想法 #{parsed_command.arguments.id}",
                )
            return _finalize_thoughts_command(
                agent,
                action=action,
                result=_format_thought_detail_result(item),
            )
        except Exception as exc:  # noqa: BLE001
            return _fail_thoughts_command(agent, action=action, exc=exc)

    if command.startswith("/thoughts update "):
        action = "update"
        _log_thoughts_command_start(agent, action=action)
        try:
            parsed_command = parse_thoughts_update_command(command)
            if parsed_command is None:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result="用法: /thoughts update <id> <内容> [--status <未完成|完成|删除>]",
                )
            if "status" in parsed_command.arguments.model_fields_set:
                updated = agent.db.update_thought(
                    parsed_command.arguments.id,
                    content=parsed_command.arguments.content,
                    status=parsed_command.arguments.status,
                )
            else:
                updated = agent.db.update_thought(
                    parsed_command.arguments.id,
                    content=parsed_command.arguments.content,
                )
            if not updated:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result=f"未找到想法 #{parsed_command.arguments.id}",
                )
            item = agent.db.get_thought(parsed_command.arguments.id)
            if item is None:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result=f"未找到想法 #{parsed_command.arguments.id}",
                )
            return _finalize_thoughts_command(
                agent,
                action=action,
                result=f"已更新想法 #{parsed_command.arguments.id}: {item.content} [状态:{item.status}]",
            )
        except Exception as exc:  # noqa: BLE001
            return _fail_thoughts_command(agent, action=action, exc=exc)

    if command.startswith("/thoughts delete "):
        action = "delete"
        _log_thoughts_command_start(agent, action=action)
        try:
            parsed_command = parse_thoughts_delete_command(command)
            if parsed_command is None:
                return _finalize_thoughts_command(agent, action=action, result="用法: /thoughts delete <id>")
            deleted = agent.db.soft_delete_thought(parsed_command.arguments.id)
            if not deleted:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result=f"未找到想法 #{parsed_command.arguments.id}",
                )
            return _finalize_thoughts_command(
                agent,
                action=action,
                result=f"想法 #{parsed_command.arguments.id} 已删除。",
            )
        except Exception as exc:  # noqa: BLE001
            return _fail_thoughts_command(agent, action=action, exc=exc)

    if command == "/schedule list" or command.startswith("/schedule list "):
        parsed_command = parse_schedule_list_command(command)
        if parsed_command is None:
            return "用法: /schedule list [--tag <标签>]"
        return _execute_schedule_cli_command(agent, parsed_command=parsed_command, raw_input=command)

    if command.startswith("/schedule view "):
        parsed_command = parse_schedule_view_command(command)
        if parsed_command is None:
            return "用法: /schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM] [--tag <标签>]"
        return _execute_schedule_cli_command(agent, parsed_command=parsed_command, raw_input=command)

    if command.startswith("/schedule get "):
        parsed_command = parse_schedule_get_command(command)
        if parsed_command is None:
            return "用法: /schedule get <id>"
        return _execute_schedule_cli_command(agent, parsed_command=parsed_command, raw_input=command)

    if command.startswith("/schedule add"):
        parsed_command = parse_schedule_add_command(command)
        if parsed_command is None:
            return (
                "用法: /schedule add <YYYY-MM-DD HH:MM> <标题> "
                "[--tag <标签>] "
                "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
                "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
            )
        return _execute_schedule_cli_command(agent, parsed_command=parsed_command, raw_input=command)

    if command.startswith("/schedule update "):
        parsed_command = parse_schedule_update_command(command)
        if parsed_command is None:
            return (
                "用法: /schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
                "[--tag <标签>] "
                "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
                "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
            )
        return _execute_schedule_cli_command(agent, parsed_command=parsed_command, raw_input=command)

    if command.startswith("/schedule delete "):
        parsed_command = parse_schedule_delete_command(command)
        if parsed_command is None:
            return "用法: /schedule delete <id>"
        return _execute_schedule_cli_command(agent, parsed_command=parsed_command, raw_input=command)

    if command.startswith("/schedule repeat "):
        parsed_command = parse_schedule_repeat_command(command)
        if parsed_command is None:
            return "用法: /schedule repeat <id> <on|off>"
        return _execute_schedule_cli_command(agent, parsed_command=parsed_command, raw_input=command)

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
