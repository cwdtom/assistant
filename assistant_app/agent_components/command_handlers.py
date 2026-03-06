from __future__ import annotations

from typing import Any

from assistant_app.agent_components.parsing_utils import (
    _INVALID_OPTION_VALUE,
    _default_schedule_list_window,
    _filter_schedules_by_calendar_view,
    _normalize_thought_status_value,
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
    _resolve_schedule_view_window,
)
from assistant_app.agent_components.render_helpers import (
    _format_history_list_result,
    _format_history_search_result,
    _format_thought_detail_result,
    _format_thoughts_list_result,
    _format_schedule_remind_meta_inline,
    _is_planner_command_success,
    _render_table,
    _schedule_list_empty_text,
    _schedule_list_title,
    _schedule_table_headers,
    _schedule_table_rows,
    _schedule_view_title,
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
        history_limit = _parse_history_list_limit(command)
        if history_limit is None:
            return "用法: /history list [--limit <>=1>]"
        turns = agent.db.recent_turns(limit=history_limit)
        if not turns:
            return "暂无历史会话。"
        return _format_history_list_result(turns)

    if command.startswith("/history search "):
        history_search = _parse_history_search_input(command.removeprefix("/history search ").strip())
        if history_search is None:
            return "用法: /history search <关键词> [--limit <>=1>]"
        keyword, history_limit = history_search
        turns = agent.db.search_turns(keyword, limit=history_limit)
        if not turns:
            return f"未找到包含“{keyword}”的历史会话。"
        return _format_history_search_result(keyword=keyword, turns=turns)

    if command == "/thoughts add" or command.startswith("/thoughts add "):
        action = "add"
        _log_thoughts_command_start(agent, action=action)
        try:
            content = command.removeprefix("/thoughts add").strip()
            if not content:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result="用法: /thoughts add <内容>",
                )
            thought_id = agent.db.add_thought(content=content)
            return _finalize_thoughts_command(
                agent,
                action=action,
                result=f"已记录想法 #{thought_id}: {content}",
            )
        except Exception as exc:  # noqa: BLE001
            return _fail_thoughts_command(agent, action=action, exc=exc)

    if command == "/thoughts list" or command.startswith("/thoughts list "):
        action = "list"
        _log_thoughts_command_start(agent, action=action)
        try:
            parsed_status = _parse_thoughts_list_status_input(command.removeprefix("/thoughts list").strip())
            if parsed_status is _INVALID_OPTION_VALUE:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result="用法: /thoughts list [--status <未完成|完成|删除>]",
                )
            list_status = parsed_status if isinstance(parsed_status, str) else None
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
            thought_id = _parse_positive_int(command.removeprefix("/thoughts get ").strip())
            if thought_id is None:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result="用法: /thoughts get <id>",
                )
            item = agent.db.get_thought(thought_id)
            if item is None:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result=f"未找到想法 #{thought_id}",
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
            parsed_update = _parse_thoughts_update_input(command.removeprefix("/thoughts update ").strip())
            if parsed_update is None:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result="用法: /thoughts update <id> <内容> [--status <未完成|完成|删除>]",
                )
            thought_id, content, status, has_status = parsed_update
            if has_status:
                normalized_status = _normalize_thought_status_value(status)
                if normalized_status is None:
                    return _finalize_thoughts_command(
                        agent,
                        action=action,
                        result="用法: /thoughts update <id> <内容> [--status <未完成|完成|删除>]",
                    )
                updated = agent.db.update_thought(
                    thought_id,
                    content=content,
                    status=normalized_status,
                )
            else:
                updated = agent.db.update_thought(
                    thought_id,
                    content=content,
                )
            if not updated:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result=f"未找到想法 #{thought_id}",
                )
            item = agent.db.get_thought(thought_id)
            if item is None:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result=f"未找到想法 #{thought_id}",
                )
            return _finalize_thoughts_command(
                agent,
                action=action,
                result=f"已更新想法 #{thought_id}: {item.content} [状态:{item.status}]",
            )
        except Exception as exc:  # noqa: BLE001
            return _fail_thoughts_command(agent, action=action, exc=exc)

    if command.startswith("/thoughts delete "):
        action = "delete"
        _log_thoughts_command_start(agent, action=action)
        try:
            thought_id = _parse_positive_int(command.removeprefix("/thoughts delete ").strip())
            if thought_id is None:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result="用法: /thoughts delete <id>",
                )
            deleted = agent.db.soft_delete_thought(thought_id)
            if not deleted:
                return _finalize_thoughts_command(
                    agent,
                    action=action,
                    result=f"未找到想法 #{thought_id}",
                )
            return _finalize_thoughts_command(
                agent,
                action=action,
                result=f"想法 #{thought_id} 已删除。",
            )
        except Exception as exc:  # noqa: BLE001
            return _fail_thoughts_command(agent, action=action, exc=exc)

    if command == "/schedule list" or command.startswith("/schedule list "):
        parsed_list_tag = _parse_schedule_list_tag_input(command.removeprefix("/schedule list").strip())
        if parsed_list_tag is _INVALID_OPTION_VALUE:
            return "用法: /schedule list [--tag <标签>]"
        list_tag = parsed_list_tag if isinstance(parsed_list_tag, str) else None
        window_start, window_end = _default_schedule_list_window(
            window_days=agent._schedule_max_window_days
        )
        items = agent.db.list_schedules(
            window_start=window_start,
            window_end=window_end,
            max_window_days=agent._schedule_max_window_days,
            tag=list_tag,
        )
        if not items:
            return _schedule_list_empty_text(window_days=agent._schedule_max_window_days, tag=list_tag)
        table = _render_table(
            headers=_schedule_table_headers(),
            rows=_schedule_table_rows(items),
        )
        title = _schedule_list_title(window_days=agent._schedule_max_window_days, tag=list_tag)
        return f"{title}:\n{table}"

    if command.startswith("/schedule view "):
        view_parsed = _parse_schedule_view_command_input(command.removeprefix("/schedule view ").strip())
        if view_parsed is None:
            return "用法: /schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM] [--tag <标签>]"
        view_name, anchor, view_tag = view_parsed
        window_start, window_end = _resolve_schedule_view_window(view_name=view_name, anchor=anchor)
        items = agent.db.list_schedules(
            window_start=window_start,
            window_end=window_end,
            max_window_days=agent._schedule_max_window_days,
            tag=view_tag,
        )
        items = _filter_schedules_by_calendar_view(items, view_name=view_name, anchor=anchor)
        if not items:
            return f"{view_name} 视图下{f'（标签:{view_tag}）' if view_tag else ''}暂无日程。"
        table = _render_table(
            headers=_schedule_table_headers(),
            rows=_schedule_table_rows(items),
        )
        title = _schedule_view_title(view_name=view_name, anchor=anchor, tag=view_tag)
        return f"{title}:\n{table}"

    if command.startswith("/schedule get "):
        schedule_id = _parse_positive_int(command.removeprefix("/schedule get ").strip())
        if schedule_id is None:
            return "用法: /schedule get <id>"
        item = agent.db.get_schedule(schedule_id)
        if item is None:
            return f"未找到日程 #{schedule_id}"
        table = _render_table(
            headers=_schedule_table_headers(),
            rows=_schedule_table_rows([item]),
        )
        return f"日程详情:\n{table}"

    if command.startswith("/schedule add"):
        add_schedule_parsed = _parse_schedule_add_input(command.removeprefix("/schedule add").strip())
        if add_schedule_parsed is None:
            return (
                "用法: /schedule add <YYYY-MM-DD HH:MM> <标题> "
                "[--tag <标签>] "
                "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
                "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
            )
        (
            add_event_time,
            add_title,
            add_tag,
            add_duration_minutes,
            add_remind_at,
            add_repeat_interval_minutes,
            add_repeat_times,
            add_repeat_remind_start_time,
        ) = add_schedule_parsed
        schedule_id = agent.db.add_schedule(
            title=add_title,
            event_time=add_event_time,
            duration_minutes=add_duration_minutes,
            remind_at=add_remind_at,
            tag=add_tag,
        )
        if add_repeat_interval_minutes is not None and add_repeat_times != 1:
            agent.db.set_schedule_recurrence(
                schedule_id,
                start_time=add_event_time,
                repeat_interval_minutes=add_repeat_interval_minutes,
                repeat_times=add_repeat_times,
                remind_start_time=add_repeat_remind_start_time,
            )
        notify_added = getattr(agent, "notify_schedule_added", None)
        if callable(notify_added):
            notify_added(schedule_id)
        remind_meta = _format_schedule_remind_meta_inline(
            remind_at=add_remind_at,
            repeat_remind_start_time=add_repeat_remind_start_time,
        )
        if add_repeat_times == 1:
            return (
                f"已添加日程 #{schedule_id} [标签:{add_tag}]: {add_event_time} {add_title} "
                f"({add_duration_minutes} 分钟){remind_meta}"
            )
        if add_repeat_times == -1:
            return (
                f"已添加无限重复日程 #{schedule_id} [标签:{add_tag}]: {add_event_time} {add_title} "
                f"(duration={add_duration_minutes}m, interval={add_repeat_interval_minutes}m{remind_meta})"
            )
        return (
            f"已添加重复日程 {add_repeat_times} 条 [标签:{add_tag}]: {add_event_time} {add_title} "
            f"(duration={add_duration_minutes}m, interval={add_repeat_interval_minutes}m, "
            f"times={add_repeat_times}{remind_meta})"
        )

    if command.startswith("/schedule update "):
        update_schedule_parsed = _parse_schedule_update_input(
            command.removeprefix("/schedule update ").strip()
        )
        if update_schedule_parsed is None:
            return (
                "用法: /schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
                "[--tag <标签>] "
                "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
                "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
            )
        (
            schedule_id,
            event_time,
            title,
            parsed_tag,
            has_tag,
            parsed_duration_minutes,
            parsed_remind_at,
            has_remind,
            repeat_interval_minutes,
            repeat_times,
            repeat_remind_start_time,
            has_repeat_remind_start_time,
        ) = update_schedule_parsed
        current_item = agent.db.get_schedule(schedule_id)
        if current_item is None:
            return f"未找到日程 #{schedule_id}"
        if parsed_duration_minutes is not None:
            applied_duration_minutes = parsed_duration_minutes
        else:
            applied_duration_minutes = current_item.duration_minutes
        schedule_update_kwargs: dict[str, Any] = {
            "title": title,
            "event_time": event_time,
            "duration_minutes": applied_duration_minutes,
        }
        if has_tag:
            schedule_update_kwargs["tag"] = parsed_tag
        if has_remind:
            schedule_update_kwargs["remind_at"] = parsed_remind_at
        if has_repeat_remind_start_time:
            schedule_update_kwargs["repeat_remind_start_time"] = repeat_remind_start_time
        updated = agent.db.update_schedule(schedule_id, **schedule_update_kwargs)
        if not updated:
            return f"未找到日程 #{schedule_id}"
        if repeat_times == 1:
            agent.db.clear_schedule_recurrence(schedule_id)
            notify_updated = getattr(agent, "notify_schedule_updated", None)
            if callable(notify_updated):
                notify_updated(schedule_id)
            item = agent.db.get_schedule(schedule_id)
            remind_meta = _format_schedule_remind_meta_inline(
                remind_at=item.remind_at if item else None,
                repeat_remind_start_time=item.repeat_remind_start_time if item else None,
            )
            if item is not None:
                return (
                    f"已更新日程 #{schedule_id} [标签:{item.tag}]: {event_time} {title} "
                    f"({applied_duration_minutes} 分钟){remind_meta}"
                )
            return (
                f"已更新日程 #{schedule_id} [标签:{parsed_tag if has_tag else current_item.tag}]: "
                f"{event_time} {title} ({applied_duration_minutes} 分钟){remind_meta}"
            )
        if repeat_interval_minutes is not None:
            remind_start_for_rule = (
                repeat_remind_start_time
                if has_repeat_remind_start_time
                else current_item.repeat_remind_start_time
            )
            agent.db.set_schedule_recurrence(
                schedule_id,
                start_time=event_time,
                repeat_interval_minutes=repeat_interval_minutes,
                repeat_times=repeat_times,
                remind_start_time=remind_start_for_rule,
            )
        notify_updated = getattr(agent, "notify_schedule_updated", None)
        if callable(notify_updated):
            notify_updated(schedule_id)
        item = agent.db.get_schedule(schedule_id)
        remind_meta = _format_schedule_remind_meta_inline(
            remind_at=item.remind_at if item else None,
            repeat_remind_start_time=item.repeat_remind_start_time if item else None,
        )
        if repeat_times == -1:
            return (
                f"已更新为无限重复日程 #{schedule_id} [标签:{item.tag if item else current_item.tag}]: "
                f"{event_time} {title} "
                f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m{remind_meta})"
            )
        return (
            f"已更新日程 #{schedule_id} [标签:{item.tag if item else current_item.tag}]: {event_time} {title} "
            f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m, "
            f"times={repeat_times}{remind_meta})"
        )

    if command.startswith("/schedule delete "):
        schedule_id = _parse_positive_int(command.removeprefix("/schedule delete ").strip())
        if schedule_id is None:
            return "用法: /schedule delete <id>"
        mapping = agent.db.get_schedule_feishu_mapping(schedule_id)
        deleted = agent.db.delete_schedule(schedule_id)
        if not deleted:
            return f"未找到日程 #{schedule_id}"
        notify_deleted = getattr(agent, "notify_schedule_deleted", None)
        if callable(notify_deleted):
            notify_deleted(schedule_id, mapping.feishu_event_id if mapping is not None else None)
        return f"日程 #{schedule_id} 已删除。"

    if command.startswith("/schedule repeat "):
        repeat_toggle_parsed = _parse_schedule_repeat_toggle_input(
            command.removeprefix("/schedule repeat ").strip()
        )
        if repeat_toggle_parsed is None:
            return "用法: /schedule repeat <id> <on|off>"
        schedule_id, enabled = repeat_toggle_parsed
        changed = agent.db.set_schedule_recurrence_enabled(schedule_id, enabled)
        if not changed:
            return f"日程 #{schedule_id} 没有可切换的重复规则。"
        status = "启用" if enabled else "停用"
        return f"已{status}日程 #{schedule_id} 的重复规则。"

    return "未知命令。输入 /help 查看可用命令。"


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
