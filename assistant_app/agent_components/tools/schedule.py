from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.parsing_utils import (
    _default_schedule_list_window,
    _filter_schedules_by_calendar_view,
    _resolve_schedule_view_window,
)
from assistant_app.agent_components.render_helpers import (
    _format_schedule_remind_meta_inline,
    _is_planner_command_success,
    _render_table,
    _schedule_list_empty_text,
    _schedule_list_title,
    _schedule_table_headers,
    _schedule_table_rows,
    _schedule_view_title,
)
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import (
    ScheduleAddArgs,
    ScheduleIdArgs,
    ScheduleListArgs,
    ScheduleRepeatArgs,
    ScheduleUpdateArgs,
    ScheduleViewArgs,
    coerce_schedule_action_payload,
)


def execute_schedule_system_action(
    agent: Any,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    *,
    raw_input: str,
) -> Any:
    runtime_payload = _coerce_schedule_runtime_payload(payload=payload, raw_input=raw_input)
    if isinstance(runtime_payload, PlannerObservation):
        return runtime_payload

    typed_observation = _execute_typed_schedule_system_action(agent, payload=runtime_payload, raw_input=raw_input)
    if typed_observation is not None:
        return typed_observation
    return _schedule_observation(raw_input=raw_input, ok=False, result="schedule.action 非法。")

def _coerce_schedule_runtime_payload(
    *,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    raw_input: str,
) -> RuntimePlannerActionPayload | PlannerObservation:
    if isinstance(payload, RuntimePlannerActionPayload):
        return payload

    try:
        return coerce_schedule_action_payload(payload)
    except ValidationError as exc:
        return _schedule_observation(
            raw_input=raw_input,
            ok=False,
            result=_schedule_validation_error_text(payload=payload, exc=exc),
        )


def _schedule_observation(*, raw_input: str, ok: bool, result: str) -> PlannerObservation:
    return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)


def _validate_schedule_recurrence(
    *,
    action: str,
    repeat_interval_minutes: int | None,
    repeat_times: int,
    has_repeat_remind_start_time: bool,
) -> str | None:
    if repeat_interval_minutes is None and repeat_times != 1:
        return f"schedule.{action} 提供 times 时必须同时提供 interval_minutes。"
    if repeat_interval_minutes is not None and repeat_times == 1:
        return f"schedule.{action} interval_minutes 存在时，times 不能为 1。"
    if has_repeat_remind_start_time and repeat_interval_minutes is None:
        return f"schedule.{action} 提供 remind_start_time 时必须提供 interval_minutes。"
    return None


def _observe_schedule_list(agent: Any, *, raw_input: str, tag: str | None) -> PlannerObservation:
    window_start, window_end = _default_schedule_list_window(window_days=agent._schedule_max_window_days)
    items = agent.db.list_schedules(
        window_start=window_start,
        window_end=window_end,
        max_window_days=agent._schedule_max_window_days,
        tag=tag,
    )
    if not items:
        return _schedule_observation(
            raw_input=raw_input,
            ok=False,
            result=_schedule_list_empty_text(window_days=agent._schedule_max_window_days, tag=tag),
        )
    table = _render_table(
        headers=_schedule_table_headers(),
        rows=_schedule_table_rows(items),
    )
    return _schedule_observation(
        raw_input=raw_input,
        ok=True,
        result=f"{_schedule_list_title(window_days=agent._schedule_max_window_days, tag=tag)}:\n{table}",
    )


def _observe_schedule_view(
    agent: Any,
    *,
    raw_input: str,
    view_name: str,
    anchor: str | None,
    tag: str | None,
) -> PlannerObservation:
    window_start, window_end = _resolve_schedule_view_window(view_name=view_name, anchor=anchor)
    items = agent.db.list_schedules(
        window_start=window_start,
        window_end=window_end,
        max_window_days=agent._schedule_max_window_days,
        tag=tag,
    )
    items = _filter_schedules_by_calendar_view(items, view_name=view_name, anchor=anchor)
    if not items:
        return _schedule_observation(
            raw_input=raw_input,
            ok=False,
            result=f"{view_name} 视图下{f'（标签:{tag}）' if tag else ''}暂无日程。",
        )
    table = _render_table(
        headers=_schedule_table_headers(),
        rows=_schedule_table_rows(items),
    )
    title = _schedule_view_title(view_name=view_name, anchor=anchor, tag=tag)
    return _schedule_observation(raw_input=raw_input, ok=True, result=f"{title}:\n{table}")


def _observe_schedule_add(
    agent: Any,
    *,
    raw_input: str,
    event_time: str,
    title: str,
    tag: str,
    duration_minutes: int,
    remind_at: str | None,
    repeat_interval_minutes: int | None,
    repeat_times: int,
    repeat_remind_start_time: str | None,
) -> PlannerObservation:
    schedule_id = agent.db.add_schedule(
        title=title,
        event_time=event_time,
        duration_minutes=duration_minutes,
        remind_at=remind_at,
        tag=tag,
    )
    if repeat_interval_minutes is not None and repeat_times != 1:
        agent.db.set_schedule_recurrence(
            schedule_id,
            start_time=event_time,
            repeat_interval_minutes=repeat_interval_minutes,
            repeat_times=repeat_times,
            remind_start_time=repeat_remind_start_time,
        )
    notify_added = getattr(agent, "notify_schedule_added", None)
    if callable(notify_added):
        notify_added(schedule_id)
    remind_meta = _format_schedule_remind_meta_inline(
        remind_at=remind_at,
        repeat_remind_start_time=repeat_remind_start_time,
    )
    if repeat_times == 1:
        result = f"已添加日程 #{schedule_id} [标签:{tag}]: {event_time} {title} ({duration_minutes} 分钟){remind_meta}"
    elif repeat_times == -1:
        result = (
            f"已添加无限重复日程 #{schedule_id} [标签:{tag}]: {event_time} {title} "
            f"(duration={duration_minutes}m, interval={repeat_interval_minutes}m{remind_meta})"
        )
    else:
        result = (
            f"已添加重复日程 {repeat_times} 条 [标签:{tag}]: {event_time} {title} "
            f"(duration={duration_minutes}m, interval={repeat_interval_minutes}m, "
            f"times={repeat_times}{remind_meta})"
        )
    return _schedule_observation(raw_input=raw_input, ok=True, result=result)


def _observe_schedule_get(agent: Any, *, raw_input: str, schedule_id: int) -> PlannerObservation:
    item = agent.db.get_schedule(schedule_id)
    if item is None:
        return _schedule_observation(raw_input=raw_input, ok=False, result=f"未找到日程 #{schedule_id}")
    table = _render_table(
        headers=_schedule_table_headers(),
        rows=_schedule_table_rows([item]),
    )
    return _schedule_observation(raw_input=raw_input, ok=True, result=f"日程详情:\n{table}")


def _observe_schedule_update(
    agent: Any,
    *,
    raw_input: str,
    schedule_id: int,
    event_time: str,
    title: str,
    tag: str | None,
    tag_present: bool,
    duration_minutes: int | None,
    remind_at: str | None,
    remind_present: bool,
    repeat_interval_minutes: int | None,
    repeat_times: int,
    repeat_remind_start_time: str | None,
    repeat_remind_start_present: bool,
) -> PlannerObservation:
    current_item = agent.db.get_schedule(schedule_id)
    if current_item is None:
        return _schedule_observation(raw_input=raw_input, ok=False, result=f"未找到日程 #{schedule_id}")

    applied_duration_minutes = duration_minutes if duration_minutes is not None else current_item.duration_minutes
    schedule_update_kwargs: dict[str, Any] = {
        "title": title,
        "event_time": event_time,
        "duration_minutes": applied_duration_minutes,
    }
    if tag_present:
        schedule_update_kwargs["tag"] = tag or "default"
    if remind_present:
        schedule_update_kwargs["remind_at"] = remind_at
    if repeat_remind_start_present:
        schedule_update_kwargs["repeat_remind_start_time"] = repeat_remind_start_time
    updated = agent.db.update_schedule(schedule_id, **schedule_update_kwargs)
    if not updated:
        return _schedule_observation(raw_input=raw_input, ok=False, result=f"未找到日程 #{schedule_id}")

    if repeat_times == 1:
        agent.db.clear_schedule_recurrence(schedule_id)
        notify_updated = getattr(agent, "notify_schedule_updated", None)
        if callable(notify_updated):
            notify_updated(schedule_id, old_schedule=current_item)
        item = agent.db.get_schedule(schedule_id)
        remind_meta = _format_schedule_remind_meta_inline(
            remind_at=item.remind_at if item else None,
            repeat_remind_start_time=item.repeat_remind_start_time if item else None,
        )
        result = f"已更新日程 #{schedule_id}: {event_time} {title} ({applied_duration_minutes} 分钟){remind_meta}"
        if item is not None:
            result = (
                f"已更新日程 #{schedule_id} [标签:{item.tag}]: {event_time} {title} "
                f"({applied_duration_minutes} 分钟){remind_meta}"
            )
        ok = _is_planner_command_success(result, tool="schedule")
        return _schedule_observation(raw_input=raw_input, ok=ok, result=result)

    if repeat_interval_minutes is not None:
        remind_start_for_rule = (
            repeat_remind_start_time if repeat_remind_start_present else current_item.repeat_remind_start_time
        )
        agent.db.set_schedule_recurrence(
            schedule_id,
            start_time=event_time,
            repeat_interval_minutes=repeat_interval_minutes,
            repeat_times=repeat_times,
            remind_start_time=remind_start_for_rule,
        )
    notify_updated = getattr(agent, "notify_schedule_updated", None)
    if callable(notify_updated):
        notify_updated(schedule_id, old_schedule=current_item)
    item = agent.db.get_schedule(schedule_id)
    remind_meta = _format_schedule_remind_meta_inline(
        remind_at=item.remind_at if item else None,
        repeat_remind_start_time=item.repeat_remind_start_time if item else None,
    )
    if repeat_times == -1:
        result = (
            f"已更新为无限重复日程 #{schedule_id} [标签:{item.tag if item else current_item.tag}]: "
            f"{event_time} {title} "
            f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m{remind_meta})"
        )
    else:
        result = (
            f"已更新日程 #{schedule_id} [标签:{item.tag if item else current_item.tag}]: "
            f"{event_time} {title} "
            f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m, "
            f"times={repeat_times}{remind_meta})"
        )
    ok = _is_planner_command_success(result, tool="schedule")
    return _schedule_observation(raw_input=raw_input, ok=ok, result=result)


def _observe_schedule_delete(agent: Any, *, raw_input: str, schedule_id: int) -> PlannerObservation:
    current_item = agent.db.get_schedule(schedule_id)
    deleted = agent.db.delete_schedule(schedule_id)
    if not deleted:
        result = f"未找到日程 #{schedule_id}"
    else:
        notify_deleted = getattr(agent, "notify_schedule_deleted", None)
        if callable(notify_deleted):
            notify_deleted(schedule_id, deleted_schedule=current_item)
        result = f"日程 #{schedule_id} 已删除。"
    ok = _is_planner_command_success(result, tool="schedule")
    return _schedule_observation(raw_input=raw_input, ok=ok, result=result)


def _observe_schedule_repeat(
    agent: Any,
    *,
    raw_input: str,
    schedule_id: int,
    enabled: bool,
) -> PlannerObservation:
    changed = agent.db.set_schedule_recurrence_enabled(schedule_id, enabled)
    if not changed:
        result = f"日程 #{schedule_id} 没有可切换的重复规则。"
    else:
        status = "启用" if enabled else "停用"
        result = f"已{status}日程 #{schedule_id} 的重复规则。"
    ok = _is_planner_command_success(result, tool="schedule")
    return _schedule_observation(raw_input=raw_input, ok=ok, result=result)


def _execute_typed_schedule_system_action(
    agent: Any,
    *,
    payload: RuntimePlannerActionPayload,
    raw_input: str,
) -> PlannerObservation | None:
    tool_name = payload.tool_name
    arguments = payload.arguments

    if tool_name == "schedule_list" and isinstance(arguments, ScheduleListArgs):
        return _observe_schedule_list(
            agent,
            raw_input=raw_input,
            tag=arguments.tag,
        )

    if tool_name == "schedule_view" and isinstance(arguments, ScheduleViewArgs):
        return _observe_schedule_view(
            agent,
            raw_input=raw_input,
            view_name=arguments.view,
            anchor=arguments.anchor,
            tag=arguments.tag,
        )

    if tool_name == "schedule_add" and isinstance(arguments, ScheduleAddArgs):
        repeat_interval_minutes = arguments.interval_minutes
        if "times" in arguments.model_fields_set:
            assert arguments.times is not None
            repeat_times = arguments.times
        else:
            repeat_times = -1 if repeat_interval_minutes is not None else 1
        recurrence_error = _validate_schedule_recurrence(
            action="add",
            repeat_interval_minutes=repeat_interval_minutes,
            repeat_times=repeat_times,
            has_repeat_remind_start_time="remind_start_time" in arguments.model_fields_set,
        )
        if recurrence_error is not None:
            return _schedule_observation(raw_input=raw_input, ok=False, result=recurrence_error)
        return _observe_schedule_add(
            agent,
            raw_input=raw_input,
            event_time=arguments.event_time,
            title=arguments.title,
            tag=arguments.tag or "default",
            duration_minutes=arguments.duration_minutes if arguments.duration_minutes is not None else 60,
            remind_at=arguments.remind_at,
            repeat_interval_minutes=repeat_interval_minutes,
            repeat_times=repeat_times,
            repeat_remind_start_time=arguments.remind_start_time,
        )

    if tool_name == "schedule_get" and isinstance(arguments, ScheduleIdArgs):
        return _observe_schedule_get(agent, raw_input=raw_input, schedule_id=arguments.id)

    if tool_name == "schedule_update" and isinstance(arguments, ScheduleUpdateArgs):
        repeat_interval_minutes = (
            arguments.interval_minutes if "interval_minutes" in arguments.model_fields_set else None
        )
        if "times" in arguments.model_fields_set:
            assert arguments.times is not None
            repeat_times = arguments.times
        else:
            repeat_times = -1 if repeat_interval_minutes is not None else 1
        recurrence_error = _validate_schedule_recurrence(
            action="update",
            repeat_interval_minutes=repeat_interval_minutes,
            repeat_times=repeat_times,
            has_repeat_remind_start_time="remind_start_time" in arguments.model_fields_set,
        )
        if recurrence_error is not None:
            return _schedule_observation(raw_input=raw_input, ok=False, result=recurrence_error)
        return _observe_schedule_update(
            agent,
            raw_input=raw_input,
            schedule_id=arguments.id,
            event_time=arguments.event_time,
            title=arguments.title,
            tag=arguments.tag,
            tag_present="tag" in arguments.model_fields_set,
            duration_minutes=arguments.duration_minutes if "duration_minutes" in arguments.model_fields_set else None,
            remind_at=arguments.remind_at,
            remind_present="remind_at" in arguments.model_fields_set,
            repeat_interval_minutes=repeat_interval_minutes,
            repeat_times=repeat_times,
            repeat_remind_start_time=arguments.remind_start_time,
            repeat_remind_start_present="remind_start_time" in arguments.model_fields_set,
        )

    if tool_name == "schedule_delete" and isinstance(arguments, ScheduleIdArgs):
        return _observe_schedule_delete(agent, raw_input=raw_input, schedule_id=arguments.id)

    if tool_name == "schedule_repeat" and isinstance(arguments, ScheduleRepeatArgs):
        return _observe_schedule_repeat(
            agent,
            raw_input=raw_input,
            schedule_id=arguments.id,
            enabled=arguments.enabled,
        )

    return None


def _schedule_validation_error_text(*, payload: dict[str, Any], exc: ValidationError) -> str:
    action = str(payload.get("action") or "").strip().lower()
    errors = exc.errors(include_url=False)
    first_error = (
        errors[0]
        if errors
        else {"type": "value_error", "loc": (), "msg": "validation error", "input": None}
    )
    location = first_error.get("loc", ())
    field_names = {str(item) for item in location}
    message = str(first_error.get("msg") or "").removeprefix("Value error, ").strip()

    if message.startswith("schedule."):
        return message
    if action not in {"add", "list", "get", "view", "update", "delete", "repeat"}:
        return "schedule.action 非法。"
    if action == "view" and "view" in field_names:
        return "schedule.view 需要合法 view(day|week|month)。"
    if action == "view" and ("anchor" in field_names or message == "anchor must match view"):
        return "schedule.view 的 anchor 非法。"
    if "id" in field_names:
        return "schedule.id 必须为正整数。"
    if action in {"add", "update"} and field_names.intersection({"event_time", "title"}):
        return f"schedule.{action} 缺少 event_time/title 或格式非法。"
    if action in {"add", "update"} and "remind_at" in field_names:
        return f"schedule.{action} remind_at 格式非法。"
    if action in {"add", "update"} and "remind_start_time" in field_names:
        return f"schedule.{action} remind_start_time 格式非法。"
    if action in {"add", "update"} and "duration_minutes" in field_names:
        return f"schedule.{action} duration_minutes 需为 >=1 的整数。"
    if action in {"add", "update"} and "interval_minutes" in field_names:
        return f"schedule.{action} interval_minutes 需为 >=1 的整数。"
    if action in {"add", "update"} and "times" in field_names:
        return f"schedule.{action} times 需为 -1 或 >=2 的整数。"
    if action == "repeat" and "enabled" in field_names:
        return "schedule.repeat 需要 enabled 布尔值。"
    return "schedule.action 非法。"
