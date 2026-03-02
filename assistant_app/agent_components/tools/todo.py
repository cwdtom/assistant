from __future__ import annotations

from typing import Any

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.parsing_utils import (
    _INVALID_OPTION_VALUE,
    _filter_todos_by_view,
    _normalize_optional_datetime_value,
    _normalize_positive_int_value,
    _normalize_todo_priority_value,
    _normalize_todo_tag_value,
    _normalize_todo_view_value,
    _now_time_text,
)
from assistant_app.agent_components.render_helpers import (
    _format_todo_meta_inline,
    _is_planner_command_success,
    _render_todo_table,
    _todo_list_empty_text,
    _todo_list_header,
    _todo_search_empty_text,
    _todo_search_header,
)


def execute_todo_system_action(agent: Any, payload: dict[str, Any], *, raw_input: str) -> Any:
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"add", "list", "get", "update", "delete", "done", "search", "view"}:
        return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result="todo.action 非法。")

    if action == "add":
        content = str(payload.get("content") or "").strip()
        if not content:
            return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result="todo.add 缺少 content。")
        parsed_tag = _normalize_todo_tag_value(payload.get("tag"))
        add_tag = parsed_tag or "default"
        if "priority" in payload:
            add_priority = _normalize_todo_priority_value(payload.get("priority"))
            if add_priority is None:
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="todo.add priority 需为 >=0 的整数。",
                )
        else:
            add_priority = 0
        add_due_at = _normalize_optional_datetime_value(payload.get("due_at"), key_present="due_at" in payload)
        if add_due_at is _INVALID_OPTION_VALUE:
            return PlannerObservation(
                tool="todo",
                input_text=raw_input,
                ok=False,
                result="todo.add due_at 格式非法，需为 YYYY-MM-DD HH:MM。",
            )
        add_due_at_text = add_due_at if isinstance(add_due_at, str) else None
        add_remind_at = _normalize_optional_datetime_value(
            payload.get("remind_at"),
            key_present="remind_at" in payload,
        )
        if add_remind_at is _INVALID_OPTION_VALUE:
            return PlannerObservation(
                tool="todo",
                input_text=raw_input,
                ok=False,
                result="todo.add remind_at 格式非法，需为 YYYY-MM-DD HH:MM。",
            )
        add_remind_at_text = add_remind_at if isinstance(add_remind_at, str) else None
        try:
            added_todo_id = agent.db.add_todo(
                content,
                tag=add_tag,
                priority=add_priority,
                due_at=add_due_at_text,
                remind_at=add_remind_at_text,
            )
        except ValueError:
            return PlannerObservation(
                tool="todo",
                input_text=raw_input,
                ok=False,
                result="提醒时间需要和截止时间一起设置，且优先级必须为大于等于 0 的整数。",
            )
        result = (
            f"已添加待办 #{added_todo_id} [标签:{add_tag}]: {content}"
            f"{_format_todo_meta_inline(add_due_at_text, add_remind_at_text, priority=add_priority)}"
        )
        return PlannerObservation(tool="todo", input_text=raw_input, ok=True, result=result)

    if action in {"list", "view"}:
        if action == "view":
            list_view = _normalize_todo_view_value(payload.get("view"))
            if list_view is None:
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="todo.view 需要合法 view(all|today|overdue|upcoming|inbox)。",
                )
        else:
            if "view" in payload:
                list_view = _normalize_todo_view_value(payload.get("view"))
                if list_view is None:
                    return PlannerObservation(
                        tool="todo",
                        input_text=raw_input,
                        ok=False,
                        result="todo.list 的 view 参数非法。",
                    )
            else:
                list_view = "all"
        list_tag = _normalize_todo_tag_value(payload.get("tag"))
        todos = agent.db.list_todos(tag=list_tag)
        todos = _filter_todos_by_view(todos, view_name=list_view)
        if not todos:
            result = _todo_list_empty_text(tag=list_tag, view_name=list_view)
            return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result=result)

        header = _todo_list_header(tag=list_tag, view_name=list_view)
        table = _render_todo_table(todos)
        return PlannerObservation(tool="todo", input_text=raw_input, ok=True, result=f"{header}\n{table}")

    if action == "search":
        keyword = str(payload.get("keyword") or "").strip()
        if not keyword:
            return PlannerObservation(
                tool="todo",
                input_text=raw_input,
                ok=False,
                result="todo.search 缺少 keyword。",
            )
        search_tag = _normalize_todo_tag_value(payload.get("tag"))
        todos = agent.db.search_todos(keyword, tag=search_tag)
        if not todos:
            return PlannerObservation(
                tool="todo",
                input_text=raw_input,
                ok=False,
                result=_todo_search_empty_text(keyword=keyword, tag=search_tag),
            )
        table = _render_todo_table(todos)
        header = _todo_search_header(keyword=keyword, tag=search_tag)
        return PlannerObservation(tool="todo", input_text=raw_input, ok=True, result=f"{header}\n{table}")

    todo_id = _normalize_positive_int_value(payload.get("id"))
    if todo_id is None:
        return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result="todo.id 必须为正整数。")

    if action == "get":
        todo = agent.db.get_todo(todo_id)
        if todo is None:
            return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result=f"未找到待办 #{todo_id}")
        table = _render_todo_table([todo])
        return PlannerObservation(tool="todo", input_text=raw_input, ok=True, result=f"待办详情:\n{table}")

    if action == "update":
        content = str(payload.get("content") or "").strip()
        if not content:
            return PlannerObservation(
                tool="todo",
                input_text=raw_input,
                ok=False,
                result="todo.update 缺少 content。",
            )
        current = agent.db.get_todo(todo_id)
        if current is None:
            return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result=f"未找到待办 #{todo_id}")
        has_priority = "priority" in payload
        if has_priority:
            update_priority = _normalize_todo_priority_value(payload.get("priority"))
            if update_priority is None:
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="todo.update priority 需为 >=0 的整数。",
                )
        else:
            update_priority = None
        has_due = "due_at" in payload
        update_due_at = _normalize_optional_datetime_value(payload.get("due_at"), key_present=has_due)
        if update_due_at is _INVALID_OPTION_VALUE:
            return PlannerObservation(
                tool="todo",
                input_text=raw_input,
                ok=False,
                result="todo.update due_at 格式非法，需为 YYYY-MM-DD HH:MM。",
            )
        has_remind = "remind_at" in payload
        update_remind_at = _normalize_optional_datetime_value(payload.get("remind_at"), key_present=has_remind)
        if update_remind_at is _INVALID_OPTION_VALUE:
            return PlannerObservation(
                tool="todo",
                input_text=raw_input,
                ok=False,
                result="todo.update remind_at 格式非法，需为 YYYY-MM-DD HH:MM。",
            )
        if has_remind and update_remind_at and not ((has_due and update_due_at) or current.due_at):
            return PlannerObservation(
                tool="todo",
                input_text=raw_input,
                ok=False,
                result="提醒时间需要和截止时间一起设置。",
            )
        update_kwargs: dict[str, Any] = {"content": content}
        if "tag" in payload:
            update_tag = _normalize_todo_tag_value(payload.get("tag"))
            if update_tag:
                update_kwargs["tag"] = update_tag
        if has_priority:
            update_kwargs["priority"] = update_priority
        if has_due:
            update_kwargs["due_at"] = update_due_at
        if has_remind:
            update_kwargs["remind_at"] = update_remind_at
        updated = agent.db.update_todo(todo_id, **update_kwargs)
        if not updated:
            return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result=f"未找到待办 #{todo_id}")
        todo = agent.db.get_todo(todo_id)
        if todo is None:
            result = f"已更新待办 #{todo_id}: {content}"
        else:
            result = (
                f"已更新待办 #{todo_id} [标签:{todo.tag}]: {content}"
                f"{_format_todo_meta_inline(todo.due_at, todo.remind_at, priority=todo.priority)}"
            )
        ok = _is_planner_command_success(result, tool="todo")
        return PlannerObservation(tool="todo", input_text=raw_input, ok=ok, result=result)

    if action == "delete":
        deleted = agent.db.delete_todo(todo_id)
        if not deleted:
            result = f"未找到待办 #{todo_id}"
        else:
            result = f"待办 #{todo_id} 已删除。"
        ok = _is_planner_command_success(result, tool="todo")
        return PlannerObservation(tool="todo", input_text=raw_input, ok=ok, result=result)

    # done
    done = agent.db.mark_todo_done(todo_id)
    if not done:
        result = f"未找到待办 #{todo_id}"
    else:
        todo = agent.db.get_todo(todo_id)
        done_completed_at = todo.completed_at if todo is not None else _now_time_text()
        result = f"待办 #{todo_id} 已完成。完成时间: {done_completed_at}"
    ok = _is_planner_command_success(result, tool="todo")
    return PlannerObservation(tool="todo", input_text=raw_input, ok=ok, result=result)
