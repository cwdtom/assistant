from __future__ import annotations

from typing import Any

THOUGHT_EXECUTION_TOOL_NAMES = ("todo", "schedule", "internet_search", "history_search")
THOUGHT_RUNTIME_TOOL_NAMES = ("ask_user", "done")
THOUGHT_ALL_TOOL_NAMES = (*THOUGHT_EXECUTION_TOOL_NAMES, *THOUGHT_RUNTIME_TOOL_NAMES)
THOUGHT_TODO_TOOL_NAMES = (
    "todo_add",
    "todo_list",
    "todo_view",
    "todo_get",
    "todo_update",
    "todo_delete",
    "todo_done",
    "todo_search",
)
THOUGHT_TOOL_GROUP_MEMBERS: dict[str, tuple[str, ...]] = {
    "todo": THOUGHT_TODO_TOOL_NAMES,
}


def normalize_plan_items(payload: dict[str, Any]) -> list[str]:
    raw_plan = payload.get("plan")
    plan_items: list[str] = []
    if isinstance(raw_plan, list):
        for item in raw_plan:
            text = ""
            if isinstance(item, dict):
                text = str(item.get("task") or item.get("item") or "").strip()
            else:
                text = str(item).strip()
            if text:
                plan_items.append(text)
    return plan_items


def normalize_tool_names(raw_tools: Any, *, allowed_tools: tuple[str, ...] = THOUGHT_ALL_TOOL_NAMES) -> list[str] | None:
    if not isinstance(raw_tools, list):
        return None
    allowed = {name.lower() for name in allowed_tools}
    normalized: list[str] = []
    for item in raw_tools:
        name = str(item or "").strip().lower()
        if not name:
            return None
        if name not in allowed:
            return None
        if name not in normalized:
            normalized.append(name)
    return normalized


def expand_tool_groups(tool_names: list[str]) -> list[str]:
    expanded: list[str] = []
    for name in tool_names:
        members = THOUGHT_TOOL_GROUP_MEMBERS.get(name)
        if members is None:
            if name not in expanded:
                expanded.append(name)
            continue
        for member in members:
            if member not in expanded:
                expanded.append(member)
    return expanded
