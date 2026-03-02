from __future__ import annotations

from typing import Any

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.parsing_utils import (
    DEFAULT_HISTORY_LIST_LIMIT,
    MAX_HISTORY_LIST_LIMIT,
    _normalize_positive_int_value,
)
from assistant_app.agent_components.render_helpers import (
    _format_history_list_result,
    _format_history_search_result,
    _is_planner_command_success,
)


def execute_history_system_action(
    agent: Any,
    payload: dict[str, Any],
    *,
    raw_input: str,
    observation_tool: str = "history",
) -> Any:
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"list", "search"}:
        return PlannerObservation(
            tool=observation_tool,
            input_text=raw_input,
            ok=False,
            result="history.action 非法。",
        )

    history_limit = DEFAULT_HISTORY_LIST_LIMIT
    if "limit" in payload and payload.get("limit") is not None:
        limit = _normalize_positive_int_value(payload.get("limit"))
        if limit is None:
            if action == "list":
                limit_error = "history.list limit 必须为正整数。"
            else:
                limit_error = "history.search limit 必须为正整数。"
            return PlannerObservation(
                tool=observation_tool,
                input_text=raw_input,
                ok=False,
                result=limit_error,
            )
        history_limit = min(limit, MAX_HISTORY_LIST_LIMIT)

    if action == "list":
        turns = agent.db.recent_turns(limit=history_limit)
        if not turns:
            result = "暂无历史会话。"
            ok = _is_planner_command_success(result, tool=observation_tool)
            return PlannerObservation(tool=observation_tool, input_text=raw_input, ok=ok, result=result)
        result = _format_history_list_result(turns)
        ok = _is_planner_command_success(result, tool=observation_tool)
        return PlannerObservation(tool=observation_tool, input_text=raw_input, ok=ok, result=result)

    keyword = str(payload.get("keyword") or "").strip()
    if not keyword:
        return PlannerObservation(
            tool=observation_tool,
            input_text=raw_input,
            ok=False,
            result="history.search keyword 不能为空。",
        )
    turns = agent.db.search_turns(keyword, limit=history_limit)
    if not turns:
        result = f"未找到包含“{keyword}”的历史会话。"
        ok = _is_planner_command_success(result, tool=observation_tool)
        return PlannerObservation(tool=observation_tool, input_text=raw_input, ok=ok, result=result)
    result = _format_history_search_result(keyword=keyword, turns=turns)
    ok = _is_planner_command_success(result, tool=observation_tool)
    return PlannerObservation(tool=observation_tool, input_text=raw_input, ok=ok, result=result)
