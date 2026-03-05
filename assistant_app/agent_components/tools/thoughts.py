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


def execute_thoughts_system_action(agent: Any, payload: dict[str, Any], *, raw_input: str) -> PlannerObservation:
    action = str(payload.get("action") or "").strip().lower()
    log_context = {
        "action": action,
        "raw_input_preview": _truncate_text(raw_input, 120),
    }
    agent._app_logger.info(
        "planner_tool_thoughts_start",
        extra={"event": "planner_tool_thoughts_start", "context": log_context},
    )
    try:
        if action not in {"add", "list", "get", "update", "delete"}:
            return _done_observation(agent=agent, action=action, raw_input=raw_input, result="thoughts.action 非法。")

        if action == "add":
            content = str(payload.get("content") or "").strip()
            if not content:
                return _done_observation(
                    agent=agent,
                    action=action,
                    raw_input=raw_input,
                    result="thoughts.add content 不能为空。",
                )
            thought_id = agent.db.add_thought(content=content)
            return _done_observation(
                agent=agent,
                action=action,
                raw_input=raw_input,
                result=f"已记录想法 #{thought_id}: {content}",
            )

        if action == "list":
            list_status: str | None = None
            if "status" in payload and payload.get("status") is not None:
                list_status = _normalize_thought_status_value(payload.get("status"))
                if list_status is None:
                    return _done_observation(
                        agent=agent,
                        action=action,
                        raw_input=raw_input,
                        result="thoughts.list status 必须为 未完成|完成|删除。",
                    )
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

        target_id = _normalize_positive_int_value(payload.get("id"))
        if target_id is None:
            return _done_observation(
                agent=agent,
                action=action,
                raw_input=raw_input,
                result="thoughts.id 必须为正整数。",
            )

        if action == "get":
            item = agent.db.get_thought(target_id)
            if item is None:
                return _done_observation(agent=agent, action=action, raw_input=raw_input, result=f"未找到想法 #{target_id}")
            return _done_observation(
                agent=agent,
                action=action,
                raw_input=raw_input,
                result=_format_thought_detail_result(item),
            )

        if action == "update":
            content = str(payload.get("content") or "").strip()
            if not content:
                return _done_observation(
                    agent=agent,
                    action=action,
                    raw_input=raw_input,
                    result="thoughts.update content 不能为空。",
                )
            if "status" in payload:
                status = _normalize_thought_status_value(payload.get("status"))
                if status is None:
                    return _done_observation(
                        agent=agent,
                        action=action,
                        raw_input=raw_input,
                        result="thoughts.update status 必须为 未完成|完成|删除。",
                    )
                updated = agent.db.update_thought(target_id, content=content, status=status)
            else:
                updated = agent.db.update_thought(target_id, content=content)
            if not updated:
                return _done_observation(agent=agent, action=action, raw_input=raw_input, result=f"未找到想法 #{target_id}")
            item = agent.db.get_thought(target_id)
            if item is None:
                return _done_observation(agent=agent, action=action, raw_input=raw_input, result=f"未找到想法 #{target_id}")
            return _done_observation(
                agent=agent,
                action=action,
                raw_input=raw_input,
                result=f"已更新想法 #{target_id}: {item.content} [状态:{item.status}]",
            )

        deleted = agent.db.soft_delete_thought(target_id)
        if not deleted:
            return _done_observation(agent=agent, action=action, raw_input=raw_input, result=f"未找到想法 #{target_id}")
        return _done_observation(agent=agent, action=action, raw_input=raw_input, result=f"想法 #{target_id} 已删除。")
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
