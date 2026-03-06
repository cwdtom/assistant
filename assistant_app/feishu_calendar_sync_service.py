from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from assistant_app.db import AssistantDB, ScheduleItem
from assistant_app.feishu_calendar_client import FeishuCalendarClient, FeishuCalendarClientError, FeishuCalendarEvent

_DEFAULT_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class _WriteSyncTask:
    action: str
    schedule_id: int
    feishu_event_id: str | None = None


@dataclass(frozen=True)
class _LocalSchedulePayload:
    title: str
    tag: str
    event_time: str
    duration_minutes: int


class FeishuCalendarSyncService:
    def __init__(
        self,
        *,
        db: AssistantDB,
        client: FeishuCalendarClient,
        logger: logging.Logger,
        calendar_id: str,
        reconcile_interval_minutes: int,
        bootstrap_past_days: int,
        bootstrap_future_days: int,
        timezone: str = _DEFAULT_TIMEZONE,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._client = client
        self._logger = logger
        self._calendar_id = calendar_id.strip()
        self._reconcile_interval_minutes = max(reconcile_interval_minutes, 1)
        self._bootstrap_past_days = max(bootstrap_past_days, 0)
        self._bootstrap_future_days = max(bootstrap_future_days, 0)
        self._timezone = timezone.strip() or _DEFAULT_TIMEZONE
        self._clock = clock or datetime.now
        self._task_queue: queue.Queue[_WriteSyncTask] = queue.Queue()
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._worker_thread: threading.Thread | None = None
        self._next_reconcile_at: datetime | None = None

    def start(self) -> None:
        with self._state_lock:
            worker = self._worker_thread
            if worker is not None and worker.is_alive():
                return
            self._stop_event.clear()
            self._worker_thread = threading.Thread(
                target=self._run_worker,
                name="feishu-calendar-sync-worker",
                daemon=True,
            )
            self._worker_thread.start()

    def stop(self, *, join_timeout: float = 2.0) -> None:
        self._stop_event.set()
        with self._state_lock:
            worker = self._worker_thread
        if worker is None:
            return
        worker.join(timeout=max(join_timeout, 0.0))
        with self._state_lock:
            if self._worker_thread is worker and not worker.is_alive():
                self._worker_thread = None

    def on_local_schedule_added(self, *, schedule_id: int) -> None:
        self._enqueue_write_task(_WriteSyncTask(action="add", schedule_id=schedule_id))

    def on_local_schedule_updated(self, *, schedule_id: int) -> None:
        self._enqueue_write_task(_WriteSyncTask(action="update", schedule_id=schedule_id))

    def on_local_schedule_deleted(self, *, schedule_id: int, feishu_event_id: str | None = None) -> None:
        self._enqueue_write_task(
            _WriteSyncTask(action="delete", schedule_id=schedule_id, feishu_event_id=feishu_event_id)
        )

    def run_startup_bootstrap_sync(self) -> None:
        now = self._clock()
        window_start, window_end = self._window_bounds(now)
        self._logger.info(
            "feishu calendar bootstrap start",
            extra={
                "event": "feishu_calendar_bootstrap_start",
                "context": {
                    "calendar_id": self._calendar_id,
                    "window_start": window_start.strftime("%Y-%m-%d %H:%M:%S"),
                    "window_end": window_end.strftime("%Y-%m-%d %H:%M:%S"),
                },
            },
        )
        deleted_count = 0
        created_count = 0
        try:
            feishu_items = self._list_feishu_events(window_start=window_start, window_end=window_end)
            for event in feishu_items:
                try:
                    deleted = self._client.delete_event(
                        calendar_id=self._calendar_id,
                        event_id=event.event_id,
                        need_notification=False,
                        ignore_not_found=True,
                    )
                    if deleted:
                        deleted_count += 1
                except Exception as exc:  # noqa: BLE001
                    self._logger.warning(
                        "feishu calendar bootstrap delete failed",
                        extra={
                            "event": "feishu_calendar_bootstrap_delete_failed",
                            "context": {"event_id": event.event_id, "error": repr(exc)},
                        },
                    )
                self._db.delete_schedule_feishu_mapping_by_event_id(event.event_id, calendar_id=self._calendar_id)

            local_items = self._db.list_base_schedules_in_window(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._window_max_days,
            )
            for item in local_items:
                event_id = self._create_feishu_event_from_schedule(item)
                if event_id is None:
                    continue
                if self._db.upsert_schedule_feishu_mapping(
                    schedule_id=item.id,
                    feishu_event_id=event_id,
                    calendar_id=self._calendar_id,
                ):
                    created_count += 1
            self._logger.info(
                "feishu calendar bootstrap done",
                extra={
                    "event": "feishu_calendar_bootstrap_done",
                    "context": {
                        "calendar_id": self._calendar_id,
                        "deleted_count": deleted_count,
                        "created_count": created_count,
                        "ok": True,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "feishu calendar bootstrap failed",
                extra={
                    "event": "feishu_calendar_bootstrap_failed",
                    "context": {
                        "calendar_id": self._calendar_id,
                        "deleted_count": deleted_count,
                        "created_count": created_count,
                        "error": repr(exc),
                        "ok": False,
                    },
                },
            )
        finally:
            # Startup should not immediately trigger Feishu->local reconcile pull.
            with self._state_lock:
                self._next_reconcile_at = now + timedelta(minutes=self._reconcile_interval_minutes)

    def poll_scheduled_reconcile(self) -> None:
        now = self._clock()
        with self._state_lock:
            if self._next_reconcile_at is not None and now < self._next_reconcile_at:
                return
            self._next_reconcile_at = now + timedelta(minutes=self._reconcile_interval_minutes)
        self._run_reconcile(now=now)

    @property
    def _window_max_days(self) -> int:
        return max(1, self._bootstrap_past_days + self._bootstrap_future_days + 2)

    def _run_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = self._task_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process_write_task(task)
            except Exception as exc:  # noqa: BLE001
                self._logger.warning(
                    "feishu calendar write sync failed",
                    extra={
                        "event": "feishu_calendar_sync_write_failed",
                        "context": {
                            "action": task.action,
                            "schedule_id": task.schedule_id,
                            "feishu_event_id": task.feishu_event_id or "",
                            "error": repr(exc),
                        },
                    },
                )

    def _enqueue_write_task(self, task: _WriteSyncTask) -> None:
        self._task_queue.put(task)
        self._logger.info(
            "feishu calendar write enqueued",
            extra={
                "event": "feishu_calendar_sync_write_enqueued",
                "context": {
                    "action": task.action,
                    "schedule_id": task.schedule_id,
                    "feishu_event_id": task.feishu_event_id or "",
                },
            },
        )

    def _process_write_task(self, task: _WriteSyncTask) -> None:
        self._logger.info(
            "feishu calendar write start",
            extra={
                "event": "feishu_calendar_sync_write_start",
                "context": {
                    "action": task.action,
                    "schedule_id": task.schedule_id,
                    "feishu_event_id": task.feishu_event_id or "",
                },
            },
        )
        if task.action == "add":
            self._process_add(task.schedule_id)
        elif task.action == "update":
            self._process_update(task.schedule_id)
        elif task.action == "delete":
            self._process_delete(task.schedule_id, task.feishu_event_id)
        else:
            raise RuntimeError(f"unknown feishu sync action: {task.action}")

    def _process_add(self, schedule_id: int) -> None:
        item = self._db.get_schedule(schedule_id)
        if item is None:
            return
        event_id = self._create_feishu_event_from_schedule(item)
        if event_id is None:
            return
        self._db.upsert_schedule_feishu_mapping(
            schedule_id=schedule_id,
            feishu_event_id=event_id,
            calendar_id=self._calendar_id,
        )
        self._log_write_done(action="add", schedule_id=schedule_id, event_id=event_id)

    def _process_update(self, schedule_id: int) -> None:
        old_mapping = self._db.get_schedule_feishu_mapping(schedule_id)
        if old_mapping is not None:
            try:
                self._client.delete_event(
                    calendar_id=self._calendar_id,
                    event_id=old_mapping.feishu_event_id,
                    need_notification=False,
                    ignore_not_found=True,
                )
            finally:
                self._db.delete_schedule_feishu_mapping(schedule_id)

        item = self._db.get_schedule(schedule_id)
        if item is None:
            return
        event_id = self._create_feishu_event_from_schedule(item)
        if event_id is None:
            return
        self._db.upsert_schedule_feishu_mapping(
            schedule_id=schedule_id,
            feishu_event_id=event_id,
            calendar_id=self._calendar_id,
        )
        self._log_write_done(action="update", schedule_id=schedule_id, event_id=event_id)

    def _process_delete(self, schedule_id: int, feishu_event_id: str | None) -> None:
        event_id = (feishu_event_id or "").strip() or None
        if event_id is None:
            mapping = self._db.get_schedule_feishu_mapping(schedule_id)
            if mapping is not None:
                event_id = mapping.feishu_event_id
        if event_id is not None:
            self._client.delete_event(
                calendar_id=self._calendar_id,
                event_id=event_id,
                need_notification=False,
                ignore_not_found=True,
            )
            self._db.delete_schedule_feishu_mapping_by_event_id(event_id, calendar_id=self._calendar_id)
        self._db.delete_schedule_feishu_mapping(schedule_id)
        self._log_write_done(action="delete", schedule_id=schedule_id, event_id=event_id)

    def _log_write_done(self, *, action: str, schedule_id: int, event_id: str | None) -> None:
        self._logger.info(
            "feishu calendar write done",
            extra={
                "event": "feishu_calendar_sync_write_done",
                "context": {
                    "action": action,
                    "schedule_id": schedule_id,
                    "feishu_event_id": event_id or "",
                    "ok": True,
                },
            },
        )

    def _create_feishu_event_from_schedule(self, item: ScheduleItem) -> str | None:
        start_ts, end_ts = self._schedule_time_range(item)
        try:
            return self._client.create_event(
                calendar_id=self._calendar_id,
                summary=item.title,
                description=item.tag if item.tag else "",
                start_timestamp=start_ts,
                end_timestamp=end_ts,
                timezone=self._timezone,
                need_notification=False,
            )
        except FeishuCalendarClientError as exc:
            self._logger.warning(
                "feishu calendar create failed",
                extra={
                    "event": "feishu_calendar_sync_create_failed",
                    "context": {
                        "schedule_id": item.id,
                        "error": repr(exc),
                        "error_code": exc.code if exc.code is not None else "",
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "feishu calendar create failed",
                extra={
                    "event": "feishu_calendar_sync_create_failed",
                    "context": {"schedule_id": item.id, "error": repr(exc)},
                },
            )
        return None

    def _run_reconcile(self, *, now: datetime) -> None:
        window_start, window_end = self._window_bounds(now)
        self._logger.info(
            "feishu calendar reconcile start",
            extra={
                "event": "feishu_calendar_reconcile_start",
                "context": {
                    "calendar_id": self._calendar_id,
                    "window_start": window_start.strftime("%Y-%m-%d %H:%M:%S"),
                    "window_end": window_end.strftime("%Y-%m-%d %H:%M:%S"),
                },
            },
        )
        local_created = 0
        local_updated = 0
        local_deleted = 0
        try:
            feishu_items = self._list_feishu_events(window_start=window_start, window_end=window_end)
            feishu_by_event_id = {item.event_id: item for item in feishu_items}
            mappings = self._db.list_schedule_feishu_mappings(calendar_id=self._calendar_id)
            mapping_by_event_id = {item.feishu_event_id: item for item in mappings}
            local_items = self._db.list_base_schedules_in_window(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._window_max_days,
            )
            local_by_id = {item.id: item for item in local_items}

            for event in feishu_items:
                desired = self._build_local_payload(event)
                mapping = mapping_by_event_id.get(event.event_id)
                if mapping is None:
                    created_id = self._db.add_schedule(
                        title=desired.title,
                        event_time=desired.event_time,
                        duration_minutes=desired.duration_minutes,
                        remind_at=None,
                        tag=desired.tag,
                    )
                    self._db.upsert_schedule_feishu_mapping(
                        schedule_id=created_id,
                        feishu_event_id=event.event_id,
                        calendar_id=self._calendar_id,
                    )
                    local_created += 1
                    continue

                current = local_by_id.get(mapping.schedule_id)
                if current is None:
                    created_id = self._db.add_schedule(
                        title=desired.title,
                        event_time=desired.event_time,
                        duration_minutes=desired.duration_minutes,
                        remind_at=None,
                        tag=desired.tag,
                    )
                    self._db.upsert_schedule_feishu_mapping(
                        schedule_id=created_id,
                        feishu_event_id=event.event_id,
                        calendar_id=self._calendar_id,
                    )
                    local_created += 1
                    continue

                if self._needs_local_update(current=current, desired=desired):
                    updated = self._db.update_schedule(
                        mapping.schedule_id,
                        title=desired.title,
                        event_time=desired.event_time,
                        duration_minutes=desired.duration_minutes,
                        tag=desired.tag,
                        remind_at=None,
                    )
                    if updated:
                        local_updated += 1

            feishu_event_ids = set(feishu_by_event_id)
            for mapping in mappings:
                if mapping.feishu_event_id in feishu_event_ids:
                    continue
                if self._db.delete_schedule(mapping.schedule_id):
                    local_deleted += 1

            refreshed_mappings = self._db.list_schedule_feishu_mappings(calendar_id=self._calendar_id)
            mapped_schedule_ids = {item.schedule_id for item in refreshed_mappings}
            local_after = self._db.list_base_schedules_in_window(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._window_max_days,
            )
            for item in local_after:
                if item.id in mapped_schedule_ids:
                    continue
                if self._db.delete_schedule(item.id):
                    local_deleted += 1

            self._logger.info(
                "feishu calendar reconcile done",
                extra={
                    "event": "feishu_calendar_reconcile_done",
                    "context": {
                        "calendar_id": self._calendar_id,
                        "feishu_count": len(feishu_items),
                        "local_created": local_created,
                        "local_updated": local_updated,
                        "local_deleted": local_deleted,
                        "ok": True,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "feishu calendar reconcile failed",
                extra={
                    "event": "feishu_calendar_reconcile_failed",
                    "context": {
                        "calendar_id": self._calendar_id,
                        "error": repr(exc),
                        "local_created": local_created,
                        "local_updated": local_updated,
                        "local_deleted": local_deleted,
                        "ok": False,
                    },
                },
            )

    def _list_feishu_events(self, *, window_start: datetime, window_end: datetime) -> list[FeishuCalendarEvent]:
        start_ts = int(window_start.timestamp())
        end_ts = int(window_end.timestamp())
        return self._client.list_events(
            calendar_id=self._calendar_id,
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            page_size=1000,
        )

    def _window_bounds(self, now: datetime) -> tuple[datetime, datetime]:
        start_day = (now - timedelta(days=self._bootstrap_past_days)).date()
        end_day = (now + timedelta(days=self._bootstrap_future_days)).date()
        window_start = datetime.combine(start_day, datetime.min.time())
        window_end = datetime.combine(end_day + timedelta(days=1), datetime.min.time()) - timedelta(seconds=1)
        return window_start, window_end

    def _schedule_time_range(self, item: ScheduleItem) -> tuple[int, int]:
        try:
            zone = ZoneInfo(self._timezone)
        except ZoneInfoNotFoundError:
            zone = ZoneInfo(_DEFAULT_TIMEZONE)
        start_dt = datetime.strptime(item.event_time, "%Y-%m-%d %H:%M").replace(tzinfo=zone)
        start_ts = int(start_dt.timestamp())
        duration = max(int(item.duration_minutes), 1)
        end_ts = int((start_dt + timedelta(minutes=duration)).timestamp())
        if end_ts <= start_ts:
            end_ts = start_ts + 60
        return start_ts, end_ts

    def _build_local_payload(self, event: FeishuCalendarEvent) -> _LocalSchedulePayload:
        timezone_name = event.timezone.strip() or self._timezone
        try:
            event_zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            event_zone = ZoneInfo(_DEFAULT_TIMEZONE)
        try:
            local_zone = ZoneInfo(self._timezone)
        except ZoneInfoNotFoundError:
            local_zone = ZoneInfo(_DEFAULT_TIMEZONE)

        start_dt = datetime.fromtimestamp(event.start_timestamp, tz=event_zone).astimezone(local_zone)
        duration_minutes = max(1, int((event.end_timestamp - event.start_timestamp) // 60))
        title = event.summary.strip() or "(无标题日程)"
        tag = event.description.strip() or "default"
        return _LocalSchedulePayload(
            title=title,
            tag=tag,
            event_time=start_dt.strftime("%Y-%m-%d %H:%M"),
            duration_minutes=duration_minutes,
        )

    @staticmethod
    def _needs_local_update(*, current: ScheduleItem, desired: _LocalSchedulePayload) -> bool:
        if current.title != desired.title:
            return True
        if current.tag != desired.tag:
            return True
        if current.event_time != desired.event_time:
            return True
        if current.duration_minutes != desired.duration_minutes:
            return True
        if current.remind_at is not None:
            return True
        return False
