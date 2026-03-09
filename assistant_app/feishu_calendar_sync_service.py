from __future__ import annotations

import logging
import queue
import threading
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from assistant_app.db import AssistantDB, ScheduleItem
from assistant_app.feishu_calendar_client import FeishuCalendarClient, FeishuCalendarClientError, FeishuCalendarEvent

_DEFAULT_TIMEZONE = "Asia/Shanghai"
_DEFAULT_EMPTY_TITLE = "(无标题日程)"
_DEFAULT_EMPTY_DESCRIPTION = "default"


@dataclass(frozen=True)
class _WriteSyncTask:
    action: str
    schedule_id: int
    schedule_snapshot: ScheduleItem | None = None


@dataclass(frozen=True)
class _LocalSchedulePayload:
    title: str
    tag: str
    event_time: str
    duration_minutes: int


@dataclass(frozen=True)
class _IdentityKey:
    title: str
    description: str
    start_minute: int
    end_minute: int


@dataclass(frozen=True)
class _IdentityMatch:
    event: FeishuCalendarEvent | None
    candidate_count: int


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

    def on_local_schedule_updated(self, *, schedule_id: int, old_schedule: ScheduleItem | None = None) -> None:
        self._enqueue_write_task(
            _WriteSyncTask(action="update", schedule_id=schedule_id, schedule_snapshot=old_schedule)
        )

    def on_local_schedule_deleted(self, *, schedule_id: int, deleted_schedule: ScheduleItem | None = None) -> None:
        self._enqueue_write_task(
            _WriteSyncTask(action="delete", schedule_id=schedule_id, schedule_snapshot=deleted_schedule)
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

            local_items = self._db.list_base_schedules_in_window(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._window_max_days,
            )
            for item in local_items:
                event_id = self._create_feishu_event_from_schedule(item)
                if event_id is not None:
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
                            "has_schedule_snapshot": task.schedule_snapshot is not None,
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
                    "has_schedule_snapshot": task.schedule_snapshot is not None,
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
                    "has_schedule_snapshot": task.schedule_snapshot is not None,
                },
            },
        )
        if task.action == "add":
            self._process_add(task.schedule_id)
        elif task.action == "update":
            self._process_update(task.schedule_id, task.schedule_snapshot)
        elif task.action == "delete":
            self._process_delete(task.schedule_id, task.schedule_snapshot)
        else:
            raise RuntimeError(f"unknown feishu sync action: {task.action}")

    def _process_add(self, schedule_id: int) -> None:
        item = self._db.get_schedule(schedule_id)
        if item is None:
            return
        identity = self._identity_from_schedule(item)
        existing = self._match_feishu_event_by_identity(action="add", schedule_id=schedule_id, identity=identity)
        if existing.event is not None:
            self._logger.info(
                "feishu calendar write skipped existing identity",
                extra={
                    "event": "feishu_calendar_sync_write_skip_existing",
                    "context": {
                        "action": "add",
                        "schedule_id": schedule_id,
                        "matched_event_id": existing.event.event_id,
                        "candidate_count": existing.candidate_count,
                        "identity_key": self._identity_key_text(identity),
                    },
                },
            )
            self._log_write_done(action="add", schedule_id=schedule_id, event_id=existing.event.event_id, skipped=True)
            return

        event_id = self._create_feishu_event_from_schedule(item)
        if event_id is None:
            return
        self._log_write_done(action="add", schedule_id=schedule_id, event_id=event_id)

    def _process_update(self, schedule_id: int, old_schedule: ScheduleItem | None) -> None:
        item = self._db.get_schedule(schedule_id)
        if item is None:
            return

        old_identity = self._identity_from_schedule(old_schedule) if old_schedule is not None else None
        new_identity = self._identity_from_schedule(item)

        if old_identity is not None and old_identity != new_identity:
            old_match = self._match_feishu_event_by_identity(
                action="update",
                schedule_id=schedule_id,
                identity=old_identity,
            )
            if old_match.event is not None:
                self._client.delete_event(
                    calendar_id=self._calendar_id,
                    event_id=old_match.event.event_id,
                    need_notification=False,
                    ignore_not_found=True,
                )

        new_match = self._match_feishu_event_by_identity(
            action="update",
            schedule_id=schedule_id,
            identity=new_identity,
        )
        if new_match.event is not None:
            self._logger.info(
                "feishu calendar write skipped existing identity",
                extra={
                    "event": "feishu_calendar_sync_write_skip_existing",
                    "context": {
                        "action": "update",
                        "schedule_id": schedule_id,
                        "matched_event_id": new_match.event.event_id,
                        "candidate_count": new_match.candidate_count,
                        "identity_key": self._identity_key_text(new_identity),
                    },
                },
            )
            self._log_write_done(
                action="update",
                schedule_id=schedule_id,
                event_id=new_match.event.event_id,
                skipped=True,
            )
            return

        event_id = self._create_feishu_event_from_schedule(item)
        if event_id is None:
            return
        self._log_write_done(action="update", schedule_id=schedule_id, event_id=event_id)

    def _process_delete(self, schedule_id: int, deleted_schedule: ScheduleItem | None) -> None:
        if deleted_schedule is None:
            self._log_write_done(action="delete", schedule_id=schedule_id, event_id=None, skipped=True)
            return

        identity = self._identity_from_schedule(deleted_schedule)
        match = self._match_feishu_event_by_identity(action="delete", schedule_id=schedule_id, identity=identity)
        event_id: str | None = None
        if match.event is not None:
            event_id = match.event.event_id
            self._client.delete_event(
                calendar_id=self._calendar_id,
                event_id=event_id,
                need_notification=False,
                ignore_not_found=True,
            )
        self._log_write_done(action="delete", schedule_id=schedule_id, event_id=event_id, skipped=event_id is None)

    def _log_write_done(self, *, action: str, schedule_id: int, event_id: str | None, skipped: bool = False) -> None:
        self._logger.info(
            "feishu calendar write done",
            extra={
                "event": "feishu_calendar_sync_write_done",
                "context": {
                    "action": action,
                    "schedule_id": schedule_id,
                    "feishu_event_id": event_id or "",
                    "matched_event_id": event_id or "",
                    "skipped": skipped,
                    "ok": True,
                },
            },
        )

    def _create_feishu_event_from_schedule(self, item: ScheduleItem) -> str | None:
        start_ts, end_ts = self._schedule_time_range(item)
        try:
            return self._client.create_event(
                calendar_id=self._calendar_id,
                summary=self._normalize_title(item.title),
                description=self._normalize_description(item.tag),
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
            local_items = self._db.list_base_schedules_in_window(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._window_max_days,
            )

            feishu_groups = self._group_feishu_by_identity(feishu_items)
            local_groups = self._group_local_by_identity(local_items)

            for identity in set(feishu_groups) | set(local_groups):
                feishu_bucket = feishu_groups.get(identity, [])
                local_bucket = local_groups.get(identity, [])

                if len(feishu_bucket) > 1:
                    self._log_identity_ambiguous(
                        action="reconcile",
                        schedule_id=None,
                        identity=identity,
                        source="feishu",
                        candidate_count=len(feishu_bucket),
                    )
                if len(local_bucket) > 1:
                    self._log_identity_ambiguous(
                        action="reconcile",
                        schedule_id=None,
                        identity=identity,
                        source="local",
                        candidate_count=len(local_bucket),
                    )

                pair_count = min(len(feishu_bucket), len(local_bucket))
                for idx in range(pair_count):
                    event = feishu_bucket[idx]
                    current = local_bucket[idx]
                    self._log_identity_match(
                        action="reconcile",
                        schedule_id=current.id,
                        identity=identity,
                        event_id=event.event_id,
                        candidate_count=len(feishu_bucket),
                    )
                    desired = self._build_local_payload(event)
                    if self._needs_local_update(current=current, desired=desired):
                        updated = self._db.update_schedule(
                            current.id,
                            title=desired.title,
                            event_time=desired.event_time,
                            duration_minutes=desired.duration_minutes,
                            tag=desired.tag,
                            remind_at=None,
                        )
                        if updated:
                            local_updated += 1

                for event in feishu_bucket[pair_count:]:
                    desired = self._build_local_payload(event)
                    self._db.add_schedule(
                        title=desired.title,
                        event_time=desired.event_time,
                        duration_minutes=desired.duration_minutes,
                        remind_at=None,
                        tag=desired.tag,
                    )
                    local_created += 1

                for current in local_bucket[pair_count:]:
                    if self._db.delete_schedule(current.id):
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
        return self._list_feishu_events_by_timestamp(start_ts=start_ts, end_ts=end_ts)

    def _list_feishu_events_by_timestamp(self, *, start_ts: int, end_ts: int) -> list[FeishuCalendarEvent]:
        normalized_start_ts = max(int(start_ts), 0)
        normalized_end_ts = max(int(end_ts), normalized_start_ts + 1)
        return self._client.list_events(
            calendar_id=self._calendar_id,
            start_timestamp=normalized_start_ts,
            end_timestamp=normalized_end_ts,
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

        start_minute = event.start_timestamp // 60
        end_minute = event.end_timestamp // 60
        duration_minutes = max(1, end_minute - start_minute)
        start_dt = datetime.fromtimestamp(start_minute * 60, tz=event_zone).astimezone(local_zone)
        return _LocalSchedulePayload(
            title=self._normalize_title(event.summary),
            tag=self._normalize_description(event.description),
            event_time=start_dt.strftime("%Y-%m-%d %H:%M"),
            duration_minutes=duration_minutes,
        )

    def _match_feishu_event_by_identity(
        self,
        *,
        action: str,
        schedule_id: int,
        identity: _IdentityKey,
    ) -> _IdentityMatch:
        start_ts = identity.start_minute * 60 - 60
        end_ts = (identity.end_minute + 1) * 60 + 60
        window_items = self._list_feishu_events_by_timestamp(start_ts=start_ts, end_ts=end_ts)
        candidates = [item for item in window_items if self._identity_from_event(item) == identity]
        candidates.sort(key=self._feishu_event_order_key)

        if len(candidates) > 1:
            self._log_identity_ambiguous(
                action=action,
                schedule_id=schedule_id,
                identity=identity,
                source="feishu",
                candidate_count=len(candidates),
            )
        matched = candidates[0] if candidates else None
        if matched is not None:
            self._log_identity_match(
                action=action,
                schedule_id=schedule_id,
                identity=identity,
                event_id=matched.event_id,
                candidate_count=len(candidates),
            )
        return _IdentityMatch(event=matched, candidate_count=len(candidates))

    def _group_local_by_identity(self, items: list[ScheduleItem]) -> dict[_IdentityKey, list[ScheduleItem]]:
        grouped: dict[_IdentityKey, list[ScheduleItem]] = defaultdict(list)
        for item in items:
            grouped[self._identity_from_schedule(item)].append(item)
        for bucket in grouped.values():
            bucket.sort(key=self._local_schedule_order_key)
        return dict(grouped)

    def _group_feishu_by_identity(
        self,
        items: list[FeishuCalendarEvent],
    ) -> dict[_IdentityKey, list[FeishuCalendarEvent]]:
        grouped: dict[_IdentityKey, list[FeishuCalendarEvent]] = defaultdict(list)
        for item in items:
            grouped[self._identity_from_event(item)].append(item)
        for bucket in grouped.values():
            bucket.sort(key=self._feishu_event_order_key)
        return dict(grouped)

    def _identity_from_schedule(self, item: ScheduleItem) -> _IdentityKey:
        start_ts, end_ts = self._schedule_time_range(item)
        return _IdentityKey(
            title=self._normalize_title(item.title),
            description=self._normalize_description(item.tag),
            start_minute=start_ts // 60,
            end_minute=end_ts // 60,
        )

    def _identity_from_event(self, event: FeishuCalendarEvent) -> _IdentityKey:
        return _IdentityKey(
            title=self._normalize_title(event.summary),
            description=self._normalize_description(event.description),
            start_minute=event.start_timestamp // 60,
            end_minute=event.end_timestamp // 60,
        )

    @staticmethod
    def _normalize_title(value: str) -> str:
        normalized = str(value or "").strip()
        return normalized or _DEFAULT_EMPTY_TITLE

    @staticmethod
    def _normalize_description(value: str) -> str:
        normalized = str(value or "").strip().lower()
        return normalized or _DEFAULT_EMPTY_DESCRIPTION

    @staticmethod
    def _local_schedule_order_key(item: ScheduleItem) -> tuple[str, int]:
        return (str(item.created_at or ""), int(item.id))

    @staticmethod
    def _feishu_event_order_key(item: FeishuCalendarEvent) -> tuple[int, int, str]:
        created_ts = item.create_timestamp if item.create_timestamp is not None else item.start_timestamp
        return (int(created_ts), int(item.start_timestamp), str(item.event_id))

    @staticmethod
    def _identity_key_text(identity: _IdentityKey) -> str:
        return (
            f"title={identity.title!r}|description={identity.description!r}|"
            f"start_minute={identity.start_minute}|end_minute={identity.end_minute}"
        )

    def _log_identity_match(
        self,
        *,
        action: str,
        schedule_id: int | None,
        identity: _IdentityKey,
        event_id: str,
        candidate_count: int,
    ) -> None:
        context: dict[str, object] = {
            "action": action,
            "matched_event_id": event_id,
            "candidate_count": candidate_count,
            "match_strategy": "earliest_created",
            "identity_key": self._identity_key_text(identity),
        }
        if schedule_id is not None:
            context["schedule_id"] = schedule_id
        self._logger.info(
            "feishu calendar identity matched",
            extra={"event": "feishu_calendar_identity_match", "context": context},
        )

    def _log_identity_ambiguous(
        self,
        *,
        action: str,
        schedule_id: int | None,
        identity: _IdentityKey,
        source: str,
        candidate_count: int,
    ) -> None:
        context: dict[str, object] = {
            "action": action,
            "source": source,
            "candidate_count": candidate_count,
            "match_strategy": "earliest_created",
            "identity_key": self._identity_key_text(identity),
        }
        if schedule_id is not None:
            context["schedule_id"] = schedule_id
        self._logger.info(
            "feishu calendar identity ambiguous",
            extra={"event": "feishu_calendar_identity_ambiguous", "context": context},
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
