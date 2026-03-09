from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.parsing_utils import (
    DEFAULT_HISTORY_LIST_LIMIT,
    MAX_HISTORY_LIST_LIMIT,
)
from assistant_app.agent_components.render_helpers import (
    _format_history_list_result,
    _format_history_search_result,
    _is_planner_command_success,
)
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import (
    HistoryListArgs,
    HistorySearchArgs,
    coerce_history_action_payload,
)


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

    try:
        return coerce_history_action_payload(payload)
    except ValidationError as exc:
        return PlannerObservation(
            tool=observation_tool,
            input_text=raw_input,
            ok=False,
            result=_history_validation_error_text(payload=payload, exc=exc),
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


def _history_validation_error_text(*, payload: dict[str, Any], exc: ValidationError) -> str:
    action = str(payload.get("action") or "").strip().lower()
    errors = exc.errors(include_url=False)
    first_error = (
        errors[0]
        if errors
        else {"type": "value_error", "loc": (), "msg": "validation error", "input": None}
    )
    location = first_error.get("loc", ())
    field_names = {str(item) for item in location}
    if action not in {"list", "search"}:
        return "history.action 非法。"
    if "limit" in field_names:
        return f"history.{action} limit 必须为正整数。"
    if action == "search" and "keyword" in field_names:
        return "history.search keyword 不能为空。"
    return "history.action 非法。"
