from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from assistant_app.db import AssistantDB, RecurringScheduleRule, ScheduleItem, TodoItem
from assistant_app.reminder_sink import ReminderEvent, ReminderSink


@dataclass(frozen=True)
class ReminderPollStats:
    candidate_count: int = 0
    delivered_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0


class ReminderService:
    def __init__(
        self,
        *,
        db: AssistantDB,
        sink: ReminderSink,
        clock: Callable[[], datetime] | None = None,
        lookahead_seconds: int = 30,
        catchup_seconds: int = 0,
        batch_limit: int = 200,
        logger: logging.Logger | None = None,
        content_rewriter: Callable[[str], str] | None = None,
    ) -> None:
        self._db = db
        self._sink = sink
        self._clock = clock or datetime.now
        self._lookahead_seconds = max(lookahead_seconds, 0)
        # V1 keeps catchup disabled even if caller passes a positive number.
        self._catchup_seconds = 0 if catchup_seconds >= 0 else 0
        self._batch_limit = max(batch_limit, 1)
        self._logger = logger or logging.getLogger("assistant_app.timer")
        self._content_rewriter = content_rewriter

    def poll_once(self) -> ReminderPollStats:
        scan_start, scan_end = self._scan_window()
        candidates = self._collect_candidates(scan_start=scan_start, scan_end=scan_end)

        delivered_count = 0
        skipped_count = 0
        failed_count = 0
        for event in candidates:
            if self._db.has_reminder_delivery(event.reminder_key):
                skipped_count += 1
                continue
            try:
                event_to_emit = self._rewrite_event_content(event)
                self._sink.emit(event_to_emit)
                saved = self._db.save_reminder_delivery(
                    reminder_key=event.reminder_key,
                    source_type=event.source_type,
                    source_id=event.source_id,
                    occurrence_time=event.occurrence_time,
                    remind_time=event.remind_time,
                )
                if saved:
                    delivered_count += 1
                else:
                    skipped_count += 1
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                self._logger.warning("timer delivery failed: %s (%s)", event.reminder_key, exc)

        return ReminderPollStats(
            candidate_count=len(candidates),
            delivered_count=delivered_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
        )

    def _rewrite_event_content(self, event: ReminderEvent) -> ReminderEvent:
        rewriter = self._content_rewriter
        if rewriter is None:
            return event
        try:
            rewritten = rewriter(event.content)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("timer content rewrite failed: %s (%s)", event.reminder_key, exc)
            return event
        normalized = rewritten.strip()
        if not normalized:
            return event
        return replace(event, content=normalized)

    def _scan_window(self) -> tuple[datetime, datetime]:
        now = self._clock()
        start = now.replace(second=0, microsecond=0) - timedelta(seconds=self._catchup_seconds)
        end = _ceil_to_minute(now + timedelta(seconds=self._lookahead_seconds))
        return start, end

    def _collect_candidates(self, *, scan_start: datetime, scan_end: datetime) -> list[ReminderEvent]:
        candidates: list[ReminderEvent] = []
        for todo in self._db.list_todos():
            event = _build_todo_reminder_event(todo, scan_start=scan_start, scan_end=scan_end)
            if event is not None:
                candidates.append(event)
        base_schedules = self._db.list_base_schedules()
        recurring_rules = self._db.list_recurring_rules()
        rule_by_schedule_id = {rule.schedule_id: rule for rule in recurring_rules}
        for schedule in base_schedules:
            rule = rule_by_schedule_id.get(schedule.id)
            if rule is None or not rule.enabled:
                event = _build_schedule_reminder_event(schedule, scan_start=scan_start, scan_end=scan_end)
                if event is not None:
                    candidates.append(event)
                continue
            candidates.extend(
                _build_recurring_schedule_reminder_events(
                    base_schedule=schedule,
                    rule=rule,
                    scan_start=scan_start,
                    scan_end=scan_end,
                )
            )
        candidates.sort(key=lambda item: (item.remind_time, item.reminder_key))
        return candidates[: self._batch_limit]


def _build_todo_reminder_event(
    todo: TodoItem,
    *,
    scan_start: datetime,
    scan_end: datetime,
) -> ReminderEvent | None:
    if todo.done or not todo.remind_at:
        return None
    remind_time = _parse_datetime(todo.remind_at)
    if remind_time is None:
        return None
    if remind_time < scan_start or remind_time > scan_end:
        return None
    reminder_key = f"todo:{todo.id}:{todo.remind_at}"
    content = f"待办提醒 #{todo.id}: {todo.content}（提醒时间 {todo.remind_at}）"
    return ReminderEvent(
        reminder_key=reminder_key,
        source_type="todo",
        source_id=todo.id,
        remind_time=todo.remind_at,
        content=content,
    )


def _build_schedule_reminder_event(
    schedule: ScheduleItem,
    *,
    scan_start: datetime,
    scan_end: datetime,
) -> ReminderEvent | None:
    if not schedule.remind_at:
        return None
    remind_time = _parse_datetime(schedule.remind_at)
    if remind_time is None:
        return None
    if remind_time < scan_start or remind_time > scan_end:
        return None
    reminder_key = f"schedule:{schedule.id}:{schedule.event_time}:{schedule.remind_at}"
    content = (
        f"日程提醒 #{schedule.id}: {schedule.title}（日程时间 {schedule.event_time}，提醒时间 {schedule.remind_at}）"
    )
    return ReminderEvent(
        reminder_key=reminder_key,
        source_type="schedule",
        source_id=schedule.id,
        remind_time=schedule.remind_at,
        content=content,
    )


def _build_recurring_schedule_reminder_events(
    *,
    base_schedule: ScheduleItem,
    rule: RecurringScheduleRule,
    scan_start: datetime,
    scan_end: datetime,
) -> list[ReminderEvent]:
    base_event_time = _parse_datetime(base_schedule.event_time)
    if base_event_time is None:
        return []
    interval = timedelta(minutes=rule.repeat_interval_minutes)
    remind_start_time = _resolve_recurring_remind_start_time(base_schedule=base_schedule, rule=rule)
    if remind_start_time is None:
        return []

    occurrence_index = _compute_first_occurrence_index(
        start=remind_start_time,
        interval=interval,
        scan_start=scan_start,
    )
    events: list[ReminderEvent] = []
    while True:
        if rule.repeat_times != -1 and occurrence_index >= rule.repeat_times:
            break
        remind_time = remind_start_time + occurrence_index * interval
        if remind_time > scan_end:
            break
        occurrence_event_time = base_event_time + occurrence_index * interval
        reminder_key = (
            "schedule:"
            f"{base_schedule.id}:{occurrence_event_time.strftime('%Y-%m-%d %H:%M')}"
            f":{remind_time.strftime('%Y-%m-%d %H:%M')}"
        )
        event_text = occurrence_event_time.strftime("%Y-%m-%d %H:%M")
        remind_text = remind_time.strftime("%Y-%m-%d %H:%M")
        content = (
            f"日程提醒 #{base_schedule.id}: {base_schedule.title}"
            f"（日程时间 {event_text}，提醒时间 {remind_text}）"
        )
        events.append(
            ReminderEvent(
                reminder_key=reminder_key,
                source_type="schedule",
                source_id=base_schedule.id,
                occurrence_time=event_text,
                remind_time=remind_text,
                content=content,
            )
        )
        occurrence_index += 1
    return events


def _resolve_recurring_remind_start_time(
    *,
    base_schedule: ScheduleItem,
    rule: RecurringScheduleRule,
) -> datetime | None:
    remind_start = _parse_datetime(rule.remind_start_time) if rule.remind_start_time else None
    if remind_start is not None:
        return remind_start
    if not base_schedule.remind_at:
        return None
    base_remind = _parse_datetime(base_schedule.remind_at)
    base_event = _parse_datetime(base_schedule.event_time)
    if base_remind is None or base_event is None:
        return None
    # Fallback keeps the same remind-event delta for each occurrence.
    delta = base_remind - base_event
    return base_event + delta


def _compute_first_occurrence_index(
    *,
    start: datetime,
    interval: timedelta,
    scan_start: datetime,
) -> int:
    if scan_start <= start:
        return 0
    interval_seconds = int(interval.total_seconds())
    if interval_seconds <= 0:
        return 0
    delta_seconds = int((scan_start - start).total_seconds())
    index = max(delta_seconds // interval_seconds, 0)
    if start + index * interval < scan_start:
        index += 1
    return index


def _parse_datetime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _ceil_to_minute(value: datetime) -> datetime:
    if value.second == 0 and value.microsecond == 0:
        return value
    return value.replace(second=0, microsecond=0) + timedelta(minutes=1)
