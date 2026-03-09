from __future__ import annotations

from typing import Any

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.parsing_utils import (
    _normalize_positive_int_value,
    _normalize_thought_status_value,
)
from assistant_app.agent_components.render_helpers import (
    _format_thought_detail_result,
    _format_thoughts_list_result,
    _is_planner_command_success,
    _truncate_text,
)
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import ThoughtsAddArgs, ThoughtsIdArgs, ThoughtsListArgs, ThoughtsUpdateArgs


def execute_thoughts_system_action(
    agent: Any,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    *,
    raw_input: str,
) -> PlannerObservation:
    action = _payload_action(payload)
    log_context = {
        "action": action,
        "raw_input_preview": _truncate_text(raw_input, 120),
    }
    agent._app_logger.info(
        "planner_tool_thoughts_start",
        extra={"event": "planner_tool_thoughts_start", "context": log_context},
    )
    try:
        runtime_payload_or_error = _coerce_thoughts_runtime_payload(payload)
        if isinstance(runtime_payload_or_error, str):
            return _done_observation(
                agent=agent,
                action=action,
                raw_input=raw_input,
                result=runtime_payload_or_error,
            )

        typed_observation = _execute_typed_thoughts_system_action(
            agent=agent,
            payload=runtime_payload_or_error,
            raw_input=raw_input,
            action=action,
        )
        if typed_observation is not None:
            return typed_observation
        return _done_observation(agent=agent, action=action, raw_input=raw_input, result="thoughts.action 非法。")
    except Exception as exc:  # noqa: BLE001
        agent._app_logger.warning(
            "planner_tool_thoughts_failed",
            extra={
                "event": "planner_tool_thoughts_failed",
                "context": {**log_context, "error": repr(exc)},
            },
        )
        return PlannerObservation(
            tool="thoughts",
            input_text=raw_input,
            ok=False,
            result=f"thoughts 工具执行失败: {exc}",
        )


def _coerce_thoughts_runtime_payload(
    payload: dict[str, Any] | RuntimePlannerActionPayload,
) -> RuntimePlannerActionPayload | str:
    if isinstance(payload, RuntimePlannerActionPayload):
        return payload

    action = str(payload.get("action") or "").strip().lower()
    if action not in {"add", "list", "get", "update", "delete"}:
        return "thoughts.action 非法。"

    if action == "add":
        content = str(payload.get("content") or "").strip()
        if not content:
            return "thoughts.add content 不能为空。"
        return RuntimePlannerActionPayload(
            tool_name="thoughts_add",
            arguments=ThoughtsAddArgs.model_validate({"content": content}),
        )

    if action == "list":
        arguments: dict[str, Any] = {}
        if "status" in payload and payload.get("status") is not None:
            status = _normalize_thought_status_value(payload.get("status"))
            if status is None:
                return "thoughts.list status 必须为 未完成|完成|删除。"
            arguments["status"] = status
        return RuntimePlannerActionPayload(
            tool_name="thoughts_list",
            arguments=ThoughtsListArgs.model_validate(arguments),
        )

    target_id = _normalize_positive_int_value(payload.get("id"))
    if target_id is None:
        return "thoughts.id 必须为正整数。"

    if action == "get":
        return RuntimePlannerActionPayload(
            tool_name="thoughts_get",
            arguments=ThoughtsIdArgs(id=target_id),
        )

    if action == "update":
        content = str(payload.get("content") or "").strip()
        if not content:
            return "thoughts.update content 不能为空。"
        arguments: dict[str, Any] = {
            "id": target_id,
            "content": content,
        }
        if "status" in payload:
            if payload.get("status") is None:
                arguments["status"] = None
            else:
                status = _normalize_thought_status_value(payload.get("status"))
                if status is None:
                    return "thoughts.update status 必须为 未完成|完成|删除。"
                arguments["status"] = status
        return RuntimePlannerActionPayload(
            tool_name="thoughts_update",
            arguments=ThoughtsUpdateArgs.model_validate(arguments),
        )

    return RuntimePlannerActionPayload(
        tool_name="thoughts_delete",
        arguments=ThoughtsIdArgs(id=target_id),
    )


def _execute_typed_thoughts_system_action(
    *,
    agent: Any,
    payload: RuntimePlannerActionPayload,
    raw_input: str,
    action: str,
) -> PlannerObservation | None:
    arguments = payload.arguments
    if action == "add" and isinstance(arguments, ThoughtsAddArgs):
        thought_id = agent.db.add_thought(content=arguments.content)
        return _done_observation(
            agent=agent,
            action=action,
            raw_input=raw_input,
            result=f"已记录想法 #{thought_id}: {arguments.content}",
        )

    if action == "list" and isinstance(arguments, ThoughtsListArgs):
        list_status = arguments.status
        items = agent.db.list_thoughts(status=list_status)
        if not items:
            if list_status:
                return _done_observation(
                    agent=agent,
                    action=action,
                    raw_input=raw_input,
                    result=f"暂无状态为“{list_status}”的想法。",
                )
            return _done_observation(agent=agent, action=action, raw_input=raw_input, result="暂无想法记录。")
        result = _format_thoughts_list_result(items=items, status=list_status)
        return _done_observation(agent=agent, action=action, raw_input=raw_input, result=result)

    if action == "get" and isinstance(arguments, ThoughtsIdArgs):
        item = agent.db.get_thought(arguments.id)
        if item is None:
            return _done_observation(
                agent=agent,
                action=action,
                raw_input=raw_input,
                result=f"未找到想法 #{arguments.id}",
            )
        return _done_observation(
            agent=agent,
            action=action,
            raw_input=raw_input,
            result=_format_thought_detail_result(item),
        )

    if action == "update" and isinstance(arguments, ThoughtsUpdateArgs):
        if "status" in arguments.model_fields_set and arguments.status is None:
            return _done_observation(
                agent=agent,
                action=action,
                raw_input=raw_input,
                result="thoughts.update status 必须为 未完成|完成|删除。",
            )
        if "status" in arguments.model_fields_set:
            updated = agent.db.update_thought(arguments.id, content=arguments.content, status=arguments.status)
        else:
            updated = agent.db.update_thought(arguments.id, content=arguments.content)
        if not updated:
            return _done_observation(
                agent=agent,
                action=action,
                raw_input=raw_input,
                result=f"未找到想法 #{arguments.id}",
            )
        item = agent.db.get_thought(arguments.id)
        if item is None:
            return _done_observation(
                agent=agent,
                action=action,
                raw_input=raw_input,
                result=f"未找到想法 #{arguments.id}",
            )
        return _done_observation(
            agent=agent,
            action=action,
            raw_input=raw_input,
            result=f"已更新想法 #{arguments.id}: {item.content} [状态:{item.status}]",
        )

    if action == "delete" and isinstance(arguments, ThoughtsIdArgs):
        deleted = agent.db.soft_delete_thought(arguments.id)
        if not deleted:
            return _done_observation(
                agent=agent,
                action=action,
                raw_input=raw_input,
                result=f"未找到想法 #{arguments.id}",
            )
        return _done_observation(
            agent=agent,
            action=action,
            raw_input=raw_input,
            result=f"想法 #{arguments.id} 已删除。",
        )

    return None


def _payload_action(payload: dict[str, Any] | RuntimePlannerActionPayload) -> str:
    if isinstance(payload, RuntimePlannerActionPayload):
        tool_name = payload.tool_name
        return {
            "thoughts_add": "add",
            "thoughts_list": "list",
            "thoughts_get": "get",
            "thoughts_update": "update",
            "thoughts_delete": "delete",
        }.get(tool_name, tool_name)
    return str(payload.get("action") or "").strip().lower()


def _done_observation(*, agent: Any, action: str, raw_input: str, result: str) -> PlannerObservation:
    observation = PlannerObservation(
        tool="thoughts",
        input_text=raw_input,
        ok=_is_planner_command_success(result, tool="thoughts"),
        result=result,
    )
    agent._app_logger.info(
        "planner_tool_thoughts_done",
        extra={
            "event": "planner_tool_thoughts_done",
            "context": {"action": action, "ok": observation.ok},
        },
    )
    return observation
