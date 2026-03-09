from __future__ import annotations

from collections.abc import Callable

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.render_helpers import _is_planner_command_success
from assistant_app.schemas.routing import JsonPlannerToolRoute, RuntimePlannerActionPayload
from assistant_app.schemas.tools import parse_json_object


def build_json_planner_tool_executor(
    *,
    route: JsonPlannerToolRoute,
    command_executor: Callable[[str], str],
) -> Callable[[str, RuntimePlannerActionPayload | None], PlannerObservation]:
    def execute(
        action_input: str,
        action_payload: RuntimePlannerActionPayload | None = None,
    ) -> PlannerObservation:
        normalized_input = action_input.strip()
        if route.legacy_command_prefix is not None:
            legacy_observation = _maybe_execute_legacy_tool_command(
                normalized_input=normalized_input,
                command_prefix=route.legacy_command_prefix,
                tool=route.tool,
                command_executor=command_executor,
            )
            if legacy_observation is not None:
                return legacy_observation
        if action_payload is not None and route.typed_payload_executor is not None:
            return route.typed_payload_executor(action_payload, normalized_input)
        payload = parse_json_object(normalized_input)
        if payload is None:
            return PlannerObservation(
                tool=route.tool,
                input_text=action_input,
                ok=False,
                result=route.invalid_json_result,
            )
        normalized_payload = dict(payload)
        if route.compat_action is not None:
            normalized_payload["action"] = route.compat_action
        return route.payload_executor(normalized_payload, normalized_input)

    return execute


def _maybe_execute_legacy_tool_command(
    *,
    normalized_input: str,
    command_prefix: str,
    tool: str,
    command_executor: Callable[[str], str],
) -> PlannerObservation | None:
    # Backward-compatible fallback for non-tool-calling thought outputs.
    if not normalized_input.startswith(command_prefix):
        return None
    command_result = command_executor(normalized_input)
    ok = _is_planner_command_success(command_result, tool=tool)
    return PlannerObservation(tool=tool, input_text=normalized_input, ok=ok, result=command_result)
