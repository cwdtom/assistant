from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.render_helpers import _is_planner_command_success, _truncate_text
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import SystemDateArgs, coerce_system_action_payload


def execute_system_system_action(
    agent: Any,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    *,
    raw_input: str,
    source: str = "planner",
    clock: Callable[[], datetime] | None = None,
) -> PlannerObservation:
    log_context = {
        "tool_name": _payload_tool_name(payload),
        "source": source,
        "raw_input_preview": _truncate_text(raw_input, 120),
    }
    agent._app_logger.info(
        "planner_tool_system_date_start",
        extra={"event": "planner_tool_system_date_start", "context": log_context},
    )
    try:
        runtime_payload = _coerce_system_runtime_payload(payload)
        if runtime_payload is None:
            return _done_observation(
                agent=agent,
                tool_name=log_context["tool_name"],
                source=source,
                raw_input=raw_input,
                result="system.action 非法。",
            )
        typed_observation = _execute_typed_system_action(
            agent=agent,
            payload=runtime_payload,
            raw_input=raw_input,
            source=source,
            clock=clock,
        )
        if typed_observation is not None:
            return typed_observation
        return _done_observation(
            agent=agent,
            tool_name=log_context["tool_name"],
            source=source,
            raw_input=raw_input,
            result="system.action 非法。",
        )
    except ValidationError as exc:
        return _done_observation(
            agent=agent,
            tool_name=log_context["tool_name"],
            source=source,
            raw_input=raw_input,
            result=str(exc).strip() or "system.action 非法。",
        )
    except ValueError as exc:
        return _done_observation(
            agent=agent,
            tool_name=log_context["tool_name"],
            source=source,
            raw_input=raw_input,
            result=str(exc).strip() or "system.action 非法。",
        )
    except Exception as exc:  # noqa: BLE001
        agent._app_logger.warning(
            "planner_tool_system_date_failed",
            extra={
                "event": "planner_tool_system_date_failed",
                "context": {**log_context, "error": repr(exc)},
            },
        )
        return PlannerObservation(
            tool="system",
            input_text=raw_input,
            ok=False,
            result=f"system 工具执行失败: {exc}",
        )


def _coerce_system_runtime_payload(
    payload: dict[str, Any] | RuntimePlannerActionPayload,
) -> RuntimePlannerActionPayload | None:
    if isinstance(payload, RuntimePlannerActionPayload):
        return payload
    return coerce_system_action_payload(payload)


def _execute_typed_system_action(
    *,
    agent: Any,
    payload: RuntimePlannerActionPayload,
    raw_input: str,
    source: str,
    clock: Callable[[], datetime] | None,
) -> PlannerObservation | None:
    if payload.tool_name == "system_date" and isinstance(payload.arguments, SystemDateArgs):
        now_value = (clock or datetime.now)().strftime("%Y-%m-%d %H:%M:%S")
        return _done_observation(
            agent=agent,
            tool_name=payload.tool_name,
            source=source,
            raw_input=raw_input,
            result=now_value,
        )
    return None


def _done_observation(
    *,
    agent: Any,
    tool_name: str,
    source: str,
    raw_input: str,
    result: str,
) -> PlannerObservation:
    observation = PlannerObservation(
        tool="system",
        input_text=raw_input,
        ok=_is_planner_command_success(result, tool="system"),
        result=result,
    )
    agent._app_logger.info(
        "planner_tool_system_date_done",
        extra={
            "event": "planner_tool_system_date_done",
            "context": {
                "tool_name": tool_name,
                "source": source,
                "ok": observation.ok,
                "result": result,
            },
        },
    )
    return observation


def _payload_tool_name(payload: dict[str, Any] | RuntimePlannerActionPayload) -> str:
    if isinstance(payload, RuntimePlannerActionPayload):
        return payload.tool_name
    if str(payload.get("action") or "").strip().lower() == "date":
        return "system_date"
    return "system_date"
