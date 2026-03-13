from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.render_helpers import (
    _is_planner_command_success,
    _render_table,
    _truncate_text,
)
from assistant_app.scheduled_task_cron import compute_next_run_at_from_cron
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.scheduled_tasks import ScheduledPlannerTask
from assistant_app.schemas.tools import (
    TimerAddArgs,
    TimerIdArgs,
    TimerListArgs,
    TimerUpdateArgs,
    coerce_timer_action_payload,
)
from assistant_app.schemas.validation_errors import first_validation_issue

_PROMPT_PREVIEW_LIMIT = 80


def execute_timer_system_action(
    agent: Any,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    *,
    raw_input: str,
    source: str = "planner",
    clock: Callable[[], datetime] | None = None,
) -> PlannerObservation:
    tool_name = _payload_tool_name(payload)
    log_context = {
        "tool_name": tool_name,
        "source": source,
        "raw_input_preview": _truncate_text(raw_input, 120),
    }
    agent._app_logger.info(
        "planner_tool_timer_start",
        extra={"event": "planner_tool_timer_start", "context": log_context},
    )
    try:
        runtime_payload = _coerce_timer_runtime_payload(payload)
        if runtime_payload is None:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result="timer.action 非法。",
            )
        typed_observation = _execute_typed_timer_system_action(
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
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            result="timer.action 非法。",
        )
    except ValidationError as exc:
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            result=_timer_validation_error_text(payload=payload, exc=exc),
        )
    except ValueError as exc:
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            result=str(exc).strip() or "timer.action 非法。",
        )
    except Exception as exc:  # noqa: BLE001
        agent._app_logger.warning(
            "planner_tool_timer_failed",
            extra={
                "event": "planner_tool_timer_failed",
                "context": {**log_context, "error": repr(exc)},
            },
        )
        return PlannerObservation(
            tool="timer",
            input_text=raw_input,
            ok=False,
            result=f"timer 工具执行失败: {exc}",
        )


def _coerce_timer_runtime_payload(
    payload: dict[str, Any] | RuntimePlannerActionPayload,
) -> RuntimePlannerActionPayload | None:
    if isinstance(payload, RuntimePlannerActionPayload):
        return payload
    return coerce_timer_action_payload(payload)


def _execute_typed_timer_system_action(
    *,
    agent: Any,
    payload: RuntimePlannerActionPayload,
    raw_input: str,
    source: str,
    clock: Callable[[], datetime] | None,
) -> PlannerObservation | None:
    tool_name = payload.tool_name
    arguments = payload.arguments
    now = (clock or datetime.now)().replace(microsecond=0)

    if tool_name == "timer_add" and isinstance(arguments, TimerAddArgs):
        next_run_at = _next_run_at_for_task(
            cron_expr=arguments.cron_expr,
            run_limit=arguments.run_limit,
            now=now,
        )
        try:
            task_id = agent.db.add_scheduled_planner_task(
                task_name=arguments.task_name,
                cron_expr=arguments.cron_expr,
                prompt=arguments.prompt,
                run_limit=arguments.run_limit,
                next_run_at=next_run_at,
            )
        except sqlite3.IntegrityError:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result=f"定时任务名称已存在: {arguments.task_name}",
            )
        item = agent.db.get_scheduled_planner_task(task_id)
        if item is None:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result=f"已创建定时任务 #{task_id}",
                task_id=task_id,
                task_name=arguments.task_name,
                run_limit=arguments.run_limit,
                next_run_at=next_run_at,
            )
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            result=_format_timer_mutation_result(prefix="已创建定时任务", item=item),
            task_id=item.id,
            task_name=item.task_name,
            run_limit=item.run_limit,
            next_run_at=item.next_run_at,
        )

    if tool_name == "timer_list" and isinstance(arguments, TimerListArgs):
        del arguments
        items = agent.db.list_scheduled_planner_tasks()
        if not items:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result="暂无定时任务。",
            )
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            result=_format_timer_list_result(items),
        )

    if tool_name == "timer_get" and isinstance(arguments, TimerIdArgs):
        item = agent.db.get_scheduled_planner_task(arguments.id)
        if item is None:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result=f"未找到定时任务 #{arguments.id}",
            )
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            result=_format_timer_detail_result(item),
            task_id=item.id,
            task_name=item.task_name,
            run_limit=item.run_limit,
            next_run_at=item.next_run_at,
        )

    if tool_name == "timer_update" and isinstance(arguments, TimerUpdateArgs):
        item = agent.db.get_scheduled_planner_task(arguments.id)
        if item is None:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result=f"未找到定时任务 #{arguments.id}",
            )
        task_name, cron_expr, prompt, run_limit = _resolve_timer_update_fields(arguments=arguments, item=item)
        next_run_at = _resolve_updated_next_run_at(
            item=item,
            arguments=arguments,
            cron_expr=cron_expr,
            run_limit=run_limit,
            now=now,
        )
        try:
            updated = agent.db.update_scheduled_planner_task(
                arguments.id,
                task_name=task_name,
                cron_expr=cron_expr,
                prompt=prompt,
                run_limit=run_limit,
                next_run_at=next_run_at,
            )
        except sqlite3.IntegrityError:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result=f"定时任务名称已存在: {task_name}",
            )
        if not updated:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result=f"未找到定时任务 #{arguments.id}",
            )
        updated_item = agent.db.get_scheduled_planner_task(arguments.id)
        if updated_item is None:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result=f"未找到定时任务 #{arguments.id}",
            )
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            result=_format_timer_mutation_result(prefix="已更新定时任务", item=updated_item),
            task_id=updated_item.id,
            task_name=updated_item.task_name,
            run_limit=updated_item.run_limit,
            next_run_at=updated_item.next_run_at,
        )

    if tool_name == "timer_delete" and isinstance(arguments, TimerIdArgs):
        item = agent.db.get_scheduled_planner_task(arguments.id)
        if item is None:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result=f"未找到定时任务 #{arguments.id}",
            )
        deleted = agent.db.delete_scheduled_planner_task(arguments.id)
        if not deleted:
            return _done_observation(
                agent=agent,
                tool_name=tool_name,
                source=source,
                raw_input=raw_input,
                result=f"未找到定时任务 #{arguments.id}",
            )
        return _done_observation(
            agent=agent,
            tool_name=tool_name,
            source=source,
            raw_input=raw_input,
            result=f"定时任务 #{arguments.id} 已删除。",
            task_id=item.id,
            task_name=item.task_name,
            run_limit=item.run_limit,
            next_run_at=item.next_run_at,
        )

    return None


def _next_run_at_for_task(*, cron_expr: str, run_limit: int, now: datetime) -> str | None:
    if run_limit == 0:
        return None
    return compute_next_run_at_from_cron(cron_expr=cron_expr, now=now)


def _resolve_timer_update_fields(
    *,
    arguments: TimerUpdateArgs,
    item: ScheduledPlannerTask,
) -> tuple[str, str, str, int]:
    task_name = item.task_name
    if "task_name" in arguments.model_fields_set and arguments.task_name is not None:
        task_name = arguments.task_name

    cron_expr = item.cron_expr
    if "cron_expr" in arguments.model_fields_set and arguments.cron_expr is not None:
        cron_expr = arguments.cron_expr

    prompt = item.prompt
    if "prompt" in arguments.model_fields_set and arguments.prompt is not None:
        prompt = arguments.prompt

    run_limit = item.run_limit
    if "run_limit" in arguments.model_fields_set:
        run_limit = arguments.run_limit if arguments.run_limit is not None else item.run_limit

    return task_name, cron_expr, prompt, run_limit


def _resolve_updated_next_run_at(
    *,
    item: ScheduledPlannerTask,
    arguments: TimerUpdateArgs,
    cron_expr: str,
    run_limit: int,
    now: datetime,
) -> str | None:
    if "run_limit" in arguments.model_fields_set and run_limit == 0:
        return None
    cron_changed = "cron_expr" in arguments.model_fields_set and cron_expr != item.cron_expr
    reenabled = "run_limit" in arguments.model_fields_set and item.run_limit == 0 and run_limit != 0
    if cron_changed or reenabled:
        return _next_run_at_for_task(cron_expr=cron_expr, run_limit=run_limit, now=now)
    return item.next_run_at


def _format_timer_mutation_result(*, prefix: str, item: ScheduledPlannerTask) -> str:
    next_run_text = item.next_run_at or "-"
    return (
        f"{prefix} #{item.id}: {item.task_name} "
        f"(cron={item.cron_expr}, run_limit={item.run_limit}, next_run_at={next_run_text})"
    )


def _format_timer_list_result(items: list[ScheduledPlannerTask]) -> str:
    headers = ["ID", "任务名", "次数", "Cron", "下次执行", "上次执行", "Prompt预览"]
    rows = [
        [
            str(item.id),
            item.task_name,
            str(item.run_limit),
            item.cron_expr,
            item.next_run_at or "-",
            item.last_run_at or "-",
            _truncate_text(item.prompt, _PROMPT_PREVIEW_LIMIT) or "-",
        ]
        for item in items
    ]
    return f"定时任务列表:\n{_render_table(headers=headers, rows=rows)}"


def _format_timer_detail_result(item: ScheduledPlannerTask) -> str:
    headers = ["ID", "任务名", "次数", "Cron", "下次执行", "上次执行", "创建时间", "更新时间", "Prompt"]
    rows = [
        [
            str(item.id),
            item.task_name,
            str(item.run_limit),
            item.cron_expr,
            item.next_run_at or "-",
            item.last_run_at or "-",
            item.created_at,
            item.updated_at,
            item.prompt,
        ]
    ]
    return f"定时任务详情:\n{_render_table(headers=headers, rows=rows)}"


def _done_observation(
    *,
    agent: Any,
    tool_name: str,
    source: str,
    raw_input: str,
    result: str,
    task_id: int | None = None,
    task_name: str | None = None,
    run_limit: int | None = None,
    next_run_at: str | None = None,
) -> PlannerObservation:
    observation = PlannerObservation(
        tool="timer",
        input_text=raw_input,
        ok=_is_planner_command_success(result, tool="timer"),
        result=result,
    )
    agent._app_logger.info(
        "planner_tool_timer_done",
        extra={
            "event": "planner_tool_timer_done",
            "context": {
                "tool_name": tool_name,
                "source": source,
                "ok": observation.ok,
                "task_id": task_id,
                "task_name": task_name,
                "run_limit": run_limit,
                "next_run_at": next_run_at,
            },
        },
    )
    return observation


def _payload_tool_name(payload: dict[str, Any] | RuntimePlannerActionPayload) -> str:
    if isinstance(payload, RuntimePlannerActionPayload):
        return payload.tool_name
    action = str(payload.get("action") or "").strip().lower()
    return {
        "add": "timer_add",
        "list": "timer_list",
        "get": "timer_get",
        "update": "timer_update",
        "delete": "timer_delete",
    }.get(action, "timer")


def _timer_validation_error_text(
    *,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    exc: ValidationError,
) -> str:
    action = _payload_action(payload)
    issue = first_validation_issue(exc)
    if action not in {"add", "list", "get", "update", "delete"}:
        return "timer.action 非法。"
    if issue.message == "timer.task_name cannot be null":
        return "timer.update task_name 不能为空。"
    if issue.message == "timer.prompt cannot be null":
        return "timer.update prompt 不能为空。"
    if issue.message == "timer.cron_expr cannot be null":
        return "timer.update cron_expr 必须为合法 cron 表达式。"
    if issue.message == "timer.run_limit cannot be null":
        return "timer.update run_limit 必须为 -1 或 >= 0。"
    if issue.field == "id":
        return "timer.id 必须为正整数。"
    if issue.field == "task_name":
        return f"timer.{action} task_name 不能为空。"
    if issue.field == "prompt":
        return f"timer.{action} prompt 不能为空。"
    if issue.field == "run_limit":
        return f"timer.{action} run_limit 必须为 -1 或 >= 0。"
    if issue.field == "cron_expr":
        return f"timer.{action} cron_expr 必须为合法 cron 表达式。"
    return issue.message or "timer 工具参数无效。"


def _payload_action(payload: dict[str, Any] | RuntimePlannerActionPayload) -> str:
    if isinstance(payload, RuntimePlannerActionPayload):
        return payload.tool_name.removeprefix("timer_")
    return str(payload.get("action") or "").strip().lower()


__all__ = ["execute_timer_system_action"]
