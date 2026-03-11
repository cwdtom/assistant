from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from assistant_app.schemas.domain import (
    ChatMessage,
    ChatTurn,
    RecurringScheduleRule,
    ReminderDelivery,
    ScheduleItem,
    ThoughtItem,
)
from assistant_app.schemas.scheduled_tasks import (
    ScheduledPlannerTask,
    ScheduledPlannerTaskCreateInput,
    ScheduledPlannerTaskUpdateInput,
)
from assistant_app.schemas.storage import (
    ScheduleBatchCreateInput,
    ScheduleCreateInput,
    ScheduleRecurrenceInput,
    ScheduleUpdateInput,
    ThoughtCreateInput,
    ThoughtUpdateInput,
)
from assistant_app.schemas.validation_errors import first_validation_issue
from assistant_app.schemas.values import (
    NormalizedTagValue,
    ScheduleDurationValue,
    ThoughtContentValue,
    ThoughtStatusValue,
)

_UNSET = object()
THOUGHT_STATUS_TODO = "未完成"
THOUGHT_STATUS_DONE = "完成"
THOUGHT_STATUS_DELETED = "删除"
TIMER_TASKS_TABLE = "timer_tasks"
LEGACY_TIMER_TASKS_TABLE = "scheduled_planner_tasks"
THOUGHT_STATUS_VALUES = (
    THOUGHT_STATUS_TODO,
    THOUGHT_STATUS_DONE,
    THOUGHT_STATUS_DELETED,
)

class AssistantDB:
    def __init__(self, db_path: str, logger: logging.Logger | None = None) -> None:
        self.db_path = db_path
        self._logger = logger or logging.getLogger("assistant_app.app")
        self._logger.propagate = False
        if not self._logger.handlers:
            self._logger.addHandler(logging.NullHandler())
        self._ensure_parent_dir()
        self._init_schema()

    def _ensure_parent_dir(self) -> None:
        path = Path(self.db_path)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    tag TEXT NOT NULL DEFAULT 'default',
                    event_time TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL DEFAULT 60 CHECK (duration_minutes >= 1),
                    remind_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._ensure_schedule_tag_column(conn)
            self._ensure_schedule_duration_column(conn)
            self._ensure_schedule_remind_column(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS recurring_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER NOT NULL UNIQUE,
                    start_time TEXT NOT NULL,
                    repeat_interval_minutes INTEGER NOT NULL CHECK (repeat_interval_minutes >= 1),
                    repeat_times INTEGER NOT NULL CHECK (repeat_times = -1 OR repeat_times >= 2),
                    remind_start_time TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
                )
                """
            )
            self._ensure_recurring_interval_column(conn)
            self._ensure_recurring_remind_start_column(conn)
            self._ensure_recurring_enabled_column(conn)
            self._ensure_recurring_repeat_times_constraint(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_content TEXT NOT NULL,
                    assistant_content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self._ensure_chat_history_turn_schema(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS thoughts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('未完成', '完成', '删除')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reminder_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reminder_key TEXT NOT NULL UNIQUE,
                    source_type TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    occurrence_time TEXT,
                    remind_time TEXT NOT NULL,
                    delivered_at TEXT NOT NULL,
                    payload TEXT
                )
                """
            )
            self._ensure_scheduled_planner_tasks_schema(conn)
            self._drop_legacy_schedule_feishu_sync_table(conn)

    def _drop_legacy_schedule_feishu_sync_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE IF EXISTS schedule_feishu_sync")

    def _ensure_scheduled_planner_tasks_schema(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute(f"PRAGMA table_info({TIMER_TASKS_TABLE})").fetchall()
        if not columns:
            legacy_columns = conn.execute(f"PRAGMA table_info({LEGACY_TIMER_TASKS_TABLE})").fetchall()
            if not legacy_columns:
                self._create_timer_tasks_table(conn)
                self._seed_timer_tasks(conn)
                return
            names = {row["name"] for row in legacy_columns}
            conn.execute(f"ALTER TABLE {LEGACY_TIMER_TASKS_TABLE} RENAME TO {TIMER_TASKS_TABLE}_old")
            self._recreate_timer_tasks_table(conn=conn, source_table=f"{TIMER_TASKS_TABLE}_old", column_names=names)
            conn.execute(f"DROP TABLE {TIMER_TASKS_TABLE}_old")
            return
        names = {row["name"] for row in columns}
        if "run_limit" in names and "enabled" not in names:
            conn.execute(
                f"""
                UPDATE {TIMER_TASKS_TABLE}
                SET run_limit = CASE
                    WHEN run_limit IS NULL THEN -1
                    WHEN run_limit = -1 THEN -1
                    WHEN run_limit < 0 THEN 0
                    ELSE run_limit
                END
                """
            )
            return

        conn.execute(f"ALTER TABLE {TIMER_TASKS_TABLE} RENAME TO {TIMER_TASKS_TABLE}_old")
        self._recreate_timer_tasks_table(conn=conn, source_table=f"{TIMER_TASKS_TABLE}_old", column_names=names)
        conn.execute(f"DROP TABLE {TIMER_TASKS_TABLE}_old")

    def _create_timer_tasks_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TIMER_TASKS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_name TEXT NOT NULL UNIQUE,
                run_limit INTEGER NOT NULL CHECK (run_limit = -1 OR run_limit >= 0),
                cron_expr TEXT NOT NULL,
                prompt TEXT NOT NULL,
                next_run_at TEXT,
                last_run_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    def _seed_timer_tasks(self, conn: sqlite3.Connection) -> None:
        timestamp = _now_iso()
        seed_rows = [
            (
                "每日用户侧写更新",
                -1,
                "0 4 * * *",
                "结合可用工具能获取的数据（一个月内的聊天记录、日程、想法等）和现有用户侧写数据总结一份新的用户侧写并覆写入侧写文件",
                None,
                None,
                timestamp,
                timestamp,
            ),
            (
                "每小时提醒",
                -1,
                "0 * * * *",
                "结合可用工具能获取的数据（聊天记录、日程、想法、用户侧写等）总结一个4小时内需要提醒的内容",
                None,
                None,
                timestamp,
                timestamp,
            ),
        ]
        conn.executemany(
            f"""
            INSERT OR IGNORE INTO {TIMER_TASKS_TABLE} (
                task_name, run_limit, cron_expr, prompt, next_run_at, last_run_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            seed_rows,
        )

    def _recreate_timer_tasks_table(
        self,
        *,
        conn: sqlite3.Connection,
        source_table: str,
        column_names: set[str],
    ) -> None:
        self._create_timer_tasks_table(conn)
        run_limit_expr = (
            "CASE "
            "WHEN enabled = 0 THEN 0 "
            "WHEN enabled = 1 THEN -1 "
            "ELSE -1 END"
        )
        if "run_limit" in column_names:
            run_limit_expr = (
                "CASE "
                "WHEN run_limit IS NULL THEN -1 "
                "WHEN run_limit = -1 THEN -1 "
                "WHEN run_limit < 0 THEN 0 "
                "ELSE run_limit END"
            )
        conn.execute(
            f"""
            INSERT INTO {TIMER_TASKS_TABLE} (
                id, task_name, run_limit, cron_expr, prompt, next_run_at, last_run_at, created_at, updated_at
            )
            SELECT
                id,
                task_name,
                {run_limit_expr},
                cron_expr,
                prompt,
                next_run_at,
                last_run_at,
                created_at,
                updated_at
            FROM {source_table}
            """
        )

    def _ensure_schedule_duration_column(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(schedules)").fetchall()
        names = {row["name"] for row in columns}
        if "duration_minutes" not in names:
            conn.execute("ALTER TABLE schedules ADD COLUMN duration_minutes INTEGER NOT NULL DEFAULT 60")
        conn.execute(
            "UPDATE schedules SET duration_minutes = 60 "
            "WHERE duration_minutes IS NULL OR duration_minutes < 1"
        )

    def _ensure_schedule_tag_column(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(schedules)").fetchall()
        names = {row["name"] for row in columns}
        if "tag" not in names:
            conn.execute("ALTER TABLE schedules ADD COLUMN tag TEXT NOT NULL DEFAULT 'default'")
        conn.execute("UPDATE schedules SET tag = 'default' WHERE tag IS NULL OR TRIM(tag) = ''")

    def _ensure_schedule_remind_column(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(schedules)").fetchall()
        names = {row["name"] for row in columns}
        if "remind_at" not in names:
            conn.execute("ALTER TABLE schedules ADD COLUMN remind_at TEXT")

    def _ensure_recurring_interval_column(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(recurring_schedules)").fetchall()
        names = {row["name"] for row in columns}
        if "repeat_interval_minutes" not in names:
            conn.execute("ALTER TABLE recurring_schedules ADD COLUMN repeat_interval_minutes INTEGER")
        # Migrate legacy repeat_name to minute interval when needed.
        if "repeat_name" in names:
            conn.execute(
                """
                UPDATE recurring_schedules
                SET repeat_interval_minutes = CASE repeat_name
                    WHEN 'daily' THEN 1440
                    WHEN 'weekly' THEN 10080
                    WHEN 'monthly' THEN 43200
                    ELSE 1440
                END
                WHERE repeat_interval_minutes IS NULL OR repeat_interval_minutes < 1
                """
            )
        conn.execute(
            "UPDATE recurring_schedules SET repeat_interval_minutes = 1440 "
            "WHERE repeat_interval_minutes IS NULL OR repeat_interval_minutes < 1"
        )

    def _ensure_recurring_enabled_column(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(recurring_schedules)").fetchall()
        names = {row["name"] for row in columns}
        if "enabled" not in names:
            conn.execute("ALTER TABLE recurring_schedules ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
        conn.execute(
            "UPDATE recurring_schedules SET enabled = 1 "
            "WHERE enabled IS NULL OR enabled NOT IN (0, 1)"
        )

    def _ensure_recurring_remind_start_column(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(recurring_schedules)").fetchall()
        names = {row["name"] for row in columns}
        if "remind_start_time" not in names:
            conn.execute("ALTER TABLE recurring_schedules ADD COLUMN remind_start_time TEXT")

    def _ensure_recurring_repeat_times_constraint(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'recurring_schedules'
            """
        ).fetchone()
        sql_text = str(row["sql"]).lower() if row and row["sql"] else ""
        if "repeat_times = -1" in sql_text:
            return

        columns = conn.execute("PRAGMA table_info(recurring_schedules)").fetchall()
        names = {item["name"] for item in columns}
        interval_expr = "1440"
        if "repeat_interval_minutes" in names and "repeat_name" in names:
            interval_expr = (
                "COALESCE(repeat_interval_minutes, CASE repeat_name "
                "WHEN 'daily' THEN 1440 "
                "WHEN 'weekly' THEN 10080 "
                "WHEN 'monthly' THEN 43200 "
                "ELSE 1440 END, 1440)"
            )
        elif "repeat_interval_minutes" in names:
            interval_expr = "COALESCE(repeat_interval_minutes, 1440)"
        elif "repeat_name" in names:
            interval_expr = (
                "CASE repeat_name "
                "WHEN 'daily' THEN 1440 "
                "WHEN 'weekly' THEN 10080 "
                "WHEN 'monthly' THEN 43200 "
                "ELSE 1440 END"
            )

        enabled_expr = "1"
        if "enabled" in names:
            enabled_expr = "COALESCE(enabled, 1)"
        remind_start_expr = "NULL"
        if "remind_start_time" in names:
            remind_start_expr = "remind_start_time"

        conn.execute("ALTER TABLE recurring_schedules RENAME TO recurring_schedules_old")
        conn.execute(
            """
            CREATE TABLE recurring_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                schedule_id INTEGER NOT NULL UNIQUE,
                start_time TEXT NOT NULL,
                repeat_interval_minutes INTEGER NOT NULL CHECK (repeat_interval_minutes >= 1),
                repeat_times INTEGER NOT NULL CHECK (repeat_times = -1 OR repeat_times >= 2),
                remind_start_time TEXT,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                created_at TEXT NOT NULL,
                FOREIGN KEY(schedule_id) REFERENCES schedules(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO recurring_schedules (
                id, schedule_id, start_time, repeat_interval_minutes, repeat_times,
                remind_start_time, enabled, created_at
            )
            SELECT
                id,
                schedule_id,
                start_time,
                {interval_expr},
                CASE
                    WHEN repeat_times = -1 THEN -1
                    WHEN repeat_times IS NULL OR repeat_times < 2 THEN 2
                    ELSE repeat_times
                END,
                {remind_start_expr},
                {enabled_expr},
                created_at
            FROM recurring_schedules_old
            """
        )
        conn.execute("DROP TABLE recurring_schedules_old")

    def _ensure_chat_history_turn_schema(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(chat_history)").fetchall()
        names = {row["name"] for row in columns}
        required = {"id", "user_content", "assistant_content", "created_at"}
        if required.issubset(names):
            return
        if {"id", "role", "content", "created_at"}.issubset(names):
            rows = conn.execute(
                """
                SELECT role, content, created_at
                FROM chat_history
                ORDER BY id ASC
                """
            ).fetchall()
            conn.execute("ALTER TABLE chat_history RENAME TO chat_history_old")
            conn.execute(
                """
                CREATE TABLE chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_content TEXT NOT NULL,
                    assistant_content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            pending_user: str | None = None
            pending_created_at: str | None = None
            for row in rows:
                role = str(row["role"] or "").strip().lower()
                content = str(row["content"] or "")
                created_at = str(row["created_at"] or _now_iso())
                if role == "user":
                    if pending_user is not None:
                        conn.execute(
                            "INSERT INTO chat_history (user_content, assistant_content, created_at) VALUES (?, ?, ?)",
                            (pending_user, "", pending_created_at or created_at),
                        )
                    pending_user = content
                    pending_created_at = created_at
                    continue
                if role == "assistant":
                    if pending_user is not None:
                        conn.execute(
                            "INSERT INTO chat_history (user_content, assistant_content, created_at) VALUES (?, ?, ?)",
                            (pending_user, content, pending_created_at or created_at),
                        )
                        pending_user = None
                        pending_created_at = None
                    else:
                        conn.execute(
                            "INSERT INTO chat_history (user_content, assistant_content, created_at) VALUES (?, ?, ?)",
                            ("", content, created_at),
                        )
                    continue
                conn.execute(
                    "INSERT INTO chat_history (user_content, assistant_content, created_at) VALUES (?, ?, ?)",
                    ("", content, created_at),
                )
            if pending_user is not None:
                conn.execute(
                    "INSERT INTO chat_history (user_content, assistant_content, created_at) VALUES (?, ?, ?)",
                    (pending_user, "", pending_created_at or _now_iso()),
                )
            conn.execute("DROP TABLE chat_history_old")
            return
        raise RuntimeError("chat_history schema is not supported")

    def add_schedule(
        self,
        title: str,
        event_time: str,
        duration_minutes: int = 60,
        remind_at: str | None = None,
        tag: str = "default",
    ) -> int:
        try:
            payload = ScheduleCreateInput.model_validate(
                {
                    "title": title,
                    "event_time": event_time,
                    "duration_minutes": duration_minutes,
                    "remind_at": remind_at,
                    "tag": tag,
                }
            )
        except ValidationError as exc:
            self._log_input_validation_failed(method="add_schedule", exc=exc)
            raise ValueError(str(exc)) from exc

        timestamp = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO schedules (title, tag, event_time, duration_minutes, remind_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    payload.title,
                    payload.tag,
                    payload.event_time,
                    payload.duration_minutes,
                    payload.remind_at,
                    timestamp,
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError("failed to insert schedule")
            return int(cur.lastrowid)

    def add_schedules(
        self,
        title: str,
        event_times: list[str],
        duration_minutes: int = 60,
        remind_at: str | None = None,
        tag: str = "default",
    ) -> list[int]:
        if not event_times:
            return []
        try:
            payload = ScheduleBatchCreateInput.model_validate(
                {
                    "title": title,
                    "event_times": event_times,
                    "duration_minutes": duration_minutes,
                    "remind_at": remind_at,
                    "tag": tag,
                }
            )
        except ValidationError as exc:
            self._log_input_validation_failed(method="add_schedules", exc=exc)
            raise ValueError(str(exc)) from exc

        timestamp = _now_iso()
        created_ids: list[int] = []
        with self._connect() as conn:
            for event_time in payload.event_times:
                cur = conn.execute(
                    "INSERT INTO schedules (title, tag, event_time, duration_minutes, remind_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        payload.title,
                        payload.tag,
                        event_time,
                        payload.duration_minutes,
                        payload.remind_at,
                        timestamp,
                    ),
                )
                if cur.lastrowid is None:
                    raise RuntimeError("failed to insert schedule")
                created_ids.append(int(cur.lastrowid))
        return created_ids

    def set_schedule_recurrence(
        self,
        schedule_id: int,
        *,
        start_time: str,
        repeat_interval_minutes: int,
        repeat_times: int,
        remind_start_time: str | None = None,
        enabled: bool = True,
    ) -> bool:
        if isinstance(repeat_times, int) and not isinstance(repeat_times, bool) and repeat_times == 1:
            return self.clear_schedule_recurrence(schedule_id)
        try:
            payload = ScheduleRecurrenceInput.model_validate(
                {
                    "schedule_id": schedule_id,
                    "start_time": start_time,
                    "repeat_interval_minutes": repeat_interval_minutes,
                    "repeat_times": repeat_times,
                    "remind_start_time": remind_start_time,
                    "enabled": enabled,
                }
            )
        except ValidationError as exc:
            self._log_input_validation_failed(method="set_schedule_recurrence", exc=exc)
            return False

        timestamp = _now_iso()
        with self._connect() as conn:
            has_schedule = (
                conn.execute("SELECT 1 FROM schedules WHERE id = ?", (payload.schedule_id,)).fetchone()
                is not None
            )
            if not has_schedule:
                return False
            conn.execute(
                """
                INSERT INTO recurring_schedules (
                    schedule_id, start_time, repeat_interval_minutes, repeat_times,
                    remind_start_time, enabled, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(schedule_id) DO UPDATE SET
                    start_time = excluded.start_time,
                    repeat_interval_minutes = excluded.repeat_interval_minutes,
                    repeat_times = excluded.repeat_times,
                    remind_start_time = excluded.remind_start_time,
                    enabled = excluded.enabled
                """,
                (
                    payload.schedule_id,
                    payload.start_time,
                    payload.repeat_interval_minutes,
                    payload.repeat_times,
                    payload.remind_start_time,
                    1 if payload.enabled else 0,
                    timestamp,
                ),
            )
        return True

    def clear_schedule_recurrence(self, schedule_id: int) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM recurring_schedules WHERE schedule_id = ?", (schedule_id,))
        return True

    def set_schedule_recurrence_enabled(self, schedule_id: int, enabled: bool) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE recurring_schedules SET enabled = ? WHERE schedule_id = ?",
                (1 if enabled else 0, schedule_id),
            )
            return cur.rowcount > 0

    def list_schedules(
        self,
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        max_window_days: int = 31,
        tag: str | None = None,
    ) -> list[ScheduleItem]:
        effective_window_start, effective_window_end = _normalize_schedule_window(
            window_start=window_start,
            window_end=window_end,
            max_window_days=max_window_days,
        )
        if (
            effective_window_start is not None
            and effective_window_end is not None
            and effective_window_end < effective_window_start
        ):
            return []

        with self._connect() as conn:
            base_items = self._list_base_schedules(conn)
            rules = self._list_recurring_rules(conn)

        normalized_tag = _normalize_tag(tag) if tag is not None else None
        if normalized_tag is None:
            filtered_base_items = base_items
        else:
            filtered_base_items = [item for item in base_items if item.tag == normalized_tag]

        rule_by_schedule_id = {rule.schedule_id: rule for rule in rules}
        base_map = {item.id: item for item in filtered_base_items}
        combined: list[ScheduleItem] = []
        for base in filtered_base_items:
            base_with_rule = _attach_recurrence_to_schedule(base, rule_by_schedule_id.get(base.id))
            if _is_schedule_item_in_window(
                base_with_rule,
                window_start=effective_window_start,
                window_end=effective_window_end,
            ):
                combined.append(base_with_rule)
        for rule in rules:
            base_item = base_map.get(rule.schedule_id)
            if base_item is None:
                continue
            if not rule.enabled:
                continue
            expand_window_start = effective_window_start
            expand_window_end = effective_window_end
            expand_max_items: int | None = None
            if rule.repeat_times == -1 and expand_window_start is None and expand_window_end is None:
                now = datetime.now()
                expand_window_start = now
                expand_window_end = now + timedelta(days=max_window_days)
                expand_max_items = 2000

            combined.extend(
                _expand_recurring_schedule_items(
                    base=base_item,
                    rule=rule,
                    window_start=expand_window_start,
                    window_end=expand_window_end,
                    max_items=expand_max_items,
                )
            )

        combined.sort(key=lambda item: (item.event_time, item.id))
        return combined

    def get_schedule(self, schedule_id: int) -> ScheduleItem | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, tag, event_time, duration_minutes, remind_at, created_at "
                "FROM schedules WHERE id = ?",
                (schedule_id,),
            ).fetchone()
            rule_row = conn.execute(
                """
                SELECT id, schedule_id, start_time, repeat_interval_minutes, repeat_times,
                       remind_start_time, enabled, created_at
                FROM recurring_schedules
                WHERE schedule_id = ?
                """,
                (schedule_id,),
            ).fetchone()
        if row is None:
            return None
        rule: RecurringScheduleRule | None = None
        if rule_row is not None:
            rule = _recurring_schedule_rule_from_row(rule_row)
        return _attach_recurrence_to_schedule(_schedule_item_from_row(row), rule)

    def update_schedule(
        self,
        schedule_id: int,
        *,
        title: str,
        event_time: str,
        tag: str | object = _UNSET,
        duration_minutes: int | object = _UNSET,
        remind_at: str | None | object = _UNSET,
        repeat_remind_start_time: str | None | object = _UNSET,
    ) -> bool:
        raw_payload: dict[str, object] = {
            "schedule_id": schedule_id,
            "title": title,
            "event_time": event_time,
        }
        if tag is not _UNSET:
            raw_payload["tag"] = tag
        if duration_minutes is not _UNSET:
            raw_payload["duration_minutes"] = duration_minutes
        if remind_at is not _UNSET:
            raw_payload["remind_at"] = remind_at
        if repeat_remind_start_time is not _UNSET:
            raw_payload["repeat_remind_start_time"] = repeat_remind_start_time

        try:
            payload = ScheduleUpdateInput.model_validate(raw_payload)
        except ValidationError as exc:
            self._log_input_validation_failed(method="update_schedule", exc=exc)
            return False

        fields = ["title = ?", "event_time = ?"]
        values: list[object] = [payload.title, payload.event_time]

        if "tag" in payload.model_fields_set:
            fields.append("tag = ?")
            values.append(payload.tag)
        if "duration_minutes" in payload.model_fields_set:
            fields.append("duration_minutes = ?")
            values.append(payload.duration_minutes)
        if "remind_at" in payload.model_fields_set:
            fields.append("remind_at = ?")
            values.append(payload.remind_at)

        values.append(payload.schedule_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE schedules SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            if cur.rowcount <= 0:
                return False
            conn.execute(
                "UPDATE recurring_schedules SET start_time = ? WHERE schedule_id = ?",
                (payload.event_time, payload.schedule_id),
            )
            if "repeat_remind_start_time" in payload.model_fields_set:
                conn.execute(
                    "UPDATE recurring_schedules SET remind_start_time = ? WHERE schedule_id = ?",
                    (payload.repeat_remind_start_time, payload.schedule_id),
                )
            return True

    def delete_schedule(self, schedule_id: int) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM recurring_schedules WHERE schedule_id = ?", (schedule_id,))
            cur = conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            return cur.rowcount > 0

    def list_base_schedules_in_window(
        self,
        *,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        max_window_days: int = 31,
    ) -> list[ScheduleItem]:
        effective_window_start, effective_window_end = _normalize_schedule_window(
            window_start=window_start,
            window_end=window_end,
            max_window_days=max_window_days,
        )
        if (
            effective_window_start is not None
            and effective_window_end is not None
            and effective_window_end < effective_window_start
        ):
            return []
        with self._connect() as conn:
            base_items = self._list_base_schedules(conn)
        return [
            item
            for item in base_items
            if _is_schedule_item_in_window(
                item,
                window_start=effective_window_start,
                window_end=effective_window_end,
            )
        ]

    def add_thought(self, content: str, status: str = THOUGHT_STATUS_TODO) -> int:
        try:
            payload = ThoughtCreateInput.model_validate({"content": content, "status": status})
        except ValidationError as exc:
            self._log_input_validation_failed(method="add_thought", exc=exc)
            raise ValueError(str(exc)) from exc

        timestamp = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO thoughts (content, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (payload.content, payload.status, timestamp, timestamp),
            )
            if cur.lastrowid is None:
                raise RuntimeError("failed to insert thought")
            return int(cur.lastrowid)

    def list_thoughts(self, *, status: str | None = None) -> list[ThoughtItem]:
        query = (
            "SELECT id, content, status, created_at, updated_at "
            "FROM thoughts WHERE status IN (?, ?) ORDER BY id ASC"
        )
        params: tuple[object, ...] = (THOUGHT_STATUS_TODO, THOUGHT_STATUS_DONE)
        if status is not None:
            normalized_status = _normalize_thought_status(status)
            query = "SELECT id, content, status, created_at, updated_at FROM thoughts WHERE status = ? ORDER BY id ASC"
            params = (normalized_status,)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_thought_item_from_row(row) for row in rows]

    def get_thought(self, thought_id: int) -> ThoughtItem | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, content, status, created_at, updated_at FROM thoughts WHERE id = ?",
                (thought_id,),
            ).fetchone()
        if row is None:
            return None
        return _thought_item_from_row(row)

    def update_thought(
        self,
        thought_id: int,
        *,
        content: str,
        status: str | object = _UNSET,
    ) -> bool:
        raw_payload: dict[str, object] = {"content": content}
        if status is not _UNSET:
            raw_payload["status"] = status

        try:
            payload = ThoughtUpdateInput.model_validate(raw_payload)
        except ValidationError as exc:
            self._log_input_validation_failed(method="update_thought", exc=exc)
            raise ValueError(str(exc)) from exc

        fields = ["content = ?"]
        values: list[object] = [payload.content]
        if "status" in payload.model_fields_set:
            fields.append("status = ?")
            values.append(payload.status)
        fields.append("updated_at = ?")
        values.append(_now_iso())
        values.append(thought_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE thoughts SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            return cur.rowcount > 0

    def _log_input_validation_failed(self, *, method: str, exc: ValidationError) -> None:
        issue = first_validation_issue(exc)
        field_name = issue.field or "unknown"
        reason = f"{field_name}: {issue.message}"
        self._logger.warning(
            "db input validation failed",
            extra={
                "event": "db_input_validation_failed",
                "context": {
                    "method": method,
                    "code": issue.code,
                    "field": issue.field,
                    "message": issue.message,
                    "reason": reason,
                },
            },
        )

    def soft_delete_thought(self, thought_id: int) -> bool:
        timestamp = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE thoughts SET status = ?, updated_at = ? WHERE id = ?",
                (THOUGHT_STATUS_DELETED, timestamp, thought_id),
            )
            return cur.rowcount > 0

    def _list_base_schedules(self, conn: sqlite3.Connection) -> list[ScheduleItem]:
        rows = conn.execute(
            "SELECT id, title, tag, event_time, duration_minutes, remind_at, created_at "
            "FROM schedules ORDER BY event_time ASC, id ASC"
        ).fetchall()
        return [_schedule_item_from_row(row) for row in rows]

    def _list_recurring_rules(self, conn: sqlite3.Connection) -> list[RecurringScheduleRule]:
        rows = conn.execute(
            """
            SELECT id, schedule_id, start_time, repeat_interval_minutes, repeat_times,
                   remind_start_time, enabled, created_at
            FROM recurring_schedules
            ORDER BY id ASC
            """
        ).fetchall()
        return [_recurring_schedule_rule_from_row(row) for row in rows]

    def list_base_schedules(self) -> list[ScheduleItem]:
        with self._connect() as conn:
            return self._list_base_schedules(conn)

    def list_recurring_rules(self) -> list[RecurringScheduleRule]:
        with self._connect() as conn:
            return self._list_recurring_rules(conn)

    def add_scheduled_planner_task(
        self,
        *,
        task_name: str,
        cron_expr: str,
        prompt: str,
        run_limit: int = -1,
        next_run_at: str | None = None,
    ) -> int:
        try:
            payload = ScheduledPlannerTaskCreateInput.model_validate(
                {
                    "task_name": task_name,
                    "run_limit": run_limit,
                    "cron_expr": cron_expr,
                    "prompt": prompt,
                    "next_run_at": next_run_at,
                }
            )
        except ValidationError as exc:
            self._log_input_validation_failed(method="add_scheduled_planner_task", exc=exc)
            raise ValueError(str(exc)) from exc

        timestamp = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                INSERT INTO {TIMER_TASKS_TABLE} (
                    task_name, run_limit, cron_expr, prompt, next_run_at, last_run_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.task_name,
                    payload.run_limit,
                    payload.cron_expr,
                    payload.prompt,
                    payload.next_run_at,
                    None,
                    timestamp,
                    timestamp,
                ),
            )
            if cur.lastrowid is None:
                raise RuntimeError("failed to insert scheduled planner task")
            return int(cur.lastrowid)

    def list_scheduled_planner_tasks(self) -> list[ScheduledPlannerTask]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, task_name, run_limit, cron_expr, prompt, next_run_at, last_run_at, created_at, updated_at
                FROM {TIMER_TASKS_TABLE}
                ORDER BY id ASC
                """
            ).fetchall()
        return [_scheduled_planner_task_from_row(row) for row in rows]

    def get_scheduled_planner_task(self, task_id: int) -> ScheduledPlannerTask | None:
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT id, task_name, run_limit, cron_expr, prompt, next_run_at, last_run_at, created_at, updated_at
                FROM {TIMER_TASKS_TABLE}
                WHERE id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return _scheduled_planner_task_from_row(row)

    def list_uninitialized_scheduled_planner_tasks(self) -> list[ScheduledPlannerTask]:
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, task_name, run_limit, cron_expr, prompt, next_run_at, last_run_at, created_at, updated_at
                FROM {TIMER_TASKS_TABLE}
                WHERE run_limit != 0 AND next_run_at IS NULL
                ORDER BY id ASC
                """
            ).fetchall()
        return [_scheduled_planner_task_from_row(row) for row in rows]

    def list_due_scheduled_planner_tasks(
        self,
        *,
        now: datetime,
        limit: int | None = None,
    ) -> list[ScheduledPlannerTask]:
        normalized_now = now.replace(microsecond=0).isoformat(sep=" ")
        sql = (
            f"""
            SELECT id, task_name, run_limit, cron_expr, prompt, next_run_at, last_run_at, created_at, updated_at
            FROM {TIMER_TASKS_TABLE}
            WHERE run_limit != 0
              AND next_run_at IS NOT NULL
              AND next_run_at <= ?
            ORDER BY next_run_at ASC, id ASC
            """
        )
        params: tuple[object, ...]
        if limit is None:
            params = (normalized_now,)
        else:
            sql += "\nLIMIT ?"
            params = (normalized_now, max(limit, 1))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_scheduled_planner_task_from_row(row) for row in rows]

    def initialize_scheduled_planner_task_next_run(
        self,
        task_id: int,
        *,
        next_run_at: str,
        updated_at: str | None = None,
    ) -> bool:
        normalized_updated_at = updated_at or _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE {TIMER_TASKS_TABLE}
                SET next_run_at = ?, updated_at = ?
                WHERE id = ? AND run_limit != 0 AND next_run_at IS NULL
                """,
                (next_run_at, normalized_updated_at, task_id),
            )
            return cur.rowcount > 0

    def update_scheduled_planner_task(
        self,
        task_id: int,
        *,
        task_name: str,
        cron_expr: str,
        prompt: str,
        run_limit: int,
        next_run_at: str | None,
    ) -> bool:
        try:
            payload = ScheduledPlannerTaskUpdateInput.model_validate(
                {
                    "task_name": task_name,
                    "run_limit": run_limit,
                    "cron_expr": cron_expr,
                    "prompt": prompt,
                    "next_run_at": next_run_at,
                }
            )
        except ValidationError as exc:
            self._log_input_validation_failed(method="update_scheduled_planner_task", exc=exc)
            raise ValueError(str(exc)) from exc

        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE {TIMER_TASKS_TABLE}
                SET
                    task_name = ?,
                    run_limit = ?,
                    cron_expr = ?,
                    prompt = ?,
                    next_run_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.task_name,
                    payload.run_limit,
                    payload.cron_expr,
                    payload.prompt,
                    payload.next_run_at,
                    _now_iso(),
                    task_id,
                ),
            )
            return cur.rowcount > 0

    def delete_scheduled_planner_task(self, task_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                f"DELETE FROM {TIMER_TASKS_TABLE} WHERE id = ?",
                (task_id,),
            )
            return cur.rowcount > 0

    def mark_scheduled_planner_task_started(
        self,
        task_id: int,
        *,
        expected_next_run_at: str,
        started_at: str,
        next_run_at: str,
        updated_at: str | None = None,
    ) -> bool:
        normalized_updated_at = updated_at or started_at
        with self._connect() as conn:
            cur = conn.execute(
                f"""
                UPDATE {TIMER_TASKS_TABLE}
                SET
                    last_run_at = ?,
                    next_run_at = ?,
                    run_limit = CASE
                        WHEN run_limit = -1 THEN -1
                        ELSE run_limit - 1
                    END,
                    updated_at = ?
                WHERE id = ? AND run_limit != 0 AND next_run_at = ?
                """,
                (
                    started_at,
                    next_run_at,
                    normalized_updated_at,
                    task_id,
                    expected_next_run_at,
                ),
            )
            return cur.rowcount > 0

    def has_reminder_delivery(self, reminder_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM reminder_deliveries WHERE reminder_key = ?",
                (reminder_key,),
            ).fetchone()
        return row is not None

    def save_reminder_delivery(
        self,
        *,
        reminder_key: str,
        source_type: str,
        source_id: int,
        occurrence_time: str | None,
        remind_time: str,
        payload: str | None = None,
    ) -> bool:
        delivered_at = _now_iso()
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO reminder_deliveries (
                        reminder_key, source_type, source_id, occurrence_time,
                        remind_time, delivered_at, payload
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reminder_key,
                        source_type,
                        source_id,
                        occurrence_time,
                        remind_time,
                        delivered_at,
                        payload,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def list_reminder_deliveries(self) -> list[ReminderDelivery]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT reminder_key, source_type, source_id, occurrence_time,
                       remind_time, delivered_at, payload
                FROM reminder_deliveries
                ORDER BY id ASC
                """
            ).fetchall()
        return [_reminder_delivery_from_row(row) for row in rows]

    def save_message(self, role: str, content: str) -> None:
        normalized_role = role.strip().lower()
        if normalized_role == "user":
            self.save_turn(user_content=content, assistant_content="")
            return
        if normalized_role == "assistant":
            timestamp = _now_iso()
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT id
                    FROM chat_history
                    WHERE assistant_content = ''
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                if row is not None:
                    conn.execute(
                        "UPDATE chat_history SET assistant_content = ? WHERE id = ?",
                        (content, int(row["id"])),
                    )
                    return
                conn.execute(
                    "INSERT INTO chat_history (user_content, assistant_content, created_at) VALUES (?, ?, ?)",
                    ("", content, timestamp),
                )
            return
        self.save_turn(user_content="", assistant_content=content)

    def save_turn(self, *, user_content: str, assistant_content: str) -> None:
        timestamp = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chat_history (user_content, assistant_content, created_at) VALUES (?, ?, ?)",
                (user_content, assistant_content, timestamp),
            )

    def recent_turns(self, limit: int = 8) -> list[ChatTurn]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_content, assistant_content, created_at
                FROM chat_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        # Reverse to keep chronological order for display.
        rows.reverse()
        return [_chat_turn_from_row(row) for row in rows]

    def recent_turns_for_planner(self, *, lookback_hours: int = 24, limit: int = 50) -> list[ChatTurn]:
        normalized_hours = max(lookback_hours, 1)
        normalized_limit = max(limit, 1)
        since = (datetime.now().replace(microsecond=0) - timedelta(hours=normalized_hours)).isoformat(sep=" ")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_content, assistant_content, created_at
                FROM chat_history
                WHERE created_at >= ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (since, normalized_limit),
            ).fetchall()
        rows.reverse()
        return [_chat_turn_from_row(row) for row in rows]

    def recent_turns_since(self, *, since: datetime, limit: int = 10000) -> list[ChatTurn]:
        normalized_limit = max(limit, 1)
        normalized_since = since.replace(microsecond=0).isoformat(sep=" ")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_content, assistant_content, created_at
                FROM chat_history
                WHERE created_at >= ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (normalized_since, normalized_limit),
            ).fetchall()
        rows.reverse()
        return [_chat_turn_from_row(row) for row in rows]

    def search_turns(self, keyword: str, *, limit: int = 20) -> list[ChatTurn]:
        text = keyword.strip()
        if not text:
            return []
        normalized_limit = max(limit, 1)
        like_pattern = f"%{text}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_content, assistant_content, created_at
                FROM chat_history
                WHERE user_content LIKE ? OR assistant_content LIKE ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (like_pattern, like_pattern, normalized_limit),
            ).fetchall()
        rows.reverse()
        return [_chat_turn_from_row(row) for row in rows]

    def recent_messages(self, limit: int = 8) -> list[ChatMessage]:
        turns = self.recent_turns(limit=max(limit, 1))
        messages: list[ChatMessage] = []
        for item in turns:
            if item.user_content:
                messages.append(ChatMessage(role="user", content=item.user_content))
            if item.assistant_content:
                messages.append(ChatMessage(role="assistant", content=item.assistant_content))
        if len(messages) <= limit:
            return messages
        return messages[-limit:]


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _normalize_tag(tag: str | None) -> str:
    return NormalizedTagValue.model_validate({"tag": tag}).tag


def _normalize_duration_minutes(duration_minutes: int) -> int:
    return ScheduleDurationValue.model_validate({"duration_minutes": duration_minutes}).duration_minutes


def _normalize_thought_content(content: str) -> str:
    return ThoughtContentValue.model_validate({"content": content}).content


def _normalize_thought_status(status: str) -> str:
    return ThoughtStatusValue.model_validate({"status": status}).status


def _schedule_item_from_row(row: sqlite3.Row) -> ScheduleItem:
    payload = dict(row)
    payload["tag"] = _normalize_tag(str(payload.get("tag")) if payload.get("tag") is not None else None)
    payload["duration_minutes"] = int(payload.get("duration_minutes") or 60)
    payload["remind_at"] = str(payload["remind_at"]) if payload.get("remind_at") else None
    payload["title"] = str(payload.get("title") or "")
    payload["event_time"] = str(payload.get("event_time") or "")
    payload["created_at"] = str(payload.get("created_at") or "")
    return ScheduleItem.model_validate(payload)


def _recurring_schedule_rule_from_row(row: sqlite3.Row) -> RecurringScheduleRule:
    payload = dict(row)
    payload["enabled"] = bool(payload["enabled"]) if payload.get("enabled") is not None else True
    payload["remind_start_time"] = str(payload["remind_start_time"]) if payload.get("remind_start_time") else None
    payload["start_time"] = str(payload.get("start_time") or "")
    payload["created_at"] = str(payload.get("created_at") or "")
    return RecurringScheduleRule.model_validate(payload)


def _thought_item_from_row(row: sqlite3.Row) -> ThoughtItem:
    payload = dict(row)
    payload["content"] = str(payload.get("content") or "")
    payload["status"] = str(payload.get("status") or THOUGHT_STATUS_TODO)
    payload["created_at"] = str(payload.get("created_at") or "")
    payload["updated_at"] = str(payload.get("updated_at") or "")
    return ThoughtItem.model_validate(payload)


def _reminder_delivery_from_row(row: sqlite3.Row) -> ReminderDelivery:
    payload = dict(row)
    payload["occurrence_time"] = str(payload["occurrence_time"]) if payload.get("occurrence_time") else None
    payload["remind_time"] = str(payload.get("remind_time") or "")
    payload["delivered_at"] = str(payload.get("delivered_at") or "")
    payload["payload"] = str(payload["payload"]) if payload.get("payload") is not None else None
    return ReminderDelivery.model_validate(payload)


def _chat_turn_from_row(row: sqlite3.Row) -> ChatTurn:
    payload = dict(row)
    payload["user_content"] = str(payload.get("user_content") or "")
    payload["assistant_content"] = str(payload.get("assistant_content") or "")
    payload["created_at"] = str(payload.get("created_at") or "")
    return ChatTurn.model_validate(payload)


def _scheduled_planner_task_from_row(row: sqlite3.Row) -> ScheduledPlannerTask:
    payload = dict(row)
    payload["task_name"] = str(payload.get("task_name") or "")
    payload["run_limit"] = int(payload.get("run_limit") if payload.get("run_limit") is not None else -1)
    payload["cron_expr"] = str(payload.get("cron_expr") or "")
    payload["prompt"] = str(payload.get("prompt") or "")
    payload["next_run_at"] = str(payload["next_run_at"]) if payload.get("next_run_at") else None
    payload["last_run_at"] = str(payload["last_run_at"]) if payload.get("last_run_at") else None
    payload["created_at"] = str(payload.get("created_at") or "")
    payload["updated_at"] = str(payload.get("updated_at") or "")
    return ScheduledPlannerTask.model_validate(payload)


def _normalize_schedule_window(
    *,
    window_start: datetime | None,
    window_end: datetime | None,
    max_window_days: int,
) -> tuple[datetime | None, datetime | None]:
    normalized_max_days = max(max_window_days, 1)
    max_delta = timedelta(days=normalized_max_days)

    start = window_start
    end = window_end
    if start is not None and end is None:
        end = start + max_delta
    elif start is None and end is not None:
        start = end - max_delta
    elif start is not None and end is not None and end - start > max_delta:
        end = start + max_delta
    return start, end


def _is_schedule_item_in_window(
    item: ScheduleItem,
    *,
    window_start: datetime | None,
    window_end: datetime | None,
) -> bool:
    if window_start is None and window_end is None:
        return True
    try:
        event_time = datetime.strptime(item.event_time, "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    if window_start is not None and event_time < window_start:
        return False
    if window_end is not None and event_time > window_end:
        return False
    return True


def _expand_recurring_schedule_items(
    *,
    base: ScheduleItem,
    rule: RecurringScheduleRule,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    max_items: int | None = None,
) -> list[ScheduleItem]:
    all_times = _build_repeated_event_times(
        start_time=rule.start_time,
        repeat_interval_minutes=rule.repeat_interval_minutes,
        repeat_times=rule.repeat_times,
        window_start=window_start,
        window_end=window_end,
        max_items=max_items,
    )
    expanded_times = all_times
    if expanded_times and expanded_times[0] == base.event_time:
        expanded_times = expanded_times[1:]
    if not expanded_times:
        return []
    return [
        ScheduleItem.model_validate(
            {
                **base.model_dump(),
                "event_time": event_time,
                "repeat_interval_minutes": rule.repeat_interval_minutes,
                "repeat_times": rule.repeat_times,
                "repeat_enabled": rule.enabled,
                "repeat_remind_start_time": rule.remind_start_time,
            }
        )
        for event_time in expanded_times
    ]


def _attach_recurrence_to_schedule(base: ScheduleItem, rule: RecurringScheduleRule | None) -> ScheduleItem:
    if rule is None:
        return base
    return ScheduleItem.model_validate(
        {
            **base.model_dump(),
            "repeat_interval_minutes": rule.repeat_interval_minutes,
            "repeat_times": rule.repeat_times,
            "repeat_enabled": rule.enabled,
            "repeat_remind_start_time": rule.remind_start_time,
        }
    )


def _build_repeated_event_times(
    *,
    start_time: str,
    repeat_interval_minutes: int,
    repeat_times: int,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    max_items: int | None = None,
) -> list[str]:
    if repeat_interval_minutes < 1:
        return [start_time]
    if repeat_times == 1:
        return [start_time]

    try:
        start = datetime.strptime(start_time, "%Y-%m-%d %H:%M")
    except ValueError:
        return [start_time]

    if repeat_times != -1 and repeat_times < 2:
        return [start_time]

    current = start
    occurrence_index = 0
    if window_start is not None and current < window_start:
        delta_minutes = int((window_start - current).total_seconds() // 60)
        skip_steps = max(delta_minutes // repeat_interval_minutes, 0)
        occurrence_index += skip_steps
        current += timedelta(minutes=skip_steps * repeat_interval_minutes)
        while current < window_start:
            occurrence_index += 1
            current += timedelta(minutes=repeat_interval_minutes)

    result: list[str] = []
    while True:
        if repeat_times != -1 and occurrence_index >= repeat_times:
            break
        if window_end is not None and current > window_end:
            break
        result.append(current.strftime("%Y-%m-%d %H:%M"))
        if max_items is not None and len(result) >= max_items:
            break
        occurrence_index += 1
        current += timedelta(minutes=repeat_interval_minutes)
    return result
