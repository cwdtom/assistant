from __future__ import annotations

import json
import re
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any

from assistant_app.db import AssistantDB
from assistant_app.llm import LLMClient

SCHEDULE_EVENT_PREFIX_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+(.+)$")
SCHEDULE_REPEAT_OPTION_PATTERN = re.compile(r"(^|\s)--repeat\s+(none|daily|weekly|monthly)")
SCHEDULE_TIMES_OPTION_PATTERN = re.compile(r"(^|\s)--times\s+(\d+)")
SCHEDULE_DURATION_OPTION_PATTERN = re.compile(r"(^|\s)--duration\s+(\d+)")
TODO_TAG_OPTION_PATTERN = re.compile(r"(^|\s)--tag\s+(\S+)")
TODO_VIEW_OPTION_PATTERN = re.compile(r"(^|\s)--view\s+(\S+)")
TODO_PRIORITY_OPTION_PATTERN = re.compile(r"(^|\s)--priority\s+(-?\d+)")
TODO_DUE_OPTION_PATTERN = re.compile(r"(^|\s)--due\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})")
TODO_REMIND_OPTION_PATTERN = re.compile(r"(^|\s)--remind\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})")
INTENT_LABELS = (
    "todo_add",
    "todo_get",
    "todo_list",
    "todo_view",
    "todo_search",
    "todo_update",
    "todo_delete",
    "todo_done",
    "schedule_add",
    "schedule_get",
    "schedule_list",
    "schedule_view",
    "schedule_update",
    "schedule_delete",
    "chat",
)
INTENT_JSON_RETRY_COUNT = 2
INTENT_SERVICE_UNAVAILABLE = "__intent_service_unavailable__"
TODO_VIEW_NAMES = ("all", "today", "overdue", "upcoming", "inbox")
SCHEDULE_REPEAT_NAMES = ("none", "daily", "weekly", "monthly")
SCHEDULE_VIEW_NAMES = ("day", "week", "month")

INTENT_ANALYZE_PROMPT = """
你是 CLI 助手的意图识别器。你必须只输出一个 json 对象，不能输出任何其他内容。

可选 intent:
- todo_add
- todo_get
- todo_list
- todo_view
- todo_search
- todo_update
- todo_delete
- todo_done
- schedule_add
- schedule_get
- schedule_list
- schedule_view
- schedule_update
- schedule_delete
- chat

输出 JSON 字段固定为:
{{
  "intent": "one_of_supported_intents",
  "todo_content": "string|null",
  "todo_tag": "string|null",
  "todo_view": "all|today|overdue|upcoming|inbox|null",
  "todo_priority": "number|null",
  "todo_due_time": "YYYY-MM-DD HH:MM|null",
  "todo_remind_time": "YYYY-MM-DD HH:MM|null",
  "todo_id": "number|null",
  "schedule_id": "number|null",
  "event_time": "YYYY-MM-DD HH:MM|null",
  "title": "string|null",
  "schedule_repeat": "none|daily|weekly|monthly|null",
  "schedule_repeat_times": "number|null",
  "schedule_duration_minutes": "number|null",
  "schedule_view": "day|week|month|null",
  "schedule_view_date": "YYYY-MM-DD|YYYY-MM|null"
}}

规则:
- 只在用户明确要操作待办/日程时，返回对应 intent，否则 intent=chat
- 与 intent 无关的字段必须填 null
- 缺少或不确定的字段填 null
- todo_get/todo_update/todo_delete/todo_done 需要 todo_id
- schedule_get/schedule_update/schedule_delete 需要 schedule_id
- todo_update 需要 todo_content
- todo_view 需要 todo_view 字段
- todo_search 需要 todo_content
- todo_priority 必须是 >=0 的整数，无法确定就填 null
- schedule_repeat 如出现必须是 none/daily/weekly/monthly
- schedule_repeat_times 如出现必须是 >=1 的整数
- schedule_duration_minutes 如出现必须是 >=1 的整数
- schedule_view 如出现必须是 day/week/month
- schedule_view_date 仅用于 schedule_view。day/week 用 YYYY-MM-DD，month 用 YYYY-MM
- todo_remind_time 仅在有 todo_due_time 时可填写
- schedule_add/schedule_update 需要 event_time 和 title
- event_time 必须是 YYYY-MM-DD HH:MM，无法确定就填 null
- todo_due_time/todo_remind_time 必须是 YYYY-MM-DD HH:MM，无法确定就填 null
""".strip()


class AssistantAgent:
    def __init__(self, db: AssistantDB, llm_client: LLMClient | None = None) -> None:
        self.db = db
        self.llm_client = llm_client

    def handle_input(self, user_input: str) -> str:
        text = user_input.strip()
        if not text:
            return "请输入内容。输入 /help 查看可用命令。"

        if text.startswith("/"):
            return self._handle_command(text)

        if not self.llm_client:
            return "当前未配置 LLM。请设置 DEEPSEEK_API_KEY 后重试。"

        intent_payload = self._analyze_intent(text)
        intent_response = self._dispatch_intent(intent_payload)
        if intent_response is not None:
            return intent_response

        return self._handle_chat(text)

    def _handle_command(self, command: str) -> str:
        if command == "/help":
            return self._help_text()

        if command == "/view list":
            return self._todo_view_list_text()

        if command.startswith("/view "):
            view_parsed = _parse_view_command_input(command.removeprefix("/view ").strip())
            if view_parsed is None:
                return "用法: /view <all|today|overdue|upcoming|inbox> [--tag <标签>]"
            view_name, view_tag = view_parsed
            list_cmd = f"/todo list --view {view_name}"
            if view_tag is not None:
                list_cmd += f" --tag {view_tag}"
            return self._handle_command(list_cmd)

        if command.startswith("/todo add "):
            add_parsed = _parse_todo_add_input(command.removeprefix("/todo add ").strip())
            if add_parsed is None:
                return (
                    "用法: /todo add <内容> [--tag <标签>] [--priority <>=0>] "
                    "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]"
                )
            content, add_tag, add_priority, add_due_at, add_remind_at = add_parsed
            if not content:
                return (
                    "用法: /todo add <内容> [--tag <标签>] [--priority <>=0>] "
                    "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]"
                )
            try:
                added_todo_id = self.db.add_todo(
                    content,
                    tag=add_tag,
                    priority=add_priority,
                    due_at=add_due_at,
                    remind_at=add_remind_at,
                )
            except ValueError:
                return "提醒时间需要和截止时间一起设置，且优先级必须为大于等于 0 的整数。"
            return (
                f"已添加待办 #{added_todo_id} [标签:{add_tag}]: {content}"
                f"{_format_todo_meta_inline(add_due_at, add_remind_at, priority=add_priority)}"
            )

        if command == "/todo list" or command.startswith("/todo list "):
            list_parsed = _parse_todo_list_options(command)
            if list_parsed is None:
                return "用法: /todo list [--tag <标签>] [--view <all|today|overdue|upcoming|inbox>]"
            list_tag, list_view = list_parsed
            todos = self.db.list_todos(tag=list_tag)
            todos = _filter_todos_by_view(todos, view_name=list_view)
            if not todos:
                if list_tag is None and list_view == "all":
                    return "暂无待办。"
                if list_tag is None:
                    return f"视图 {list_view} 下暂无待办。"
                if list_view == "all":
                    return f"标签 {list_tag} 下暂无待办。"
                return f"标签 {list_tag} 的 {list_view} 视图下暂无待办。"

            header_parts: list[str] = []
            if list_tag is not None:
                header_parts.append(f"标签: {list_tag}")
            if list_view != "all":
                header_parts.append(f"视图: {list_view}")
            if header_parts:
                header = f"待办列表({', '.join(header_parts)}):"
            else:
                header = "待办列表:"
            rows = [
                [
                    str(item.id),
                    "完成" if item.done else "待办",
                    item.tag,
                    str(item.priority),
                    item.content,
                    item.created_at,
                    item.completed_at or "-",
                    item.due_at or "-",
                    item.remind_at or "-",
                ]
                for item in todos
            ]
            table = _render_table(
                headers=["ID", "状态", "标签", "优先级", "内容", "创建时间", "完成时间", "截止时间", "提醒时间"],
                rows=rows,
            )
            return f"{header}\n{table}"

        if command.startswith("/todo search "):
            search_parsed = _parse_todo_search_input(command.removeprefix("/todo search ").strip())
            if search_parsed is None:
                return "用法: /todo search <关键词> [--tag <标签>]"
            keyword, search_tag = search_parsed
            todos = self.db.search_todos(keyword, tag=search_tag)
            if not todos:
                if search_tag is None:
                    return f"未找到包含“{keyword}”的待办。"
                return f"未在标签 {search_tag} 下找到包含“{keyword}”的待办。"

            rows = [
                [
                    str(item.id),
                    "完成" if item.done else "待办",
                    item.tag,
                    str(item.priority),
                    item.content,
                    item.created_at,
                    item.completed_at or "-",
                    item.due_at or "-",
                    item.remind_at or "-",
                ]
                for item in todos
            ]
            table = _render_table(
                headers=["ID", "状态", "标签", "优先级", "内容", "创建时间", "完成时间", "截止时间", "提醒时间"],
                rows=rows,
            )
            if search_tag is None:
                header = f"搜索结果(关键词: {keyword}):"
            else:
                header = f"搜索结果(关键词: {keyword}, 标签: {search_tag}):"
            return f"{header}\n{table}"

        if command.startswith("/todo get "):
            get_todo_id = _parse_positive_int(command.removeprefix("/todo get ").strip())
            if get_todo_id is None:
                return "用法: /todo get <id>"
            todo = self.db.get_todo(get_todo_id)
            if todo is None:
                return f"未找到待办 #{get_todo_id}"
            status = "x" if todo.done else " "
            completed_at = todo.completed_at or "-"
            due_at = todo.due_at or "-"
            remind_at = todo.remind_at or "-"
            table = _render_table(
                headers=["ID", "状态", "标签", "优先级", "内容", "创建时间", "完成时间", "截止时间", "提醒时间"],
                rows=[
                    [
                        str(todo.id),
                        "完成" if status == "x" else "待办",
                        todo.tag,
                        str(todo.priority),
                        todo.content,
                        todo.created_at,
                        completed_at,
                        due_at,
                        remind_at,
                    ]
                ],
            )
            return f"待办详情:\n{table}"

        if command.startswith("/todo update "):
            update_parsed = _parse_todo_update_input(command.removeprefix("/todo update ").strip())
            if update_parsed is None:
                return (
                    "用法: /todo update <id> <内容> [--tag <标签>] "
                    "[--priority <>=0>] [--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]"
                )
            (
                update_todo_id,
                content,
                update_tag,
                update_priority,
                update_due_at,
                update_remind_at,
                has_priority,
                has_due,
                has_remind,
            ) = update_parsed
            current = self.db.get_todo(update_todo_id)
            if current is None:
                return f"未找到待办 #{update_todo_id}"

            if has_remind and update_remind_at and not ((has_due and update_due_at) or current.due_at):
                return "提醒时间需要和截止时间一起设置。"

            update_kwargs: dict[str, Any] = {"content": content}
            if update_tag is not None:
                update_kwargs["tag"] = update_tag
            if has_priority:
                update_kwargs["priority"] = update_priority
            if has_due:
                update_kwargs["due_at"] = update_due_at
            if has_remind:
                update_kwargs["remind_at"] = update_remind_at

            updated = self.db.update_todo(update_todo_id, **update_kwargs)
            if not updated:
                return f"未找到待办 #{update_todo_id}"
            todo = self.db.get_todo(update_todo_id)
            if todo is None:
                return f"已更新待办 #{update_todo_id}: {content}"
            return (
                f"已更新待办 #{update_todo_id} [标签:{todo.tag}]: {content}"
                f"{_format_todo_meta_inline(todo.due_at, todo.remind_at, priority=todo.priority)}"
            )

        if command.startswith("/todo delete "):
            delete_todo_id = _parse_positive_int(command.removeprefix("/todo delete ").strip())
            if delete_todo_id is None:
                return "用法: /todo delete <id>"
            deleted = self.db.delete_todo(delete_todo_id)
            if not deleted:
                return f"未找到待办 #{delete_todo_id}"
            return f"待办 #{delete_todo_id} 已删除。"

        if command.startswith("/todo done "):
            id_text = command.removeprefix("/todo done ").strip()
            if not id_text.isdigit():
                return "用法: /todo done <id>"
            done = self.db.mark_todo_done(int(id_text))
            if not done:
                return f"未找到待办 #{id_text}"
            todo = self.db.get_todo(int(id_text))
            done_completed_at = todo.completed_at if todo is not None else _now_time_text()
            return f"待办 #{id_text} 已完成。完成时间: {done_completed_at}"

        if command == "/schedule list":
            items = self.db.list_schedules()
            if not items:
                return "暂无日程。"
            table = _render_table(
                headers=["ID", "时间", "时长(分钟)", "标题", "创建时间"],
                rows=[
                    [str(item.id), item.event_time, str(item.duration_minutes), item.title, item.created_at]
                    for item in items
                ],
            )
            return f"日程列表:\n{table}"

        if command.startswith("/schedule view "):
            view_parsed = _parse_schedule_view_input(command.removeprefix("/schedule view ").strip())
            if view_parsed is None:
                return "用法: /schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM]"
            view_name, anchor = view_parsed
            items = _filter_schedules_by_calendar_view(self.db.list_schedules(), view_name=view_name, anchor=anchor)
            if not items:
                return f"{view_name} 视图下暂无日程。"
            table = _render_table(
                headers=["ID", "时间", "时长(分钟)", "标题", "创建时间"],
                rows=[
                    [str(item.id), item.event_time, str(item.duration_minutes), item.title, item.created_at]
                    for item in items
                ],
            )
            if anchor:
                return f"日历视图({view_name}, {anchor}):\n{table}"
            return f"日历视图({view_name}):\n{table}"

        if command.startswith("/schedule get "):
            schedule_id = _parse_positive_int(command.removeprefix("/schedule get ").strip())
            if schedule_id is None:
                return "用法: /schedule get <id>"
            item = self.db.get_schedule(schedule_id)
            if item is None:
                return f"未找到日程 #{schedule_id}"
            table = _render_table(
                headers=["ID", "时间", "时长(分钟)", "标题", "创建时间"],
                rows=[[str(item.id), item.event_time, str(item.duration_minutes), item.title, item.created_at]],
            )
            return f"日程详情:\n{table}"

        if command.startswith("/schedule add"):
            add_schedule_parsed = _parse_schedule_add_input(command.removeprefix("/schedule add").strip())
            if add_schedule_parsed is None:
                return (
                    "用法: /schedule add <YYYY-MM-DD HH:MM> <标题> "
                    "[--duration <>=1>] [--repeat <none|daily|weekly|monthly>] [--times <>=1>]"
                )
            event_time, title, duration_minutes, repeat_name, repeat_times = add_schedule_parsed
            event_times = _build_schedule_event_times(
                event_time=event_time,
                repeat_name=repeat_name,
                repeat_times=repeat_times,
            )
            conflicts = self.db.find_schedule_conflicts(event_times)
            if conflicts:
                return _format_schedule_conflicts(conflicts)
            created_ids = self.db.add_schedules(
                title=title,
                event_times=event_times,
                duration_minutes=duration_minutes,
            )
            if len(created_ids) == 1:
                return f"已添加日程 #{created_ids[0]}: {event_time} {title} ({duration_minutes} 分钟)"
            return (
                f"已添加重复日程 {len(created_ids)} 条: {event_time} {title} "
                f"(duration={duration_minutes}m, repeat={repeat_name}, times={repeat_times})"
            )

        if command.startswith("/schedule update "):
            update_schedule_parsed = _parse_schedule_update_input(command.removeprefix("/schedule update ").strip())
            if update_schedule_parsed is None:
                return (
                    "用法: /schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
                    "[--duration <>=1>] [--repeat <none|daily|weekly|monthly>] [--times <>=1>]"
                )
            schedule_id, event_time, title, parsed_duration_minutes, repeat_name, repeat_times = update_schedule_parsed
            current_item = self.db.get_schedule(schedule_id)
            if current_item is None:
                return f"未找到日程 #{schedule_id}"
            if parsed_duration_minutes is not None:
                applied_duration_minutes = parsed_duration_minutes
            else:
                applied_duration_minutes = current_item.duration_minutes
            event_times = _build_schedule_event_times(
                event_time=event_time,
                repeat_name=repeat_name,
                repeat_times=repeat_times,
            )
            conflicts = self.db.find_schedule_conflicts(event_times, exclude_schedule_id=schedule_id)
            if conflicts:
                return _format_schedule_conflicts(conflicts)
            updated = self.db.update_schedule(
                schedule_id,
                title=title,
                event_time=event_time,
                duration_minutes=applied_duration_minutes,
            )
            if not updated:
                return f"未找到日程 #{schedule_id}"
            if repeat_times <= 1:
                return f"已更新日程 #{schedule_id}: {event_time} {title} ({applied_duration_minutes} 分钟)"
            follow_up_times = event_times[1:]
            created_ids = self.db.add_schedules(
                title=title,
                event_times=follow_up_times,
                duration_minutes=applied_duration_minutes,
            )
            return (
                f"已更新日程 #{schedule_id}: {event_time} {title} "
                f"(duration={applied_duration_minutes}m, repeat={repeat_name}, 新增 {len(created_ids)} 条后续日程)"
            )

        if command.startswith("/schedule delete "):
            schedule_id = _parse_positive_int(command.removeprefix("/schedule delete ").strip())
            if schedule_id is None:
                return "用法: /schedule delete <id>"
            deleted = self.db.delete_schedule(schedule_id)
            if not deleted:
                return f"未找到日程 #{schedule_id}"
            return f"日程 #{schedule_id} 已删除。"

        return "未知命令。输入 /help 查看可用命令。"

    def _analyze_intent(self, text: str) -> dict[str, Any]:
        if not self.llm_client:
            return {"intent": "chat"}

        messages = [
            {"role": "system", "content": INTENT_ANALYZE_PROMPT},
            {"role": "user", "content": text},
        ]
        max_attempts = 1 + INTENT_JSON_RETRY_COUNT

        for _ in range(max_attempts):
            try:
                raw = self._llm_reply_for_intent(messages)
            except Exception:
                continue

            cleaned = _strip_think_blocks(raw).strip()
            payload = _try_parse_json(cleaned)
            if isinstance(payload, dict):
                intent = str(payload.get("intent", "")).strip().lower()
                if intent not in INTENT_LABELS:
                    intent = "chat"
                payload["intent"] = intent
                if self._intent_requires_retry(payload):
                    continue
                return payload

        return {"intent": INTENT_SERVICE_UNAVAILABLE}

    def _intent_requires_retry(self, payload: dict[str, Any]) -> bool:
        intent = str(payload.get("intent", "chat")).strip().lower()

        if intent == "todo_add":
            content = str(payload.get("todo_content") or "").strip()
            due_time = str(payload.get("todo_due_time") or "").strip()
            remind_time = str(payload.get("todo_remind_time") or "").strip()
            priority_raw = payload.get("todo_priority")
            priority = _normalize_todo_priority_value(priority_raw)
            if not content:
                return True
            if priority_raw is not None and priority is None:
                return True
            if due_time and not _is_valid_datetime_text(due_time):
                return True
            if remind_time and not _is_valid_datetime_text(remind_time):
                return True
            if remind_time and not due_time:
                return True
            return False

        if intent in {"todo_get", "todo_delete", "todo_done"}:
            todo_id = payload.get("todo_id")
            return self._is_invalid_positive_id(todo_id)

        if intent == "todo_view":
            view_name = _normalize_todo_view_value(payload.get("todo_view"))
            return view_name is None

        if intent == "todo_search":
            keyword = str(payload.get("todo_content") or "").strip()
            return not keyword

        if intent == "todo_update":
            todo_id = payload.get("todo_id")
            content = str(payload.get("todo_content") or "").strip()
            due_time = str(payload.get("todo_due_time") or "").strip()
            remind_time = str(payload.get("todo_remind_time") or "").strip()
            priority_raw = payload.get("todo_priority")
            priority = _normalize_todo_priority_value(priority_raw)
            if self._is_invalid_positive_id(todo_id) or not content:
                return True
            if priority_raw is not None and priority is None:
                return True
            if due_time and not _is_valid_datetime_text(due_time):
                return True
            if remind_time and not _is_valid_datetime_text(remind_time):
                return True
            if remind_time and not due_time:
                current = self.db.get_todo(self._to_int(todo_id))
                if current is None or not current.due_at:
                    return True
            return False

        if intent == "schedule_add":
            event_time = str(payload.get("event_time") or "").strip()
            title = str(payload.get("title") or "").strip()
            repeat_raw = payload.get("schedule_repeat")
            repeat_name = _normalize_schedule_repeat_value(repeat_raw) or "none"
            repeat_times_raw = payload.get("schedule_repeat_times")
            repeat_times = _normalize_schedule_repeat_times_value(repeat_times_raw)
            duration_raw = payload.get("schedule_duration_minutes")
            duration_minutes = _normalize_schedule_duration_minutes_value(duration_raw)
            if not event_time or not title:
                return True
            if repeat_raw is not None and _normalize_schedule_repeat_value(repeat_raw) is None:
                return True
            if repeat_times_raw is not None and repeat_times is None:
                return True
            if duration_raw is not None and duration_minutes is None:
                return True
            if repeat_name == "none" and (repeat_times or 1) > 1:
                return True
            return re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", event_time) is None

        if intent in {"schedule_get", "schedule_delete"}:
            schedule_id = payload.get("schedule_id")
            return self._is_invalid_positive_id(schedule_id)

        if intent == "schedule_view":
            view_name = _normalize_schedule_view_value(payload.get("schedule_view"))
            date_text = str(payload.get("schedule_view_date") or "").strip()
            if view_name is None:
                return True
            if not date_text:
                return False
            return _normalize_schedule_view_anchor(view_name=view_name, value=date_text) is None

        if intent == "schedule_update":
            schedule_id = payload.get("schedule_id")
            event_time = str(payload.get("event_time") or "").strip()
            title = str(payload.get("title") or "").strip()
            repeat_raw = payload.get("schedule_repeat")
            repeat_name = _normalize_schedule_repeat_value(repeat_raw) or "none"
            repeat_times_raw = payload.get("schedule_repeat_times")
            repeat_times = _normalize_schedule_repeat_times_value(repeat_times_raw)
            duration_raw = payload.get("schedule_duration_minutes")
            duration_minutes = _normalize_schedule_duration_minutes_value(duration_raw)
            if self._is_invalid_positive_id(schedule_id):
                return True
            if not event_time or not title:
                return True
            if repeat_raw is not None and _normalize_schedule_repeat_value(repeat_raw) is None:
                return True
            if repeat_times_raw is not None and repeat_times is None:
                return True
            if duration_raw is not None and duration_minutes is None:
                return True
            if repeat_name == "none" and (repeat_times or 1) > 1:
                return True
            return re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", event_time) is None

        return False

    def _is_invalid_positive_id(self, value: Any) -> bool:
        if value is None:
            return True
        try:
            return self._to_int(value) <= 0
        except (TypeError, ValueError):
            return True

    def _llm_reply_for_intent(self, messages: list[dict[str, str]]) -> str:
        if self.llm_client is None:
            return ""

        reply_json = getattr(self.llm_client, "reply_json", None)
        if callable(reply_json):
            try:
                return str(reply_json(messages))
            except Exception:
                # Some OpenAI-compatible providers don't support response_format.
                pass
        return self.llm_client.reply(messages)

    def _dispatch_intent(self, payload: dict[str, Any]) -> str | None:
        intent = str(payload.get("intent", "chat")).lower()

        if intent == INTENT_SERVICE_UNAVAILABLE:
            return "抱歉，当前意图识别服务暂时不可用。你可以稍后重试，或先使用 /todo、/schedule 命令继续操作。"

        if intent == "todo_view":
            view_name = _normalize_todo_view_value(payload.get("todo_view"))
            if view_name is None:
                return "我识别到你可能要查看待办视图，但视图名称不完整。"
            tag = _normalize_todo_tag_value(payload.get("todo_tag"))
            cmd = f"/todo list --view {view_name}"
            if tag is not None:
                cmd += f" --tag {tag}"
            return self._handle_command(cmd)

        if intent == "todo_list":
            tag = _normalize_todo_tag_value(payload.get("todo_tag"))
            if tag is None:
                return self._handle_command("/todo list")
            return self._handle_command(f"/todo list --tag {tag}")

        if intent == "todo_search":
            keyword = str(payload.get("todo_content") or "").strip()
            if not keyword:
                return "我识别到你可能要搜索待办，但缺少关键词。"
            tag = _normalize_todo_tag_value(payload.get("todo_tag"))
            cmd = f"/todo search {keyword}"
            if tag is not None:
                cmd += f" --tag {tag}"
            return self._handle_command(cmd)

        if intent == "todo_get":
            todo_id = payload.get("todo_id")
            if todo_id is None:
                return "我识别到你可能要查看待办，但缺少编号。请告诉我待办 id。"
            return self._handle_command(f"/todo get {self._to_int(todo_id)}")

        if intent == "todo_update":
            todo_id = payload.get("todo_id")
            content = str(payload.get("todo_content") or "").strip()
            if todo_id is None or not content:
                return "我识别到你可能要修改待办，但编号或内容不完整。"
            tag = _normalize_todo_tag_value(payload.get("todo_tag"))
            priority = _normalize_todo_priority_value(payload.get("todo_priority"))
            due_time_raw = str(payload.get("todo_due_time") or "").strip()
            remind_time_raw = str(payload.get("todo_remind_time") or "").strip()
            due_time = _normalize_datetime_text(due_time_raw) if due_time_raw else None
            remind_time = _normalize_datetime_text(remind_time_raw) if remind_time_raw else None
            cmd = f"/todo update {self._to_int(todo_id)} {content}"
            if tag is not None:
                cmd += f" --tag {tag}"
            if priority is not None:
                cmd += f" --priority {priority}"
            if due_time:
                cmd += f" --due {due_time}"
            if remind_time:
                cmd += f" --remind {remind_time}"
            return self._handle_command(cmd)

        if intent == "todo_delete":
            todo_id = payload.get("todo_id")
            if todo_id is None:
                return "我识别到你可能要删除待办，但缺少编号。请告诉我待办 id。"
            return self._handle_command(f"/todo delete {self._to_int(todo_id)}")

        if intent == "schedule_list":
            return self._handle_command("/schedule list")

        if intent == "schedule_view":
            view_name = _normalize_schedule_view_value(payload.get("schedule_view"))
            if view_name is None:
                return "我识别到你可能要查看日历视图，但视图类型不完整。"
            anchor_raw = str(payload.get("schedule_view_date") or "").strip()
            anchor = _normalize_schedule_view_anchor(view_name=view_name, value=anchor_raw) if anchor_raw else None
            cmd = f"/schedule view {view_name}"
            if anchor is not None:
                cmd += f" {anchor}"
            return self._handle_command(cmd)

        if intent == "todo_add":
            content = str(payload.get("todo_content") or "").strip()
            if not content:
                return "我识别到你可能要添加待办，但缺少内容。请再说具体事项。"
            tag = _normalize_todo_tag_value(payload.get("todo_tag")) or "default"
            priority = _normalize_todo_priority_value(payload.get("todo_priority"))
            due_time_raw = str(payload.get("todo_due_time") or "").strip()
            remind_time_raw = str(payload.get("todo_remind_time") or "").strip()
            due_time = _normalize_datetime_text(due_time_raw) if due_time_raw else None
            remind_time = _normalize_datetime_text(remind_time_raw) if remind_time_raw else None
            cmd = f"/todo add {content} --tag {tag}"
            if priority is not None:
                cmd += f" --priority {priority}"
            if due_time:
                cmd += f" --due {due_time}"
            if remind_time:
                cmd += f" --remind {remind_time}"
            return self._handle_command(cmd)

        if intent == "todo_done":
            todo_id = payload.get("todo_id")
            if todo_id is None:
                return "我识别到你可能要完成待办，但缺少编号。请告诉我待办 id。"
            return self._handle_command(f"/todo done {self._to_int(todo_id)}")

        if intent == "schedule_add":
            event_time = str(payload.get("event_time") or "").strip()
            title = str(payload.get("title") or "").strip()
            if not event_time or not title:
                return "我识别到你可能要添加日程，但时间或标题不完整。"
            repeat_name = _normalize_schedule_repeat_value(payload.get("schedule_repeat")) or "none"
            repeat_times = _normalize_schedule_repeat_times_value(payload.get("schedule_repeat_times")) or 1
            duration_minutes = _normalize_schedule_duration_minutes_value(payload.get("schedule_duration_minutes"))
            cmd = f"/schedule add {event_time} {title}"
            if duration_minutes is not None:
                cmd += f" --duration {duration_minutes}"
            if repeat_name != "none":
                cmd += f" --repeat {repeat_name} --times {repeat_times}"
            return self._handle_command(cmd)

        if intent == "schedule_get":
            schedule_id = payload.get("schedule_id")
            if schedule_id is None:
                return "我识别到你可能要查看日程，但缺少编号。请告诉我日程 id。"
            return self._handle_command(f"/schedule get {self._to_int(schedule_id)}")

        if intent == "schedule_update":
            schedule_id = payload.get("schedule_id")
            event_time = str(payload.get("event_time") or "").strip()
            title = str(payload.get("title") or "").strip()
            if schedule_id is None or not event_time or not title:
                return "我识别到你可能要修改日程，但编号、时间或标题不完整。"
            repeat_name = _normalize_schedule_repeat_value(payload.get("schedule_repeat")) or "none"
            repeat_times = _normalize_schedule_repeat_times_value(payload.get("schedule_repeat_times")) or 1
            duration_minutes = _normalize_schedule_duration_minutes_value(payload.get("schedule_duration_minutes"))
            cmd = f"/schedule update {self._to_int(schedule_id)} {event_time} {title}"
            if duration_minutes is not None:
                cmd += f" --duration {duration_minutes}"
            if repeat_name != "none":
                cmd += f" --repeat {repeat_name} --times {repeat_times}"
            return self._handle_command(cmd)

        if intent == "schedule_delete":
            schedule_id = payload.get("schedule_id")
            if schedule_id is None:
                return "我识别到你可能要删除日程，但缺少编号。请告诉我日程 id。"
            return self._handle_command(f"/schedule delete {self._to_int(schedule_id)}")

        return None

    @staticmethod
    def _to_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        value_str = str(value).strip()
        if not value_str:
            return 0
        return int(float(value_str))

    def _handle_chat(self, text: str) -> str:
        if not self.llm_client:
            return "当前未配置 LLM。请设置 DEEPSEEK_API_KEY 后重试。"

        self.db.save_message("user", text)

        messages = [
            {
                "role": "system",
                "content": self._build_system_prompt(),
            }
        ]

        for item in self.db.recent_messages(limit=8):
            messages.append({"role": item.role, "content": item.content})

        try:
            answer = self.llm_client.reply(messages)
        except Exception as exc:  # noqa: BLE001
            return f"调用模型失败: {exc}"

        answer = _strip_think_blocks(answer).strip()
        if not answer:
            answer = "我这次没有拿到有效回复，可以再试一次。"

        self.db.save_message("assistant", answer)
        return answer

    def _build_system_prompt(self) -> str:
        todos = [item for item in self.db.list_todos() if not item.done][:5]
        schedules = self.db.list_schedules()[:5]

        todo_lines = (
            "\n".join(
                (
                    f"- {item.id}. [{item.tag}] {item.content}"
                    f"{_format_todo_meta_inline(item.due_at, item.remind_at, priority=item.priority)}"
                )
                for item in todos
            )
            or "- 无"
        )
        schedule_lines = "\n".join(f"- {item.event_time} {item.title}" for item in schedules) or "- 无"

        return (
            "你是一个中文优先的个人助手。回答尽量简洁、可执行。\n"
            "你可以参考当前用户的本地事项：\n"
            f"待办（未完成）:\n{todo_lines}\n"
            f"日程（按时间排序）:\n{schedule_lines}\n"
            "如果用户问到计划安排，优先结合这些事项给建议。"
        )

    @staticmethod
    def _todo_view_list_text() -> str:
        return (
            "可用视图:\n"
            "- all: 全部待办（含已完成）\n"
            "- today: 今天到期且未完成\n"
            "- overdue: 已逾期且未完成\n"
            "- upcoming: 未来 7 天到期且未完成\n"
            "- inbox: 未设置截止时间且未完成"
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "可用命令:\n"
            "/help\n"
            "/view list\n"
            "/view <all|today|overdue|upcoming|inbox> [--tag <标签>]\n"
            "/todo add <内容> [--tag <标签>] [--priority <>=0>] "
            "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]\n"
            "/todo list [--tag <标签>] [--view <all|today|overdue|upcoming|inbox>]\n"
            "/todo search <关键词> [--tag <标签>]\n"
            "/todo get <id>\n"
            "/todo update <id> <内容> [--tag <标签>] [--priority <>=0>] "
            "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]\n"
            "/todo delete <id>\n"
            "/todo done <id>\n"
            "/schedule add <YYYY-MM-DD HH:MM> <标题> "
            "[--duration <>=1>] [--repeat <none|daily|weekly|monthly>] [--times <>=1>]\n"
            "/schedule get <id>\n"
            "/schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM]\n"
            "/schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
            "[--duration <>=1>] [--repeat <none|daily|weekly|monthly>] [--times <>=1>]\n"
            "/schedule delete <id>\n"
            "/schedule list\n"
            "你也可以直接说自然语言（会先做意图识别，再执行动作）。\n"
            "其他文本会直接发给 AI。"
        )


def _parse_positive_int(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    value = int(raw)
    if value <= 0:
        return None
    return value


def _parse_todo_add_input(raw: str) -> tuple[str, str, int, str | None, str | None] | None:
    parsed = _parse_todo_text_with_options(raw, default_tag="default", default_priority=0)
    if parsed is None:
        return None
    content, tag, priority, due_at, remind_at, _, _, _ = parsed
    if remind_at and not due_at:
        return None
    if tag is None:
        tag = "default"
    if priority is None:
        priority = 0
    return content, tag, priority, due_at, remind_at


def _parse_todo_update_input(
    raw: str,
) -> tuple[int, str, str | None, int | None, str | None, str | None, bool, bool, bool] | None:
    parts = raw.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None

    todo_id = _parse_positive_int(parts[0])
    if todo_id is None:
        return None

    parsed = _parse_todo_text_with_options(parts[1], default_tag=None, default_priority=None)
    if parsed is None:
        return None
    content, tag, priority, due_at, remind_at, has_priority, has_due, has_remind = parsed
    return todo_id, content, tag, priority, due_at, remind_at, has_priority, has_due, has_remind


def _parse_todo_text_with_options(
    raw: str,
    *,
    default_tag: str | None,
    default_priority: int | None,
) -> tuple[str, str | None, int | None, str | None, str | None, bool, bool, bool] | None:
    text = raw.strip()
    if not text:
        return None

    working = text
    tag: str | None = default_tag
    priority: int | None = default_priority
    due_at: str | None = None
    remind_at: str | None = None
    has_priority = False
    has_due = False
    has_remind = False

    tag_match = TODO_TAG_OPTION_PATTERN.search(working)
    if tag_match:
        provided_tag = _sanitize_tag(tag_match.group(2))
        if not provided_tag:
            return None
        tag = provided_tag
        working = _remove_option_span(working, tag_match.span())

    priority_match = TODO_PRIORITY_OPTION_PATTERN.search(working)
    if priority_match:
        parsed_priority = _normalize_todo_priority_value(priority_match.group(2))
        if parsed_priority is None:
            return None
        priority = parsed_priority
        has_priority = True
        working = _remove_option_span(working, priority_match.span())

    due_match = TODO_DUE_OPTION_PATTERN.search(working)
    if due_match:
        parsed_due = _normalize_datetime_text(due_match.group(2))
        if not parsed_due:
            return None
        due_at = parsed_due
        has_due = True
        working = _remove_option_span(working, due_match.span())

    remind_match = TODO_REMIND_OPTION_PATTERN.search(working)
    if remind_match:
        parsed_remind = _normalize_datetime_text(remind_match.group(2))
        if not parsed_remind:
            return None
        remind_at = parsed_remind
        has_remind = True
        working = _remove_option_span(working, remind_match.span())

    content = re.sub(r"\s+", " ", working).strip()
    if not content:
        return None

    return content, tag, priority, due_at, remind_at, has_priority, has_due, has_remind


def _parse_todo_list_options(command: str) -> tuple[str | None, str] | None:
    if command == "/todo list":
        return None, "all"

    suffix = command.removeprefix("/todo list").strip()
    if not suffix:
        return None, "all"

    working = suffix
    tag: str | None = None
    view_name = "all"

    tag_match = TODO_TAG_OPTION_PATTERN.search(working)
    if tag_match:
        parsed_tag = _sanitize_tag(tag_match.group(2))
        if parsed_tag is None:
            return None
        tag = parsed_tag
        working = _remove_option_span(working, tag_match.span())

    view_match = TODO_VIEW_OPTION_PATTERN.search(working)
    if view_match:
        parsed_view = _normalize_todo_view_value(view_match.group(2))
        if parsed_view is None:
            return None
        view_name = parsed_view
        working = _remove_option_span(working, view_match.span())

    leftover = re.sub(r"\s+", " ", working).strip()
    if leftover:
        if " " in leftover:
            return None
        if leftover.startswith("--"):
            return None
        parsed_tag = _sanitize_tag(leftover)
        if parsed_tag is None:
            return None
        if tag is not None:
            return None
        tag = parsed_tag

    return tag, view_name


def _parse_view_command_input(raw: str) -> tuple[str, str | None] | None:
    text = raw.strip()
    if not text:
        return None

    parts = text.split(maxsplit=1)
    view_name = _normalize_todo_view_value(parts[0])
    if view_name is None:
        return None

    if len(parts) == 1:
        return view_name, None

    suffix = parts[1].strip()
    if not suffix:
        return view_name, None

    option_match = re.match(r"^--tag\s+(\S+)$", suffix)
    if option_match is None:
        return None
    tag = _sanitize_tag(option_match.group(1))
    if tag is None:
        return None
    return view_name, tag


def _parse_todo_search_input(raw: str) -> tuple[str, str | None] | None:
    text = raw.strip()
    if not text:
        return None

    working = text
    tag: str | None = None

    tag_match = TODO_TAG_OPTION_PATTERN.search(working)
    if tag_match:
        provided_tag = _sanitize_tag(tag_match.group(2))
        if not provided_tag:
            return None
        tag = provided_tag
        working = _remove_option_span(working, tag_match.span())

    keyword = re.sub(r"\s+", " ", working).strip()
    if not keyword:
        return None
    return keyword, tag


def _parse_schedule_add_input(raw: str) -> tuple[str, str, int, str, int] | None:
    parsed = _parse_schedule_input(raw, default_duration_minutes=60)
    if parsed is None:
        return None
    event_time, title, duration_minutes, repeat_name, repeat_times = parsed
    if duration_minutes is None:
        return None
    if repeat_name == "none" and repeat_times > 1:
        return None
    return event_time, title, duration_minutes, repeat_name, repeat_times


def _parse_schedule_update_input(raw: str) -> tuple[int, str, str, int | None, str, int] | None:
    parts = raw.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    schedule_id = _parse_positive_int(parts[0])
    if schedule_id is None:
        return None
    parsed = _parse_schedule_input(parts[1], default_duration_minutes=None)
    if parsed is None:
        return None
    event_time, title, duration_minutes, repeat_name, repeat_times = parsed
    if repeat_name == "none" and repeat_times > 1:
        return None
    return schedule_id, event_time, title, duration_minutes, repeat_name, repeat_times


def _parse_schedule_input(
    raw: str,
    *,
    default_duration_minutes: int | None,
) -> tuple[str, str, int | None, str, int] | None:
    text = raw.strip()
    if not text:
        return None
    matched = SCHEDULE_EVENT_PREFIX_PATTERN.match(text)
    if matched is None:
        return None
    event_time = matched.group(1)
    if not _is_valid_datetime_text(event_time):
        return None

    working = matched.group(2).strip()
    if not working:
        return None

    duration_minutes: int | None = default_duration_minutes
    repeat_name = "none"
    repeat_times = 1

    duration_match = SCHEDULE_DURATION_OPTION_PATTERN.search(working)
    if duration_match:
        parsed_duration = _normalize_schedule_duration_minutes_value(duration_match.group(2))
        if parsed_duration is None:
            return None
        duration_minutes = parsed_duration
        working = _remove_option_span(working, duration_match.span())

    repeat_match = SCHEDULE_REPEAT_OPTION_PATTERN.search(working)
    if repeat_match:
        parsed_repeat = _normalize_schedule_repeat_value(repeat_match.group(2))
        if parsed_repeat is None:
            return None
        repeat_name = parsed_repeat
        working = _remove_option_span(working, repeat_match.span())

    times_match = SCHEDULE_TIMES_OPTION_PATTERN.search(working)
    if times_match:
        parsed_times = _normalize_schedule_repeat_times_value(times_match.group(2))
        if parsed_times is None:
            return None
        repeat_times = parsed_times
        working = _remove_option_span(working, times_match.span())

    title = re.sub(r"\s+", " ", working).strip()
    if not title:
        return None
    if re.search(r"(^|\s)--(duration|repeat|times)\b", title):
        return None
    return event_time, title, duration_minutes, repeat_name, repeat_times


def _parse_schedule_view_input(raw: str) -> tuple[str, str | None] | None:
    text = raw.strip()
    if not text:
        return None
    parts = text.split(maxsplit=1)
    view_name = _normalize_schedule_view_value(parts[0])
    if view_name is None:
        return None
    if len(parts) == 1:
        return view_name, None
    anchor = _normalize_schedule_view_anchor(view_name=view_name, value=parts[1].strip())
    if anchor is None:
        return None
    return view_name, anchor


def _sanitize_tag(tag: str | None) -> str | None:
    if tag is None:
        return None
    normalized = tag.strip().lower()
    if not normalized:
        return None
    normalized = normalized.lstrip("#")
    if not normalized:
        return None
    return re.sub(r"\s+", "-", normalized)


def _normalize_todo_tag_value(value: Any) -> str | None:
    if value is None:
        return None
    return _sanitize_tag(str(value))


def _normalize_todo_view_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in TODO_VIEW_NAMES:
        return text
    return None


def _normalize_schedule_repeat_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in SCHEDULE_REPEAT_NAMES:
        return text
    return None


def _normalize_schedule_view_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in SCHEDULE_VIEW_NAMES:
        return text
    return None


def _normalize_schedule_repeat_times_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        return parsed if parsed >= 1 else None
    text = str(value).strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    if parsed < 1:
        return None
    return parsed


def _normalize_schedule_duration_minutes_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        return parsed if parsed >= 1 else None
    text = str(value).strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    if parsed < 1:
        return None
    return parsed


def _normalize_schedule_view_anchor(*, view_name: str, value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if view_name in {"day", "week"}:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return parsed.strftime("%Y-%m-%d")
    if view_name == "month":
        try:
            parsed = datetime.strptime(text, "%Y-%m")
        except ValueError:
            return None
        return parsed.strftime("%Y-%m")
    return None


def _normalize_todo_priority_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
        return value if value >= 0 else None

    text = str(value).strip()
    if not re.fullmatch(r"-?\d+", text):
        return None
    parsed = int(text)
    if parsed < 0:
        return None
    return parsed


def _normalize_datetime_text(value: str) -> str | None:
    text = re.sub(r"\s+", " ", value).strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d %H:%M")


def _filter_todos_by_view(todos: list[Any], *, view_name: str, now: datetime | None = None) -> list[Any]:
    if view_name == "all":
        return todos

    current = now or datetime.now()
    today = current.date()
    today_end = datetime.combine(today, datetime.max.time())
    upcoming_end = current + timedelta(days=7)

    filtered: list[Any] = []
    for item in todos:
        if item.done:
            continue
        due_at = _parse_due_datetime(item.due_at)

        if view_name == "today":
            if due_at is not None and due_at.date() == today:
                filtered.append(item)
            continue

        if view_name == "overdue":
            if due_at is not None and due_at < current:
                filtered.append(item)
            continue

        if view_name == "upcoming":
            if due_at is not None and today_end < due_at <= upcoming_end:
                filtered.append(item)
            continue

        if view_name == "inbox":
            if due_at is None:
                filtered.append(item)
            continue

    return filtered


def _filter_schedules_by_calendar_view(
    schedules: list[Any],
    *,
    view_name: str,
    anchor: str | None,
    now: datetime | None = None,
) -> list[Any]:
    if view_name not in SCHEDULE_VIEW_NAMES:
        return schedules

    current = now or datetime.now()
    if anchor:
        if view_name == "month":
            anchor_time = datetime.strptime(anchor, "%Y-%m")
        else:
            anchor_time = datetime.strptime(anchor, "%Y-%m-%d")
    else:
        anchor_time = current

    if view_name == "day":
        start = datetime.combine(anchor_time.date(), datetime.min.time())
        end = start + timedelta(days=1)
    elif view_name == "week":
        week_start_date = anchor_time.date() - timedelta(days=anchor_time.weekday())
        start = datetime.combine(week_start_date, datetime.min.time())
        end = start + timedelta(days=7)
    else:
        month_start = datetime(anchor_time.year, anchor_time.month, 1)
        if anchor_time.month == 12:
            month_end = datetime(anchor_time.year + 1, 1, 1)
        else:
            month_end = datetime(anchor_time.year, anchor_time.month + 1, 1)
        start, end = month_start, month_end

    filtered: list[Any] = []
    for item in schedules:
        event_time = _parse_due_datetime(item.event_time)
        if event_time is None:
            continue
        if start <= event_time < end:
            filtered.append(item)
    return filtered


def _parse_due_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _build_schedule_event_times(*, event_time: str, repeat_name: str, repeat_times: int) -> list[str]:
    base = datetime.strptime(event_time, "%Y-%m-%d %H:%M")
    times = max(repeat_times, 1)
    if repeat_name == "none":
        return [base.strftime("%Y-%m-%d %H:%M")]

    result: list[str] = []
    current = base
    for _ in range(times):
        result.append(current.strftime("%Y-%m-%d %H:%M"))
        if repeat_name == "daily":
            current += timedelta(days=1)
        elif repeat_name == "weekly":
            current += timedelta(weeks=1)
        elif repeat_name == "monthly":
            current = _add_month(current, months=1)
        else:
            current += timedelta(days=1)
    return result


def _add_month(value: datetime, *, months: int) -> datetime:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    max_day = monthrange(year, month)[1]
    day = min(value.day, max_day)
    return value.replace(year=year, month=month, day=day)


def _now_time_text() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _is_valid_datetime_text(value: str) -> bool:
    return _normalize_datetime_text(value) is not None


def _remove_option_span(text: str, span: tuple[int, int]) -> str:
    start, end = span
    return (text[:start] + " " + text[end:]).strip()


def _format_todo_meta_inline(due_at: str | None, remind_at: str | None, *, priority: int | None = None) -> str:
    meta_parts: list[str] = []
    if priority is not None:
        meta_parts.append(f"优先级:{priority}")
    if due_at:
        meta_parts.append(f"截止:{due_at}")
    if remind_at:
        meta_parts.append(f"提醒:{remind_at}")
    if not meta_parts:
        return ""
    return " | " + " ".join(meta_parts)


def _format_schedule_conflicts(conflicts: list[Any]) -> str:
    lines = ["日程冲突：以下时间点已有日程，请调整时间后重试。"]
    for item in conflicts[:5]:
        lines.append(f"- #{item.id} {item.event_time} {item.title}")
    if len(conflicts) > 5:
        lines.append(f"- ... 还有 {len(conflicts) - 5} 条冲突")
    return "\n".join(lines)


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(_table_cell_text(item) for item in headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_table_cell_text(item) for item in row) + " |" for row in rows]
    return "\n".join([header_line, separator, *body])


def _table_cell_text(value: str) -> str:
    # Keep table layout stable even if content contains separators or line breaks.
    return value.replace("|", "｜").replace("\n", " ").strip()


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)


def _extract_intent_label(text: str) -> str | None:
    cleaned = text.strip().lower()
    if not cleaned:
        return None

    parsed = _try_parse_json(cleaned)
    if isinstance(parsed, dict):
        candidate = str(parsed.get("intent", "")).strip().lower()
        if candidate in INTENT_LABELS:
            return candidate

    for label in INTENT_LABELS:
        if re.search(rf"\b{re.escape(label)}\b", cleaned):
            return label

    # If the model returns only one token-like word, accept it when possible.
    token = cleaned.split()[0]
    if token in INTENT_LABELS:
        return token

    # Semantic fallback from model free-form output.
    has_todo = "待办" in cleaned or "todo" in cleaned
    has_schedule = "日程" in cleaned or "schedule" in cleaned

    list_words = ("查看", "看一下", "列表", "记录", "还有", "当前", "列出", "如下", "清单", "汇总", "扫")
    add_words = ("添加", "新增", "增加", "记下", "记录了", "创建", "已添加", "已更新", "加", "加进去")
    done_words = ("完成", "done", "标记完成")

    if has_todo and any(word in cleaned for word in list_words):
        return "todo_list"
    if has_todo and any(word in cleaned for word in done_words):
        return "todo_done"
    if has_todo and any(word in cleaned for word in add_words):
        return "todo_add"

    if has_schedule and any(word in cleaned for word in list_words):
        return "schedule_list"
    if has_schedule and any(word in cleaned for word in add_words):
        return "schedule_add"

    return None


def _try_parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None

    cleaned = text.strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return None


def _extract_args_from_text(text: str, intent: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "todo_content": None,
        "todo_id": None,
        "event_time": None,
        "title": None,
    }
    cleaned = text.strip()

    if intent == "todo_add":
        extracted = _extract_todo_content(cleaned)
        if extracted:
            payload["todo_content"] = extracted
        return payload

    if intent == "todo_done":
        matched = re.search(r"(?:待办|todo)\s*#?\s*(\d+)", cleaned, flags=re.IGNORECASE)
        if matched:
            payload["todo_id"] = int(matched.group(1))
        return payload

    if intent == "schedule_add":
        time_match = re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", cleaned)
        if time_match:
            payload["event_time"] = time_match.group(0).replace("  ", " ")

        title_patterns = [
            r"(?:事项|标题)[:：]\s*[`\"“”']?([^`\"“”'\n。]+)",
            r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*[，, ]\s*([^。\n]+)",
        ]
        for pattern in title_patterns:
            matched = re.search(pattern, cleaned)
            if matched:
                payload["title"] = matched.group(1).strip().strip("。")
                break
        return payload

    return payload


def _extract_todo_content(text: str) -> str | None:
    candidates: list[str] = []

    quoted = re.findall(r"[\"“”'`](.+?)[\"“”'`]", text)
    candidates.extend(item.strip() for item in quoted if item.strip())

    patterns = [
        r"(?:添加|增加|新增|创建|记下|记录|加个|加一个)(?:一条|一个|条)?(?:测试)?待办[，,:：\s]*(.+)$",
        r"(?:帮我|请)?(?:把)?待办[，,:：\s]*(.+)$",
        r"todo[，,:：\s]*(.+)$",
        r"把(.+?)(?:加进去|加入|记下|添加)$",
    ]
    for pattern in patterns:
        matched = re.search(pattern, text, flags=re.IGNORECASE)
        if matched:
            value = matched.group(1).strip()
            if value:
                candidates.append(value)

    for raw in candidates:
        normalized = raw.strip("，,。；;：: ")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        # Ignore obvious non-task artifacts from hallucinated analysis text.
        if re.fullmatch(r"\.[a-z0-9]{1,8}", normalized, flags=re.IGNORECASE):
            continue
        if normalized:
            return normalized
    return None
