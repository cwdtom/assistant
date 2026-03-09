from __future__ import annotations

from typing import Any

from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.parsing_utils import (
    _INVALID_OPTION_VALUE,
    _default_schedule_list_window,
    _filter_schedules_by_calendar_view,
    _normalize_datetime_text,
    _normalize_optional_datetime_value,
    _normalize_positive_int_value,
    _normalize_schedule_duration_minutes_value,
    _normalize_schedule_interval_minutes_value,
    _normalize_schedule_repeat_times_value,
    _normalize_schedule_tag_value,
    _normalize_schedule_view_anchor,
    _normalize_schedule_view_value,
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
)


def execute_schedule_system_action(
    agent: Any,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    *,
    raw_input: str,
) -> Any:
    typed_observation = _execute_typed_schedule_system_action(agent, payload=payload, raw_input=raw_input)
    if typed_observation is not None:
        return typed_observation

    assert isinstance(payload, dict)
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"add", "list", "get", "view", "update", "delete", "repeat"}:
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=False, result="schedule.action 非法。")

    if action == "list":
        list_tag = _normalize_schedule_tag_value(payload.get("tag"))
        window_start, window_end = _default_schedule_list_window(window_days=agent._schedule_max_window_days)
        items = agent.db.list_schedules(
            window_start=window_start,
            window_end=window_end,
            max_window_days=agent._schedule_max_window_days,
            tag=list_tag,
        )
        if not items:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result=_schedule_list_empty_text(window_days=agent._schedule_max_window_days, tag=list_tag),
            )
        table = _render_table(
            headers=_schedule_table_headers(),
            rows=_schedule_table_rows(items),
        )
        return PlannerObservation(
            tool="schedule",
            input_text=raw_input,
            ok=True,
            result=f"{_schedule_list_title(window_days=agent._schedule_max_window_days, tag=list_tag)}:\n{table}",
        )

    if action == "view":
        view_name = _normalize_schedule_view_value(payload.get("view"))
        if view_name is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.view 需要合法 view(day|week|month)。",
            )
        anchor: str | None = None
        if "anchor" in payload and payload.get("anchor") is not None:
            anchor = _normalize_schedule_view_anchor(view_name=view_name, value=str(payload.get("anchor")))
            if anchor is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.view 的 anchor 非法。",
                )
        window_start, window_end = _resolve_schedule_view_window(view_name=view_name, anchor=anchor)
        view_tag = _normalize_schedule_tag_value(payload.get("tag"))
        items = agent.db.list_schedules(
            window_start=window_start,
            window_end=window_end,
            max_window_days=agent._schedule_max_window_days,
            tag=view_tag,
        )
        items = _filter_schedules_by_calendar_view(items, view_name=view_name, anchor=anchor)
        if not items:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result=f"{view_name} 视图下{f'（标签:{view_tag}）' if view_tag else ''}暂无日程。",
            )
        table = _render_table(
            headers=_schedule_table_headers(),
            rows=_schedule_table_rows(items),
        )
        title = _schedule_view_title(view_name=view_name, anchor=anchor, tag=view_tag)
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=True, result=f"{title}:\n{table}")

    if action == "add":
        add_event_time = _normalize_datetime_text(str(payload.get("event_time") or ""))
        add_title = str(payload.get("title") or "").strip()
        add_tag = _normalize_schedule_tag_value(payload.get("tag")) or "default"
        if not add_event_time or not add_title:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add 缺少 event_time/title 或格式非法。",
            )
        if "duration_minutes" in payload:
            add_duration_minutes = _normalize_schedule_duration_minutes_value(payload.get("duration_minutes"))
            if add_duration_minutes is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.add duration_minutes 需为 >=1 的整数。",
                )
        else:
            add_duration_minutes = 60
        add_remind_at = _normalize_optional_datetime_value(
            payload.get("remind_at"),
            key_present="remind_at" in payload,
        )
        if add_remind_at is _INVALID_OPTION_VALUE:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add remind_at 格式非法。",
            )
        add_remind_at_text = add_remind_at if isinstance(add_remind_at, str) else None
        if "interval_minutes" in payload:
            add_repeat_interval_minutes = _normalize_schedule_interval_minutes_value(payload.get("interval_minutes"))
            if add_repeat_interval_minutes is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.add interval_minutes 需为 >=1 的整数。",
                )
        else:
            add_repeat_interval_minutes = None
        if "times" in payload:
            add_repeat_times = _normalize_schedule_repeat_times_value(payload.get("times"))
            if add_repeat_times is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.add times 需为 -1 或 >=2 的整数。",
                )
        else:
            add_repeat_times = -1 if add_repeat_interval_minutes is not None else 1
        has_repeat_remind_start_time = "remind_start_time" in payload
        add_repeat_remind_start_time = _normalize_optional_datetime_value(
            payload.get("remind_start_time"),
            key_present=has_repeat_remind_start_time,
        )
        if add_repeat_remind_start_time is _INVALID_OPTION_VALUE:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add remind_start_time 格式非法。",
            )
        add_repeat_remind_start_time_text = (
            add_repeat_remind_start_time if isinstance(add_repeat_remind_start_time, str) else None
        )
        if add_repeat_interval_minutes is None and add_repeat_times != 1:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add 提供 times 时必须同时提供 interval_minutes。",
            )
        if add_repeat_interval_minutes is not None and add_repeat_times == 1:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add interval_minutes 存在时，times 不能为 1。",
            )
        if has_repeat_remind_start_time and add_repeat_interval_minutes is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add 提供 remind_start_time 时必须提供 interval_minutes。",
            )
        schedule_id = agent.db.add_schedule(
            title=add_title,
            event_time=add_event_time,
            duration_minutes=add_duration_minutes,
            remind_at=add_remind_at_text,
            tag=add_tag,
        )
        if add_repeat_interval_minutes is not None and add_repeat_times != 1:
            agent.db.set_schedule_recurrence(
                schedule_id,
                start_time=add_event_time,
                repeat_interval_minutes=add_repeat_interval_minutes,
                repeat_times=add_repeat_times,
                remind_start_time=add_repeat_remind_start_time_text,
            )
        notify_added = getattr(agent, "notify_schedule_added", None)
        if callable(notify_added):
            notify_added(schedule_id)
        remind_meta = _format_schedule_remind_meta_inline(
            remind_at=add_remind_at_text,
            repeat_remind_start_time=add_repeat_remind_start_time_text,
        )
        if add_repeat_times == 1:
            result = (
                f"已添加日程 #{schedule_id} [标签:{add_tag}]: {add_event_time} {add_title} "
                f"({add_duration_minutes} 分钟){remind_meta}"
            )
        elif add_repeat_times == -1:
            result = (
                f"已添加无限重复日程 #{schedule_id} [标签:{add_tag}]: {add_event_time} {add_title} "
                f"(duration={add_duration_minutes}m, interval={add_repeat_interval_minutes}m{remind_meta})"
            )
        else:
            result = (
                f"已添加重复日程 {add_repeat_times} 条 [标签:{add_tag}]: {add_event_time} {add_title} "
                f"(duration={add_duration_minutes}m, interval={add_repeat_interval_minutes}m, "
                f"times={add_repeat_times}{remind_meta})"
            )
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=True, result=result)

    target_schedule_id = _normalize_positive_int_value(payload.get("id"))
    if target_schedule_id is None:
        return PlannerObservation(
            tool="schedule",
            input_text=raw_input,
            ok=False,
            result="schedule.id 必须为正整数。",
        )

    if action == "get":
        item = agent.db.get_schedule(target_schedule_id)
        if item is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result=f"未找到日程 #{target_schedule_id}",
            )
        table = _render_table(
            headers=_schedule_table_headers(),
            rows=_schedule_table_rows([item]),
        )
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=True, result=f"日程详情:\n{table}")

    if action == "update":
        event_time = _normalize_datetime_text(str(payload.get("event_time") or ""))
        title = str(payload.get("title") or "").strip()
        if not event_time or not title:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update 缺少 event_time/title 或格式非法。",
            )
        current_item = agent.db.get_schedule(target_schedule_id)
        if current_item is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result=f"未找到日程 #{target_schedule_id}",
            )
        update_tag = _normalize_schedule_tag_value(payload.get("tag"))
        if "duration_minutes" in payload:
            parsed_duration_minutes = _normalize_schedule_duration_minutes_value(payload.get("duration_minutes"))
            if parsed_duration_minutes is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.update duration_minutes 需为 >=1 的整数。",
                )
        else:
            parsed_duration_minutes = None
        if parsed_duration_minutes is not None:
            applied_duration_minutes = parsed_duration_minutes
        else:
            applied_duration_minutes = current_item.duration_minutes
        has_remind = "remind_at" in payload
        parsed_remind_at = _normalize_optional_datetime_value(payload.get("remind_at"), key_present=has_remind)
        if parsed_remind_at is _INVALID_OPTION_VALUE:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update remind_at 格式非法。",
            )
        parsed_remind_at_text = parsed_remind_at if isinstance(parsed_remind_at, str) else None
        if "interval_minutes" in payload:
            repeat_interval_minutes = _normalize_schedule_interval_minutes_value(payload.get("interval_minutes"))
            if repeat_interval_minutes is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.update interval_minutes 需为 >=1 的整数。",
                )
        else:
            repeat_interval_minutes = None
        if "times" in payload:
            repeat_times = _normalize_schedule_repeat_times_value(payload.get("times"))
            if repeat_times is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.update times 需为 -1 或 >=2 的整数。",
                )
        else:
            repeat_times = -1 if repeat_interval_minutes is not None else 1
        has_repeat_remind_start_time = "remind_start_time" in payload
        repeat_remind_start_time = _normalize_optional_datetime_value(
            payload.get("remind_start_time"),
            key_present=has_repeat_remind_start_time,
        )
        if repeat_remind_start_time is _INVALID_OPTION_VALUE:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update remind_start_time 格式非法。",
            )
        repeat_remind_start_time_text = (
            repeat_remind_start_time if isinstance(repeat_remind_start_time, str) else None
        )
        if repeat_interval_minutes is None and repeat_times != 1:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update 提供 times 时必须同时提供 interval_minutes。",
            )
        if repeat_interval_minutes is not None and repeat_times == 1:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update interval_minutes 存在时，times 不能为 1。",
            )
        if has_repeat_remind_start_time and repeat_interval_minutes is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update 提供 remind_start_time 时必须提供 interval_minutes。",
            )
        schedule_update_kwargs: dict[str, Any] = {
            "title": title,
            "event_time": event_time,
            "duration_minutes": applied_duration_minutes,
        }
        if "tag" in payload:
            schedule_update_kwargs["tag"] = update_tag or "default"
        if has_remind:
            schedule_update_kwargs["remind_at"] = parsed_remind_at_text
        if has_repeat_remind_start_time:
            schedule_update_kwargs["repeat_remind_start_time"] = repeat_remind_start_time_text
        updated = agent.db.update_schedule(target_schedule_id, **schedule_update_kwargs)
        if not updated:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result=f"未找到日程 #{target_schedule_id}",
            )
        if repeat_times == 1:
            agent.db.clear_schedule_recurrence(target_schedule_id)
            notify_updated = getattr(agent, "notify_schedule_updated", None)
            if callable(notify_updated):
                notify_updated(target_schedule_id, old_schedule=current_item)
            item = agent.db.get_schedule(target_schedule_id)
            remind_meta = _format_schedule_remind_meta_inline(
                remind_at=item.remind_at if item else None,
                repeat_remind_start_time=item.repeat_remind_start_time if item else None,
            )
            result = (
                f"已更新日程 #{target_schedule_id}: {event_time} {title} "
                f"({applied_duration_minutes} 分钟){remind_meta}"
            )
            if item is not None:
                result = (
                    f"已更新日程 #{target_schedule_id} [标签:{item.tag}]: {event_time} {title} "
                    f"({applied_duration_minutes} 分钟){remind_meta}"
                )
            ok = _is_planner_command_success(result, tool="schedule")
            return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)
        if repeat_interval_minutes is not None:
            remind_start_for_rule = (
                repeat_remind_start_time_text
                if has_repeat_remind_start_time
                else current_item.repeat_remind_start_time
            )
            agent.db.set_schedule_recurrence(
                target_schedule_id,
                start_time=event_time,
                repeat_interval_minutes=repeat_interval_minutes,
                repeat_times=repeat_times,
                remind_start_time=remind_start_for_rule,
            )
        notify_updated = getattr(agent, "notify_schedule_updated", None)
        if callable(notify_updated):
            notify_updated(target_schedule_id, old_schedule=current_item)
        item = agent.db.get_schedule(target_schedule_id)
        remind_meta = _format_schedule_remind_meta_inline(
            remind_at=item.remind_at if item else None,
            repeat_remind_start_time=item.repeat_remind_start_time if item else None,
        )
        if repeat_times == -1:
            result = (
                f"已更新为无限重复日程 #{target_schedule_id} [标签:{item.tag if item else current_item.tag}]: "
                f"{event_time} {title} "
                f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m{remind_meta})"
            )
        else:
            result = (
                f"已更新日程 #{target_schedule_id} [标签:{item.tag if item else current_item.tag}]: "
                f"{event_time} {title} "
                f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m, "
                f"times={repeat_times}{remind_meta})"
            )
        ok = _is_planner_command_success(result, tool="schedule")
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)

    if action == "delete":
        current_item = agent.db.get_schedule(target_schedule_id)
        deleted = agent.db.delete_schedule(target_schedule_id)
        if not deleted:
            result = f"未找到日程 #{target_schedule_id}"
        else:
            notify_deleted = getattr(agent, "notify_schedule_deleted", None)
            if callable(notify_deleted):
                notify_deleted(target_schedule_id, deleted_schedule=current_item)
            result = f"日程 #{target_schedule_id} 已删除。"
        ok = _is_planner_command_success(result, tool="schedule")
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)

    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        return PlannerObservation(
            tool="schedule",
            input_text=raw_input,
            ok=False,
            result="schedule.repeat 需要 enabled 布尔值。",
        )
    changed = agent.db.set_schedule_recurrence_enabled(target_schedule_id, enabled)
    if not changed:
        result = f"日程 #{target_schedule_id} 没有可切换的重复规则。"
    else:
        status = "启用" if enabled else "停用"
        result = f"已{status}日程 #{target_schedule_id} 的重复规则。"
    ok = _is_planner_command_success(result, tool="schedule")
    return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)


def _execute_typed_schedule_system_action(
    agent: Any,
    *,
    payload: dict[str, Any] | RuntimePlannerActionPayload,
    raw_input: str,
) -> PlannerObservation | None:
    if not isinstance(payload, RuntimePlannerActionPayload):
        return None

    tool_name = payload.tool_name
    arguments = payload.arguments

    if tool_name == "schedule_list" and isinstance(arguments, ScheduleListArgs):
        list_tag = _normalize_schedule_tag_value(arguments.tag)
        window_start, window_end = _default_schedule_list_window(window_days=agent._schedule_max_window_days)
        items = agent.db.list_schedules(
            window_start=window_start,
            window_end=window_end,
            max_window_days=agent._schedule_max_window_days,
            tag=list_tag,
        )
        if not items:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result=_schedule_list_empty_text(window_days=agent._schedule_max_window_days, tag=list_tag),
            )
        table = _render_table(
            headers=_schedule_table_headers(),
            rows=_schedule_table_rows(items),
        )
        return PlannerObservation(
            tool="schedule",
            input_text=raw_input,
            ok=True,
            result=f"{_schedule_list_title(window_days=agent._schedule_max_window_days, tag=list_tag)}:\n{table}",
        )

    if tool_name == "schedule_view" and isinstance(arguments, ScheduleViewArgs):
        anchor = arguments.anchor
        if anchor is not None:
            anchor = _normalize_schedule_view_anchor(view_name=arguments.view, value=anchor)
            if anchor is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.view 的 anchor 非法。",
                )
        window_start, window_end = _resolve_schedule_view_window(view_name=arguments.view, anchor=anchor)
        view_tag = _normalize_schedule_tag_value(arguments.tag)
        items = agent.db.list_schedules(
            window_start=window_start,
            window_end=window_end,
            max_window_days=agent._schedule_max_window_days,
            tag=view_tag,
        )
        items = _filter_schedules_by_calendar_view(items, view_name=arguments.view, anchor=anchor)
        if not items:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result=f"{arguments.view} 视图下{f'（标签:{view_tag}）' if view_tag else ''}暂无日程。",
            )
        table = _render_table(
            headers=_schedule_table_headers(),
            rows=_schedule_table_rows(items),
        )
        title = _schedule_view_title(view_name=arguments.view, anchor=anchor, tag=view_tag)
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=True, result=f"{title}:\n{table}")

    if tool_name == "schedule_add" and isinstance(arguments, ScheduleAddArgs):
        if "duration_minutes" in arguments.model_fields_set and arguments.duration_minutes is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add duration_minutes 需为 >=1 的整数。",
            )
        if "interval_minutes" in arguments.model_fields_set and arguments.interval_minutes is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add interval_minutes 需为 >=1 的整数。",
            )
        if "times" in arguments.model_fields_set and arguments.times is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add times 需为 -1 或 >=2 的整数。",
            )

        add_event_time = arguments.event_time
        add_title = arguments.title
        add_tag = _normalize_schedule_tag_value(arguments.tag) or "default"
        add_duration_minutes = arguments.duration_minutes if arguments.duration_minutes is not None else 60
        add_remind_at_text = arguments.remind_at
        add_repeat_interval_minutes = arguments.interval_minutes
        add_repeat_times = arguments.times if arguments.times is not None else (
            -1 if add_repeat_interval_minutes is not None else 1
        )
        add_repeat_remind_start_time_text = arguments.remind_start_time
        has_repeat_remind_start_time = "remind_start_time" in arguments.model_fields_set

        if add_repeat_interval_minutes is None and add_repeat_times != 1:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add 提供 times 时必须同时提供 interval_minutes。",
            )
        if add_repeat_interval_minutes is not None and add_repeat_times == 1:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add interval_minutes 存在时，times 不能为 1。",
            )
        if has_repeat_remind_start_time and add_repeat_interval_minutes is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.add 提供 remind_start_time 时必须提供 interval_minutes。",
            )

        schedule_id = agent.db.add_schedule(
            title=add_title,
            event_time=add_event_time,
            duration_minutes=add_duration_minutes,
            remind_at=add_remind_at_text,
            tag=add_tag,
        )
        if add_repeat_interval_minutes is not None and add_repeat_times != 1:
            agent.db.set_schedule_recurrence(
                schedule_id,
                start_time=add_event_time,
                repeat_interval_minutes=add_repeat_interval_minutes,
                repeat_times=add_repeat_times,
                remind_start_time=add_repeat_remind_start_time_text,
            )
        notify_added = getattr(agent, "notify_schedule_added", None)
        if callable(notify_added):
            notify_added(schedule_id)
        remind_meta = _format_schedule_remind_meta_inline(
            remind_at=add_remind_at_text,
            repeat_remind_start_time=add_repeat_remind_start_time_text,
        )
        if add_repeat_times == 1:
            result = (
                f"已添加日程 #{schedule_id} [标签:{add_tag}]: {add_event_time} {add_title} "
                f"({add_duration_minutes} 分钟){remind_meta}"
            )
        elif add_repeat_times == -1:
            result = (
                f"已添加无限重复日程 #{schedule_id} [标签:{add_tag}]: {add_event_time} {add_title} "
                f"(duration={add_duration_minutes}m, interval={add_repeat_interval_minutes}m{remind_meta})"
            )
        else:
            result = (
                f"已添加重复日程 {add_repeat_times} 条 [标签:{add_tag}]: {add_event_time} {add_title} "
                f"(duration={add_duration_minutes}m, interval={add_repeat_interval_minutes}m, "
                f"times={add_repeat_times}{remind_meta})"
            )
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=True, result=result)

    if tool_name == "schedule_get" and isinstance(arguments, ScheduleIdArgs):
        item = agent.db.get_schedule(arguments.id)
        if item is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result=f"未找到日程 #{arguments.id}",
            )
        table = _render_table(
            headers=_schedule_table_headers(),
            rows=_schedule_table_rows([item]),
        )
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=True, result=f"日程详情:\n{table}")

    if tool_name == "schedule_update" and isinstance(arguments, ScheduleUpdateArgs):
        if "duration_minutes" in arguments.model_fields_set and arguments.duration_minutes is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update duration_minutes 需为 >=1 的整数。",
            )
        if "interval_minutes" in arguments.model_fields_set and arguments.interval_minutes is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update interval_minutes 需为 >=1 的整数。",
            )
        if "times" in arguments.model_fields_set and arguments.times is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update times 需为 -1 或 >=2 的整数。",
            )

        current_item = agent.db.get_schedule(arguments.id)
        if current_item is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result=f"未找到日程 #{arguments.id}",
            )
        update_tag = _normalize_schedule_tag_value(arguments.tag)
        parsed_duration_minutes = arguments.duration_minutes if "duration_minutes" in arguments.model_fields_set else None
        applied_duration_minutes = (
            parsed_duration_minutes if parsed_duration_minutes is not None else current_item.duration_minutes
        )
        has_remind = "remind_at" in arguments.model_fields_set
        parsed_remind_at_text = arguments.remind_at
        repeat_interval_minutes = arguments.interval_minutes if "interval_minutes" in arguments.model_fields_set else None
        if "times" in arguments.model_fields_set:
            repeat_times = arguments.times
        else:
            repeat_times = -1 if repeat_interval_minutes is not None else 1
        has_repeat_remind_start_time = "remind_start_time" in arguments.model_fields_set
        repeat_remind_start_time_text = arguments.remind_start_time

        if repeat_interval_minutes is None and repeat_times != 1:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update 提供 times 时必须同时提供 interval_minutes。",
            )
        if repeat_interval_minutes is not None and repeat_times == 1:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update interval_minutes 存在时，times 不能为 1。",
            )
        if has_repeat_remind_start_time and repeat_interval_minutes is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.update 提供 remind_start_time 时必须提供 interval_minutes。",
            )

        schedule_update_kwargs: dict[str, Any] = {
            "title": arguments.title,
            "event_time": arguments.event_time,
            "duration_minutes": applied_duration_minutes,
        }
        if "tag" in arguments.model_fields_set:
            schedule_update_kwargs["tag"] = update_tag or "default"
        if has_remind:
            schedule_update_kwargs["remind_at"] = parsed_remind_at_text
        if has_repeat_remind_start_time:
            schedule_update_kwargs["repeat_remind_start_time"] = repeat_remind_start_time_text
        updated = agent.db.update_schedule(arguments.id, **schedule_update_kwargs)
        if not updated:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result=f"未找到日程 #{arguments.id}",
            )
        if repeat_times == 1:
            agent.db.clear_schedule_recurrence(arguments.id)
            notify_updated = getattr(agent, "notify_schedule_updated", None)
            if callable(notify_updated):
                notify_updated(arguments.id, old_schedule=current_item)
            item = agent.db.get_schedule(arguments.id)
            remind_meta = _format_schedule_remind_meta_inline(
                remind_at=item.remind_at if item else None,
                repeat_remind_start_time=item.repeat_remind_start_time if item else None,
            )
            result = (
                f"已更新日程 #{arguments.id}: {arguments.event_time} {arguments.title} "
                f"({applied_duration_minutes} 分钟){remind_meta}"
            )
            if item is not None:
                result = (
                    f"已更新日程 #{arguments.id} [标签:{item.tag}]: {arguments.event_time} {arguments.title} "
                    f"({applied_duration_minutes} 分钟){remind_meta}"
                )
            ok = _is_planner_command_success(result, tool="schedule")
            return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)
        if repeat_interval_minutes is not None:
            remind_start_for_rule = (
                repeat_remind_start_time_text if has_repeat_remind_start_time else current_item.repeat_remind_start_time
            )
            agent.db.set_schedule_recurrence(
                arguments.id,
                start_time=arguments.event_time,
                repeat_interval_minutes=repeat_interval_minutes,
                repeat_times=repeat_times,
                remind_start_time=remind_start_for_rule,
            )
        notify_updated = getattr(agent, "notify_schedule_updated", None)
        if callable(notify_updated):
            notify_updated(arguments.id, old_schedule=current_item)
        item = agent.db.get_schedule(arguments.id)
        remind_meta = _format_schedule_remind_meta_inline(
            remind_at=item.remind_at if item else None,
            repeat_remind_start_time=item.repeat_remind_start_time if item else None,
        )
        if repeat_times == -1:
            result = (
                f"已更新为无限重复日程 #{arguments.id} [标签:{item.tag if item else current_item.tag}]: "
                f"{arguments.event_time} {arguments.title} "
                f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m{remind_meta})"
            )
        else:
            result = (
                f"已更新日程 #{arguments.id} [标签:{item.tag if item else current_item.tag}]: "
                f"{arguments.event_time} {arguments.title} "
                f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m, "
                f"times={repeat_times}{remind_meta})"
            )
        ok = _is_planner_command_success(result, tool="schedule")
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)

    if tool_name == "schedule_delete" and isinstance(arguments, ScheduleIdArgs):
        current_item = agent.db.get_schedule(arguments.id)
        deleted = agent.db.delete_schedule(arguments.id)
        if not deleted:
            result = f"未找到日程 #{arguments.id}"
        else:
            notify_deleted = getattr(agent, "notify_schedule_deleted", None)
            if callable(notify_deleted):
                notify_deleted(arguments.id, deleted_schedule=current_item)
            result = f"日程 #{arguments.id} 已删除。"
        ok = _is_planner_command_success(result, tool="schedule")
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)

    if tool_name == "schedule_repeat" and isinstance(arguments, ScheduleRepeatArgs):
        changed = agent.db.set_schedule_recurrence_enabled(arguments.id, arguments.enabled)
        if not changed:
            result = f"日程 #{arguments.id} 没有可切换的重复规则。"
        else:
            status = "启用" if arguments.enabled else "停用"
            result = f"已{status}日程 #{arguments.id} 的重复规则。"
        ok = _is_planner_command_success(result, tool="schedule")
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)

    return None
