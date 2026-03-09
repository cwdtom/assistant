from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from assistant_app.db import AssistantDB, ScheduleItem
from assistant_app.schemas.planner import ProactiveDoneArguments
from assistant_app.schemas.proactive import (
    ProactiveChatTurnContextItem,
    ProactiveHistoryListToolResult,
    ProactiveHistorySearchToolResult,
    ProactiveInternetSearchToolResult,
    ProactiveScheduleContextItem,
    ProactiveScheduleGetToolResult,
    ProactiveScheduleListToolResult,
    ProactiveScheduleViewToolResult,
)
from assistant_app.schemas.tools import (
    PROACTIVE_TOOL_ARGS_MODELS,
    ProactiveHistoryListArgs,
    ProactiveHistorySearchArgs,
    ProactiveInternetSearchArgs,
    ProactiveScheduleGetArgs,
    ProactiveScheduleListArgs,
    ProactiveScheduleViewArgs,
    ProactiveToolArgsBase,
    build_function_tool_schema,
    validate_proactive_tool_arguments,
)
from assistant_app.search import SearchProvider

_ProactiveSchemaModel = type[ProactiveToolArgsBase] | type[ProactiveDoneArguments]

_PROACTIVE_TOOL_SCHEMA_SPECS: tuple[tuple[str, str, _ProactiveSchemaModel], ...] = (
    ("schedule_list", "List schedules in upcoming window.", PROACTIVE_TOOL_ARGS_MODELS["schedule_list"]),
    ("schedule_view", "View schedules by day/week/month.", PROACTIVE_TOOL_ARGS_MODELS["schedule_view"]),
    ("schedule_get", "Get a schedule by id.", PROACTIVE_TOOL_ARGS_MODELS["schedule_get"]),
    ("history_list", "List recent chat turns.", PROACTIVE_TOOL_ARGS_MODELS["history_list"]),
    ("history_search", "Search chat turns by keyword.", PROACTIVE_TOOL_ARGS_MODELS["history_search"]),
    ("internet_search", "Search public web for supplemental evidence.", PROACTIVE_TOOL_ARGS_MODELS["internet_search"]),
    ("done", "Finish proactive decision with structured output.", ProactiveDoneArguments),
)


def build_proactive_tool_schemas() -> list[dict[str, Any]]:
    return [
        build_function_tool_schema(
            name=name,
            description=description,
            arguments_model=arguments_model,
        )
        for name, description, arguments_model in _PROACTIVE_TOOL_SCHEMA_SPECS
    ]


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

    def execute(self, *, tool_name: str, arguments: dict[str, Any] | ProactiveToolArgsBase) -> str:
        validated_arguments: ProactiveToolArgsBase | None
        if isinstance(arguments, ProactiveToolArgsBase):
            expected_cls = PROACTIVE_TOOL_ARGS_MODELS.get(tool_name)
            validated_arguments = arguments if expected_cls and isinstance(arguments, expected_cls) else None
        else:
            validated_arguments = validate_proactive_tool_arguments(tool_name, arguments)
        if validated_arguments is None:
            raise ValueError(f"invalid arguments for tool: {tool_name}")
        if tool_name == "schedule_list":
            return self._schedule_list(validated_arguments)
        if tool_name == "schedule_view":
            return self._schedule_view(validated_arguments)
        if tool_name == "schedule_get":
            return self._schedule_get(validated_arguments)
        if tool_name == "history_list":
            return self._history_list(validated_arguments)
        if tool_name == "history_search":
            return self._history_search(validated_arguments)
        if tool_name == "internet_search":
            return self._internet_search(validated_arguments)
        raise ValueError(f"unsupported tool: {tool_name}")

    def _schedule_list(self, arguments: ProactiveScheduleListArgs) -> str:
        tag = arguments.tag or None
        end = self._now + timedelta(hours=self._lookahead_hours)
        max_window_days = max((self._lookahead_hours + 23) // 24, 1)
        items = self._db.list_schedules(
            window_start=self._now,
            window_end=end,
            max_window_days=max_window_days,
            tag=tag,
        )
        payload = ProactiveScheduleListToolResult(
            count=min(len(items), 50),
            items=[ProactiveScheduleContextItem.from_schedule_item(item) for item in items[:50]],
        )
        return payload.model_dump_json()

    def _schedule_view(self, arguments: ProactiveScheduleViewArgs) -> str:
        view = arguments.view
        tag = arguments.tag or None
        anchor = arguments.anchor or None
        end = self._now + timedelta(hours=self._lookahead_hours)
        max_window_days = max((self._lookahead_hours + 23) // 24, 1)
        items = self._db.list_schedules(
            window_start=self._now,
            window_end=end,
            max_window_days=max_window_days,
            tag=tag,
        )
        filtered = _filter_schedules_by_view(items, view=view, anchor=anchor)
        payload = ProactiveScheduleViewToolResult(
            view=view,
            anchor=anchor or "",
            count=min(len(filtered), 50),
            items=[ProactiveScheduleContextItem.from_schedule_item(item) for item in filtered[:50]],
        )
        return payload.model_dump_json()

    def _schedule_get(self, arguments: ProactiveScheduleGetArgs) -> str:
        schedule_id = arguments.id
        item = self._db.get_schedule(schedule_id)
        if item is None:
            return ProactiveScheduleGetToolResult(found=False, id=schedule_id).model_dump_json()
        return ProactiveScheduleGetToolResult(
            found=True,
            item=ProactiveScheduleContextItem.from_schedule_item(item),
        ).model_dump_json()

    def _history_list(self, arguments: ProactiveHistoryListArgs) -> str:
        limit = arguments.limit or 20
        since = self._now - timedelta(hours=self._chat_lookback_hours)
        turns = self._db.recent_turns_since(since=since, limit=limit)
        payload = ProactiveHistoryListToolResult(
            limit=limit,
            count=len(turns),
            items=[ProactiveChatTurnContextItem.from_chat_turn(turn) for turn in turns],
        )
        return payload.model_dump_json()

    def _history_search(self, arguments: ProactiveHistorySearchArgs) -> str:
        keyword = arguments.keyword
        limit = arguments.limit or 20
        since = self._now - timedelta(hours=self._chat_lookback_hours)
        recent_turns = self._db.recent_turns_since(since=since, limit=2000)
        lowered_keyword = keyword.lower()
        matched = [
            turn
            for turn in recent_turns
            if lowered_keyword in turn.user_content.lower() or lowered_keyword in turn.assistant_content.lower()
        ]
        selected = matched[-limit:]
        payload = ProactiveHistorySearchToolResult(
            keyword=keyword,
            count=len(selected),
            items=[ProactiveChatTurnContextItem.from_chat_turn(turn) for turn in selected],
        )
        return payload.model_dump_json()

    def _internet_search(self, arguments: ProactiveInternetSearchArgs) -> str:
        query = arguments.query
        results = self._search_provider.search(query, top_k=self._internet_search_top_k)
        payload = ProactiveInternetSearchToolResult(
            query=query,
            count=min(len(results), self._internet_search_top_k),
            items=results[: self._internet_search_top_k],
        )
        return payload.model_dump_json()


def _filter_schedules_by_view(items: list[ScheduleItem], *, view: str, anchor: str | None) -> list[ScheduleItem]:
    if view == "day":
        day = _parse_date(anchor) or datetime.now().date()
        return [
            item
            for item in items
            if _parse_event_time(item.event_time) and _parse_event_time(item.event_time).date() == day
        ]
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
