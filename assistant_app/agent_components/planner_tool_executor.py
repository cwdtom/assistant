from __future__ import annotations

from collections.abc import Callable
from typing import Any

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.tools.planner_tool_routing import (
    build_json_planner_tool_executor,
)
from assistant_app.schemas.routing import JsonPlannerToolRoute, RuntimePlannerActionPayload

JsonRouteExecutor = Callable[[dict[str, Any] | RuntimePlannerActionPayload, str], PlannerObservation]
InternetSearchRouteExecutor = Callable[[str, RuntimePlannerActionPayload | None], PlannerObservation]


class PlannerToolExecutor:
    def __init__(
        self,
        *,
        command_executor: Callable[[str], str],
        schedule_executor: JsonRouteExecutor,
        history_executor: JsonRouteExecutor,
        history_search_executor: JsonRouteExecutor,
        thoughts_executor: JsonRouteExecutor,
        system_executor: JsonRouteExecutor,
        internet_search_executor: InternetSearchRouteExecutor,
    ) -> None:
        self._routes = self._build_routes(
            command_executor=command_executor,
            schedule_executor=schedule_executor,
            history_executor=history_executor,
            history_search_executor=history_search_executor,
            thoughts_executor=thoughts_executor,
            system_executor=system_executor,
            internet_search_executor=internet_search_executor,
        )

    def execute(
        self,
        *,
        action_tool: str,
        action_input: str,
        action_payload: RuntimePlannerActionPayload | None = None,
    ) -> PlannerObservation:
        handler = self._routes.get(action_tool)
        if handler is None:
            return PlannerObservation(
                tool=action_tool or "unknown",
                input_text=action_input,
                ok=False,
                result=f"未知工具: {action_tool}",
            )
        return handler(action_input, action_payload)

    def _build_routes(
        self,
        *,
        command_executor: Callable[[str], str],
        schedule_executor: JsonRouteExecutor,
        history_executor: JsonRouteExecutor,
        history_search_executor: JsonRouteExecutor,
        thoughts_executor: JsonRouteExecutor,
        system_executor: JsonRouteExecutor,
        internet_search_executor: InternetSearchRouteExecutor,
    ) -> dict[str, Callable[[str, RuntimePlannerActionPayload | None], PlannerObservation]]:
        json_routes: dict[str, JsonPlannerToolRoute] = {
            "schedule": JsonPlannerToolRoute(
                tool="schedule",
                invalid_json_result="schedule 工具参数无效：需要 JSON 对象。",
                legacy_command_prefix="/schedule",
                payload_executor=lambda payload, raw_input: schedule_executor(payload, raw_input),
                typed_payload_executor=lambda payload, raw_input: schedule_executor(payload, raw_input),
            ),
            "history": JsonPlannerToolRoute(
                tool="history",
                invalid_json_result="history 工具参数无效：需要 JSON 对象。",
                legacy_command_prefix="/history",
                payload_executor=lambda payload, raw_input: history_executor(payload, raw_input),
                typed_payload_executor=lambda payload, raw_input: history_executor(payload, raw_input),
            ),
            "history_search": JsonPlannerToolRoute(
                tool="history_search",
                invalid_json_result="history_search 工具参数无效：需要 JSON 对象。",
                legacy_command_prefix="/history search",
                compat_action="search",
                payload_executor=lambda payload, raw_input: history_search_executor(payload, raw_input),
                typed_payload_executor=lambda payload, raw_input: history_search_executor(payload, raw_input),
            ),
            "thoughts": JsonPlannerToolRoute(
                tool="thoughts",
                invalid_json_result="thoughts 工具参数无效：需要 JSON 对象。",
                legacy_command_prefix="/thoughts",
                payload_executor=lambda payload, raw_input: thoughts_executor(payload, raw_input),
                typed_payload_executor=lambda payload, raw_input: thoughts_executor(payload, raw_input),
            ),
            "system": JsonPlannerToolRoute(
                tool="system",
                invalid_json_result="system 工具参数无效：需要 JSON 对象。",
                legacy_command_prefix="/date",
                payload_executor=lambda payload, raw_input: system_executor(payload, raw_input),
                typed_payload_executor=lambda payload, raw_input: system_executor(payload, raw_input),
            ),
        }
        routes = {
            name: build_json_planner_tool_executor(route=route, command_executor=command_executor)
            for name, route in json_routes.items()
        }
        routes["internet_search"] = internet_search_executor
        return routes
