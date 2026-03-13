from __future__ import annotations

import json
import re
from typing import Any

from assistant_app.db import ChatTurn, ThoughtItem
from assistant_app.search import SearchResult


def _history_table_rows(turns: list[ChatTurn]) -> list[list[str]]:
    return [
        [
            str(index),
            _truncate_text(item.user_content, 300) or "-",
            _truncate_text(item.assistant_content, 300) or "-",
            item.created_at,
        ]
        for index, item in enumerate(turns, start=1)
    ]


def _format_history_list_result(turns: list[ChatTurn]) -> str:
    table = _render_table(headers=["#", "用户输入", "最终回答", "时间"], rows=_history_table_rows(turns))
    return f"历史会话(最近 {len(turns)} 轮):\n{table}"


def _format_history_search_result(*, keyword: str, turns: list[ChatTurn]) -> str:
    table = _render_table(headers=["#", "用户输入", "最终回答", "时间"], rows=_history_table_rows(turns))
    return f"历史搜索(关键词: {keyword}, 命中 {len(turns)} 轮):\n{table}"


def _thoughts_table_headers() -> list[str]:
    return ["ID", "内容", "状态", "创建时间", "更新时间"]


def _thoughts_table_rows(items: list[ThoughtItem]) -> list[list[str]]:
    return [
        [
            str(item.id),
            _truncate_text(item.content, 300) or "-",
            item.status,
            item.created_at,
            item.updated_at,
        ]
        for item in items
    ]


def _format_thoughts_list_result(*, items: list[ThoughtItem], status: str | None) -> str:
    title = f"想法列表(状态: {status})" if status else "想法列表(状态: pending|completed)"
    table = _render_table(headers=_thoughts_table_headers(), rows=_thoughts_table_rows(items))
    return f"{title}:\n{table}"


def _format_thought_detail_result(item: ThoughtItem) -> str:
    table = _render_table(headers=_thoughts_table_headers(), rows=_thoughts_table_rows([item]))
    return f"想法详情:\n{table}"


def _schedule_list_empty_text(*, window_days: int, tag: str | None) -> str:
    if tag:
        return f"前天起未来 {window_days} 天内（标签:{tag}）暂无日程。"
    return f"前天起未来 {window_days} 天内暂无日程。"


def _schedule_list_title(*, window_days: int, tag: str | None) -> str:
    title_suffix = f"，标签:{tag}" if tag else ""
    return f"日程列表(前天起未来 {window_days} 天{title_suffix})"


def _schedule_view_title(*, view_name: str, anchor: str | None, tag: str | None) -> str:
    title = f"日历视图({view_name}, {anchor})" if anchor else f"日历视图({view_name})"
    if tag:
        title = f"{title} [标签:{tag}]"
    return title


def _format_schedule_remind_meta_inline(
    *,
    remind_at: str | None,
    repeat_remind_start_time: str | None,
) -> str:
    meta_parts: list[str] = []
    if remind_at:
        meta_parts.append(f"提醒:{remind_at}")
    if repeat_remind_start_time:
        meta_parts.append(f"重复提醒开始:{repeat_remind_start_time}")
    if not meta_parts:
        return ""
    return " | " + " ".join(meta_parts)


def _repeat_enabled_text(value: bool | None) -> str:
    if value is None:
        return "-"
    return "on" if value else "off"


def _schedule_table_headers() -> list[str]:
    return [
        "ID",
        "时间",
        "时长(分钟)",
        "标签",
        "标题",
        "提醒时间",
        "重复提醒开始",
        "重复间隔(分钟)",
        "重复次数",
        "重复启用",
        "创建时间",
    ]


def _schedule_table_rows(items: list[Any]) -> list[list[str]]:
    return [
        [
            str(item.id),
            item.event_time,
            str(item.duration_minutes),
            item.tag,
            item.title,
            item.remind_at or "-",
            item.repeat_remind_start_time or "-",
            str(item.repeat_interval_minutes) if item.repeat_interval_minutes is not None else "-",
            str(item.repeat_times) if item.repeat_times is not None else "-",
            _repeat_enabled_text(item.repeat_enabled),
            item.created_at,
        ]
        for item in items
    ]


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


def _truncate_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."


def _is_planner_command_success(result: str, *, tool: str) -> bool:
    text = result.strip()
    if not text:
        return False

    if text.startswith("用法:") or text.startswith("未知命令"):
        return False

    if tool == "schedule":
        if text.startswith("未找到日程 #") or "没有可切换的重复规则" in text:
            return False
    if tool in {"history", "history_search"}:
        if text.startswith("未找到包含") or text.startswith("暂无历史会话"):
            return False
    if tool == "thoughts":
        if text.startswith("thoughts.") or text.startswith("未找到想法 #") or text.startswith("暂无想法"):
            return False
    if tool == "timer":
        if (
            text.startswith("timer.")
            or text.startswith("未找到定时任务 #")
            or text.startswith("暂无定时任务")
            or text.startswith("定时任务名称已存在:")
        ):
            return False
    if tool == "system":
        if text.startswith("system."):
            return False

    return True


def _format_search_results(results: list[SearchResult], *, top_k: int) -> str:
    target_top_k = max(top_k, 1)
    lines = [f"互联网搜索结果（返回 {len(results)} 条，目标 Top {target_top_k}）:"]
    for index, item in enumerate(results, start=1):
        snippet = item.snippet or "-"
        lines.append(f"{index}. {item.title}")
        lines.append(f"   摘要: {snippet}")
        lines.append(f"   链接: {item.url}")
    return "\n".join(lines)
