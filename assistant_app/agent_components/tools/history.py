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
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import HistoryListArgs, HistorySearchArgs


def execute_history_system_action(
    agent: Any,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    *,
    raw_input: str,
    observation_tool: str = "history",
) -> Any:
    runtime_payload = _coerce_history_runtime_payload(
        payload=payload,
        raw_input=raw_input,
        observation_tool=observation_tool,
    )
    if isinstance(runtime_payload, PlannerObservation):
        return runtime_payload

    typed_observation = _execute_typed_history_system_action(
        agent,
        payload=runtime_payload,
        raw_input=raw_input,
        observation_tool=observation_tool,
    )
    if typed_observation is not None:
        return typed_observation
    return PlannerObservation(
        tool=observation_tool,
        input_text=raw_input,
        ok=False,
        result="history.action 非法。",
    )


def _coerce_history_runtime_payload(
    *,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    raw_input: str,
    observation_tool: str,
) -> RuntimePlannerActionPayload | PlannerObservation:
    if isinstance(payload, RuntimePlannerActionPayload):
        return payload

    action = str(payload.get("action") or "").strip().lower()
    if action not in {"list", "search"}:
        return PlannerObservation(
            tool=observation_tool,
            input_text=raw_input,
            ok=False,
            result="history.action 非法。",
        )

    arguments: dict[str, Any] = {}
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
        arguments["limit"] = limit

    if action == "list":
        return RuntimePlannerActionPayload(
            tool_name="history_list",
            arguments=HistoryListArgs.model_validate(arguments),
        )

    keyword = str(payload.get("keyword") or "").strip()
    if not keyword:
        return PlannerObservation(
            tool=observation_tool,
            input_text=raw_input,
            ok=False,
            result="history.search keyword 不能为空。",
        )
    arguments["keyword"] = keyword
    return RuntimePlannerActionPayload(
        tool_name="history_search",
        arguments=HistorySearchArgs.model_validate(arguments),
    )


def _execute_typed_history_system_action(
    agent: Any,
    *,
    payload: RuntimePlannerActionPayload,
    raw_input: str,
    observation_tool: str,
) -> PlannerObservation | None:
    tool_name = payload.tool_name
    arguments = payload.arguments
    if tool_name == "history_list" and isinstance(arguments, HistoryListArgs):
        history_limit = _normalized_history_limit(arguments.limit)
        turns = agent.db.recent_turns(limit=history_limit)
        if not turns:
            result = "暂无历史会话。"
            ok = _is_planner_command_success(result, tool=observation_tool)
            return PlannerObservation(tool=observation_tool, input_text=raw_input, ok=ok, result=result)
        result = _format_history_list_result(turns)
        ok = _is_planner_command_success(result, tool=observation_tool)
        return PlannerObservation(tool=observation_tool, input_text=raw_input, ok=ok, result=result)

    if tool_name == "history_search" and isinstance(arguments, HistorySearchArgs):
        history_limit = _normalized_history_limit(arguments.limit)
        turns = agent.db.search_turns(arguments.keyword, limit=history_limit)
        if not turns:
            result = f"未找到包含“{arguments.keyword}”的历史会话。"
            ok = _is_planner_command_success(result, tool=observation_tool)
            return PlannerObservation(tool=observation_tool, input_text=raw_input, ok=ok, result=result)
        result = _format_history_search_result(keyword=arguments.keyword, turns=turns)
        ok = _is_planner_command_success(result, tool=observation_tool)
        return PlannerObservation(tool=observation_tool, input_text=raw_input, ok=ok, result=result)

    return None


def _normalized_history_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_HISTORY_LIST_LIMIT
    return min(limit, MAX_HISTORY_LIST_LIMIT)
