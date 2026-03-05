from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from assistant_app.db import AssistantDB, ChatTurn, ScheduleItem
from assistant_app.search import SearchProvider


def build_proactive_tool_schemas() -> list[dict[str, Any]]:
    return [
        _function_tool(
            name="schedule_list",
            description="List schedules in upcoming window.",
            properties={"tag": {"type": "string"}},
        ),
        _function_tool(
            name="schedule_view",
            description="View schedules by day/week/month.",
            properties={
                "view": {"type": "string", "enum": ["day", "week", "month"]},
                "anchor": {"type": "string", "description": "YYYY-MM-DD or YYYY-MM"},
                "tag": {"type": "string"},
            },
            required=["view"],
        ),
        _function_tool(
            name="schedule_get",
            description="Get a schedule by id.",
            properties={"id": {"type": "integer", "minimum": 1}},
            required=["id"],
        ),
        _function_tool(
            name="history_list",
            description="List recent chat turns.",
            properties={"limit": {"type": "integer", "minimum": 1, "maximum": 200}},
        ),
        _function_tool(
            name="history_search",
            description="Search chat turns by keyword.",
            properties={
                "keyword": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            required=["keyword"],
        ),
        _function_tool(
            name="internet_search",
            description="Search public web for supplemental evidence.",
            properties={"query": {"type": "string"}},
            required=["query"],
        ),
        _function_tool(
            name="done",
            description="Finish proactive decision with structured output.",
            properties={
                "notify": {"type": "boolean"},
                "message": {"type": "string"},
                "reason": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            required=["notify", "message", "reason"],
        ),
    ]


def _function_tool(
    *,
    name: str,
    description: str,
    properties: dict[str, dict[str, Any]],
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
                "additionalProperties": False,
            },
        },
    }


class ProactiveToolExecutor:
    def __init__(
        self,
        *,
        db: AssistantDB,
        search_provider: SearchProvider,
        now: datetime,
        lookahead_hours: int,
        chat_lookback_hours: int,
        internet_search_top_k: int,
    ) -> None:
        self._db = db
        self._search_provider = search_provider
        self._now = now
        self._lookahead_hours = max(lookahead_hours, 1)
        self._chat_lookback_hours = max(chat_lookback_hours, 1)
        self._internet_search_top_k = max(internet_search_top_k, 1)

    def execute(self, *, tool_name: str, arguments: dict[str, Any]) -> str:
        if tool_name == "schedule_list":
            return self._schedule_list(arguments)
        if tool_name == "schedule_view":
            return self._schedule_view(arguments)
        if tool_name == "schedule_get":
            return self._schedule_get(arguments)
        if tool_name == "history_list":
            return self._history_list(arguments)
        if tool_name == "history_search":
            return self._history_search(arguments)
        if tool_name == "internet_search":
            return self._internet_search(arguments)
        raise ValueError(f"unsupported tool: {tool_name}")

    def _schedule_list(self, arguments: dict[str, Any]) -> str:
        tag = _as_nonempty_text(arguments.get("tag"))
        end = self._now + timedelta(hours=self._lookahead_hours)
        max_window_days = max((self._lookahead_hours + 23) // 24, 1)
        items = self._db.list_schedules(window_start=self._now, window_end=end, max_window_days=max_window_days, tag=tag)
        payload = [_schedule_to_payload(item) for item in items[:50]]
        return _json_dumps({"count": len(payload), "items": payload})

    def _schedule_view(self, arguments: dict[str, Any]) -> str:
        view = (_as_nonempty_text(arguments.get("view")) or "").lower()
        if view not in {"day", "week", "month"}:
            raise ValueError("schedule_view.view must be day|week|month")
        tag = _as_nonempty_text(arguments.get("tag"))
        anchor = _as_nonempty_text(arguments.get("anchor"))
        end = self._now + timedelta(hours=self._lookahead_hours)
        max_window_days = max((self._lookahead_hours + 23) // 24, 1)
        items = self._db.list_schedules(window_start=self._now, window_end=end, max_window_days=max_window_days, tag=tag)
        filtered = _filter_schedules_by_view(items, view=view, anchor=anchor)
        payload = [_schedule_to_payload(item) for item in filtered[:50]]
        return _json_dumps({"view": view, "anchor": anchor or "", "count": len(payload), "items": payload})

    def _schedule_get(self, arguments: dict[str, Any]) -> str:
        schedule_id = _as_positive_int(arguments.get("id"))
        if schedule_id is None:
            raise ValueError("schedule_get.id must be positive int")
        item = self._db.get_schedule(schedule_id)
        if item is None:
            return _json_dumps({"found": False, "id": schedule_id})
        return _json_dumps({"found": True, "item": _schedule_to_payload(item)})

    def _history_list(self, arguments: dict[str, Any]) -> str:
        limit = _as_positive_int(arguments.get("limit")) or 20
        limit = min(limit, 200)
        since = self._now - timedelta(hours=self._chat_lookback_hours)
        turns = self._db.recent_turns_since(since=since, limit=limit)
        payload = [_turn_to_payload(turn) for turn in turns]
        return _json_dumps({"limit": limit, "count": len(payload), "items": payload})

    def _history_search(self, arguments: dict[str, Any]) -> str:
        keyword = _as_nonempty_text(arguments.get("keyword"))
        if not keyword:
            raise ValueError("history_search.keyword is required")
        limit = _as_positive_int(arguments.get("limit")) or 20
        limit = min(limit, 200)
        since = self._now - timedelta(hours=self._chat_lookback_hours)
        recent_turns = self._db.recent_turns_since(since=since, limit=2000)
        lowered_keyword = keyword.lower()
        matched = [
            turn
            for turn in recent_turns
            if lowered_keyword in turn.user_content.lower() or lowered_keyword in turn.assistant_content.lower()
        ]
        payload = [_turn_to_payload(turn) for turn in matched[-limit:]]
        return _json_dumps({"keyword": keyword, "count": len(payload), "items": payload})

    def _internet_search(self, arguments: dict[str, Any]) -> str:
        query = _as_nonempty_text(arguments.get("query"))
        if not query:
            raise ValueError("internet_search.query is required")
        results = self._search_provider.search(query, top_k=self._internet_search_top_k)
        payload = [
            {"title": item.title, "snippet": item.snippet, "url": item.url}
            for item in results[: self._internet_search_top_k]
        ]
        return _json_dumps({"query": query, "count": len(payload), "items": payload})


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _as_nonempty_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _as_positive_int(value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    if value < 1:
        return None
    return value


def _schedule_to_payload(item: ScheduleItem) -> dict[str, object]:
    return {
        "id": item.id,
        "title": item.title,
        "tag": item.tag,
        "event_time": item.event_time,
        "duration_minutes": item.duration_minutes,
        "remind_at": item.remind_at,
        "repeat_interval_minutes": item.repeat_interval_minutes,
        "repeat_times": item.repeat_times,
        "repeat_enabled": item.repeat_enabled,
    }


def _turn_to_payload(turn: ChatTurn) -> dict[str, str]:
    return {
        "created_at": turn.created_at,
        "user_content": turn.user_content,
        "assistant_content": turn.assistant_content,
    }


def _filter_schedules_by_view(items: list[ScheduleItem], *, view: str, anchor: str | None) -> list[ScheduleItem]:
    if view == "day":
        day = _parse_date(anchor) or datetime.now().date()
        return [item for item in items if _parse_event_time(item.event_time) and _parse_event_time(item.event_time).date() == day]
    if view == "week":
        day = _parse_date(anchor) or datetime.now().date()
        week_start = day - timedelta(days=day.weekday())
        week_end = week_start + timedelta(days=6)
        filtered: list[ScheduleItem] = []
        for item in items:
            event = _parse_event_time(item.event_time)
            if event is None:
                continue
            if week_start <= event.date() <= week_end:
                filtered.append(item)
        return filtered
    if view == "month":
        year, month = _parse_year_month(anchor)
        if year is None or month is None:
            now = datetime.now()
            year, month = now.year, now.month
        filtered = []
        for item in items:
            event = _parse_event_time(item.event_time)
            if event is None:
                continue
            if event.year == year and event.month == month:
                filtered.append(item)
        return filtered
    return items


def _parse_event_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _parse_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_year_month(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError:
        return None, None
    return parsed.year, parsed.month
