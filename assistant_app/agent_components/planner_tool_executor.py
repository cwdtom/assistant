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
        timer_executor: JsonRouteExecutor,
        history_executor: JsonRouteExecutor,
        history_search_executor: JsonRouteExecutor,
        thoughts_executor: JsonRouteExecutor,
        user_profile_executor: JsonRouteExecutor,
        system_executor: JsonRouteExecutor,
        internet_search_executor: InternetSearchRouteExecutor,
    ) -> None:
        self._routes = self._build_routes(
            command_executor=command_executor,
            schedule_executor=schedule_executor,
            timer_executor=timer_executor,
            history_executor=history_executor,
            history_search_executor=history_search_executor,
            thoughts_executor=thoughts_executor,
            user_profile_executor=user_profile_executor,
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
        timer_executor: JsonRouteExecutor,
        history_executor: JsonRouteExecutor,
        history_search_executor: JsonRouteExecutor,
        thoughts_executor: JsonRouteExecutor,
        user_profile_executor: JsonRouteExecutor,
        system_executor: JsonRouteExecutor,
        internet_search_executor: InternetSearchRouteExecutor,
    ) -> dict[str, Callable[[str, RuntimePlannerActionPayload | None], PlannerObservation]]:
        json_routes: dict[str, JsonPlannerToolRoute] = {
            "schedule": self._build_json_route(
                tool="schedule",
                invalid_json_result="schedule 工具参数无效：需要 JSON 对象。",
                executor=schedule_executor,
                legacy_command_prefix="/schedule",
            ),
            "timer": self._build_json_route(
                tool="timer",
                invalid_json_result="timer 工具参数无效：需要 JSON 对象。",
                executor=timer_executor,
            ),
            "history": self._build_json_route(
                tool="history",
                invalid_json_result="history 工具参数无效：需要 JSON 对象。",
                executor=history_executor,
                legacy_command_prefix="/history",
            ),
            "history_search": self._build_json_route(
                tool="history_search",
                invalid_json_result="history_search 工具参数无效：需要 JSON 对象。",
                executor=history_search_executor,
                legacy_command_prefix="/history search",
                compat_action="search",
            ),
            "thoughts": self._build_json_route(
                tool="thoughts",
                invalid_json_result="thoughts 工具参数无效：需要 JSON 对象。",
                executor=thoughts_executor,
                legacy_command_prefix="/thoughts",
            ),
            "user_profile": self._build_json_route(
                tool="user_profile",
                invalid_json_result="user_profile 工具参数无效：需要 JSON 对象。",
                executor=user_profile_executor,
            ),
            "system": self._build_json_route(
                tool="system",
                invalid_json_result="system 工具参数无效：需要 JSON 对象。",
                executor=system_executor,
                legacy_command_prefix="/date",
            ),
        }
        routes = {
            name: build_json_planner_tool_executor(route=route, command_executor=command_executor)
            for name, route in json_routes.items()
        }
        routes["internet_search"] = internet_search_executor
        return routes

    @staticmethod
    def _build_json_route(
        *,
        tool: str,
        invalid_json_result: str,
        executor: JsonRouteExecutor,
        legacy_command_prefix: str | None = None,
        compat_action: str | None = None,
    ) -> JsonPlannerToolRoute:
        return JsonPlannerToolRoute(
            tool=tool,
            invalid_json_result=invalid_json_result,
            payload_executor=lambda payload, raw_input: executor(payload, raw_input),
            typed_payload_executor=lambda payload, raw_input: executor(payload, raw_input),
            legacy_command_prefix=legacy_command_prefix,
            compat_action=compat_action,
        )
