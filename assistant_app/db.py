from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

_UNSET = object()


@dataclass(frozen=True)
class TodoItem:
    id: int
    content: str
    tag: str
    priority: int
    done: bool
    created_at: str
    completed_at: str | None
    due_at: str | None
    remind_at: str | None


@dataclass(frozen=True)
class ScheduleItem:
    id: int
    title: str
    event_time: str
    duration_minutes: int
    created_at: str
    remind_at: str | None = None
    repeat_interval_minutes: int | None = None
    repeat_times: int | None = None
    repeat_enabled: bool | None = None
    repeat_remind_start_time: str | None = None


@dataclass(frozen=True)
class RecurringScheduleRule:
    id: int
    schedule_id: int
    start_time: str
    repeat_interval_minutes: int
    repeat_times: int
    remind_start_time: str | None
    enabled: bool
    created_at: str


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ChatTurn:
    user_content: str
    assistant_content: str
    created_at: str


@dataclass(frozen=True)
class ReminderDelivery:
    reminder_key: str
    source_type: str
    source_id: int
    occurrence_time: str | None
    remind_time: str
    delivered_at: str
    payload: str | None


class AssistantDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
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
                CREATE TABLE IF NOT EXISTS todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    tag TEXT NOT NULL DEFAULT 'default',
                    priority INTEGER NOT NULL DEFAULT 0 CHECK (priority >= 0),
                    done INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    due_at TEXT,
                    remind_at TEXT
                )
                """
            )
            self._ensure_todo_tag_column(conn)
            self._ensure_todo_extra_columns(conn)
            self._ensure_todo_priority_column(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL DEFAULT 60 CHECK (duration_minutes >= 1),
                    remind_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
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

    def _ensure_todo_tag_column(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(todos)").fetchall()
        has_tag = any(row["name"] == "tag" for row in columns)
        if not has_tag:
            conn.execute("ALTER TABLE todos ADD COLUMN tag TEXT NOT NULL DEFAULT 'default'")

    def _ensure_todo_extra_columns(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(todos)").fetchall()
        names = {row["name"] for row in columns}
        if "completed_at" not in names:
            conn.execute("ALTER TABLE todos ADD COLUMN completed_at TEXT")
        if "due_at" not in names:
            conn.execute("ALTER TABLE todos ADD COLUMN due_at TEXT")
        if "remind_at" not in names:
            conn.execute("ALTER TABLE todos ADD COLUMN remind_at TEXT")

    def _ensure_todo_priority_column(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(todos)").fetchall()
        names = {row["name"] for row in columns}
        if "priority" not in names:
            conn.execute("ALTER TABLE todos ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE todos SET priority = 0 WHERE priority IS NULL OR priority < 0")

    def _ensure_schedule_duration_column(self, conn: sqlite3.Connection) -> None:
        columns = conn.execute("PRAGMA table_info(schedules)").fetchall()
        names = {row["name"] for row in columns}
        if "duration_minutes" not in names:
            conn.execute("ALTER TABLE schedules ADD COLUMN duration_minutes INTEGER NOT NULL DEFAULT 60")
        conn.execute(
            "UPDATE schedules SET duration_minutes = 60 "
            "WHERE duration_minutes IS NULL OR duration_minutes < 1"
        )

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

    def add_todo(
        self,
        content: str,
        tag: str = "default",
        priority: int = 0,
        due_at: str | None = None,
        remind_at: str | None = None,
    ) -> int:
        if remind_at and not due_at:
            raise ValueError("remind_at requires due_at")
        timestamp = _now_iso()
        normalized_tag = _normalize_tag(tag)
        normalized_priority = _normalize_priority(priority)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO todos (content, tag, priority, done, created_at, completed_at, due_at, remind_at)
                VALUES (?, ?, ?, 0, ?, NULL, ?, ?)
                """,
                (content, normalized_tag, normalized_priority, timestamp, due_at, remind_at),
            )
            if cur.lastrowid is None:
                raise RuntimeError("failed to insert todo")
            return int(cur.lastrowid)

    def list_todos(self, tag: str | None = None) -> list[TodoItem]:
        normalized_tag = _normalize_tag(tag) if tag is not None else None
        with self._connect() as conn:
            if normalized_tag is None:
                rows = conn.execute(
                    """
                    SELECT id, content, tag, priority, done, created_at, completed_at, due_at, remind_at
                    FROM todos
                    ORDER BY priority ASC, id ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, content, tag, priority, done, created_at, completed_at, due_at, remind_at
                    FROM todos
                    WHERE tag = ?
                    ORDER BY priority ASC, id ASC
                    """,
                    (normalized_tag,),
                ).fetchall()
        return [
            TodoItem(
                id=row["id"],
                content=row["content"],
                tag=row["tag"] or "default",
                priority=_normalize_priority(row["priority"] if row["priority"] is not None else 0),
                done=bool(row["done"]),
                created_at=row["created_at"],
                completed_at=row["completed_at"],
                due_at=row["due_at"],
                remind_at=row["remind_at"],
            )
            for row in rows
        ]

    def search_todos(self, keyword: str, *, tag: str | None = None) -> list[TodoItem]:
        text = keyword.strip()
        if not text:
            return []

        normalized_tag = _normalize_tag(tag) if tag is not None else None
        like_pattern = f"%{text}%"
        with self._connect() as conn:
            if normalized_tag is None:
                rows = conn.execute(
                    """
                    SELECT id, content, tag, priority, done, created_at, completed_at, due_at, remind_at
                    FROM todos
                    WHERE content LIKE ?
                    ORDER BY priority ASC, id ASC
                    """,
                    (like_pattern,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, content, tag, priority, done, created_at, completed_at, due_at, remind_at
                    FROM todos
                    WHERE content LIKE ? AND tag = ?
                    ORDER BY priority ASC, id ASC
                    """,
                    (like_pattern, normalized_tag),
                ).fetchall()
        return [
            TodoItem(
                id=row["id"],
                content=row["content"],
                tag=row["tag"] or "default",
                priority=_normalize_priority(row["priority"] if row["priority"] is not None else 0),
                done=bool(row["done"]),
                created_at=row["created_at"],
                completed_at=row["completed_at"],
                due_at=row["due_at"],
                remind_at=row["remind_at"],
            )
            for row in rows
        ]

    def get_todo(self, todo_id: int) -> TodoItem | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, content, tag, priority, done, created_at, completed_at, due_at, remind_at
                FROM todos
                WHERE id = ?
                """,
                (todo_id,),
            ).fetchone()
        if row is None:
            return None
        return TodoItem(
            id=row["id"],
            content=row["content"],
            tag=row["tag"] or "default",
            priority=_normalize_priority(row["priority"] if row["priority"] is not None else 0),
            done=bool(row["done"]),
            created_at=row["created_at"],
            completed_at=row["completed_at"],
            due_at=row["due_at"],
            remind_at=row["remind_at"],
        )

    def update_todo(
        self,
        todo_id: int,
        *,
        content: str | None = None,
        tag: str | None = None,
        priority: int | object = _UNSET,
        done: bool | None = None,
        due_at: str | None | object = _UNSET,
        remind_at: str | None | object = _UNSET,
    ) -> bool:
        current = self.get_todo(todo_id)
        if current is None:
            return False

        fields: list[str] = []
        values: list[object] = []

        if content is not None:
            fields.append("content = ?")
            values.append(content)
        if tag is not None:
            fields.append("tag = ?")
            values.append(_normalize_tag(tag))
        if priority is not _UNSET:
            if not isinstance(priority, int) or isinstance(priority, bool):
                return False
            if priority < 0:
                return False
            fields.append("priority = ?")
            values.append(_normalize_priority(priority))
        if done is not None:
            fields.append("done = ?")
            values.append(1 if done else 0)
            fields.append("completed_at = ?")
            if done:
                values.append(current.completed_at or _now_iso())
            else:
                values.append(None)
        if due_at is not _UNSET:
            fields.append("due_at = ?")
            values.append(due_at)
        if remind_at is not _UNSET:
            fields.append("remind_at = ?")
            values.append(remind_at)

        effective_due = current.due_at if due_at is _UNSET else due_at
        effective_remind = current.remind_at if remind_at is _UNSET else remind_at
        if effective_remind and not effective_due:
            return False

        if not fields:
            return False

        values.append(todo_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE todos SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            return cur.rowcount > 0

    def delete_todo(self, todo_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
            return cur.rowcount > 0

    def mark_todo_done(self, todo_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE todos SET done = 1, completed_at = COALESCE(completed_at, ?) WHERE id = ?",
                (_now_iso(), todo_id),
            )
            return cur.rowcount > 0

    def add_schedule(
        self,
        title: str,
        event_time: str,
        duration_minutes: int = 60,
        remind_at: str | None = None,
    ) -> int:
        timestamp = _now_iso()
        normalized_duration = _normalize_duration_minutes(duration_minutes)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO schedules (title, event_time, duration_minutes, remind_at, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (title, event_time, normalized_duration, remind_at, timestamp),
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
    ) -> list[int]:
        if not event_times:
            return []
        timestamp = _now_iso()
        normalized_duration = _normalize_duration_minutes(duration_minutes)
        created_ids: list[int] = []
        with self._connect() as conn:
            for event_time in event_times:
                cur = conn.execute(
                    "INSERT INTO schedules (title, event_time, duration_minutes, remind_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (title, event_time, normalized_duration, remind_at, timestamp),
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
        if repeat_times == 1:
            return self.clear_schedule_recurrence(schedule_id)
        if not isinstance(repeat_interval_minutes, int) or isinstance(repeat_interval_minutes, bool):
            return False
        if repeat_interval_minutes < 1:
            return False
        if (
            not isinstance(repeat_times, int)
            or isinstance(repeat_times, bool)
            or (repeat_times != -1 and repeat_times < 2)
        ):
            return False

        timestamp = _now_iso()
        with self._connect() as conn:
            has_schedule = conn.execute("SELECT 1 FROM schedules WHERE id = ?", (schedule_id,)).fetchone() is not None
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
                    schedule_id,
                    start_time,
                    repeat_interval_minutes,
                    repeat_times,
                    remind_start_time,
                    1 if enabled else 0,
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

        rule_by_schedule_id = {rule.schedule_id: rule for rule in rules}
        base_map = {item.id: item for item in base_items}
        combined: list[ScheduleItem] = []
        for base in base_items:
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
                "SELECT id, title, event_time, duration_minutes, remind_at, created_at FROM schedules WHERE id = ?",
                (schedule_id,),
            ).fetchone()
            rule_row = conn.execute(
                """
                SELECT schedule_id, start_time, repeat_interval_minutes, repeat_times, remind_start_time, enabled
                FROM recurring_schedules
                WHERE schedule_id = ?
                """,
                (schedule_id,),
            ).fetchone()
        if row is None:
            return None
        rule: RecurringScheduleRule | None = None
        if rule_row is not None:
            rule = RecurringScheduleRule(
                id=0,
                schedule_id=int(rule_row["schedule_id"]),
                start_time=str(rule_row["start_time"]),
                repeat_interval_minutes=int(rule_row["repeat_interval_minutes"]),
                repeat_times=int(rule_row["repeat_times"]),
                remind_start_time=str(rule_row["remind_start_time"]) if rule_row["remind_start_time"] else None,
                enabled=bool(rule_row["enabled"]),
                created_at=row["created_at"],
            )
        return _attach_recurrence_to_schedule(
            ScheduleItem(
                id=row["id"],
                title=row["title"],
                event_time=row["event_time"],
                duration_minutes=_normalize_duration_minutes(
                    row["duration_minutes"] if row["duration_minutes"] is not None else 60
                ),
                remind_at=str(row["remind_at"]) if row["remind_at"] else None,
                created_at=row["created_at"],
            ),
            rule,
        )

    def update_schedule(
        self,
        schedule_id: int,
        *,
        title: str,
        event_time: str,
        duration_minutes: int | object = _UNSET,
        remind_at: str | None | object = _UNSET,
        repeat_remind_start_time: str | None | object = _UNSET,
    ) -> bool:
        fields = ["title = ?", "event_time = ?"]
        values: list[object] = [title, event_time]

        if duration_minutes is not _UNSET:
            if (
                not isinstance(duration_minutes, int)
                or isinstance(duration_minutes, bool)
                or duration_minutes < 1
            ):
                return False
            normalized_duration = _normalize_duration_minutes(duration_minutes)
            fields.append("duration_minutes = ?")
            values.append(normalized_duration)
        if remind_at is not _UNSET:
            fields.append("remind_at = ?")
            values.append(remind_at)

        values.append(schedule_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE schedules SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            if cur.rowcount <= 0:
                return False
            conn.execute(
                "UPDATE recurring_schedules SET start_time = ? WHERE schedule_id = ?",
                (event_time, schedule_id),
            )
            if repeat_remind_start_time is not _UNSET:
                conn.execute(
                    "UPDATE recurring_schedules SET remind_start_time = ? WHERE schedule_id = ?",
                    (repeat_remind_start_time, schedule_id),
                )
            return True

    def delete_schedule(self, schedule_id: int) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM recurring_schedules WHERE schedule_id = ?", (schedule_id,))
            cur = conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            return cur.rowcount > 0

    def _list_base_schedules(self, conn: sqlite3.Connection) -> list[ScheduleItem]:
        rows = conn.execute(
            "SELECT id, title, event_time, duration_minutes, remind_at, created_at "
            "FROM schedules ORDER BY event_time ASC, id ASC"
        ).fetchall()
        return [
            ScheduleItem(
                id=int(row["id"]),
                title=str(row["title"]),
                event_time=str(row["event_time"]),
                duration_minutes=_normalize_duration_minutes(
                    row["duration_minutes"] if row["duration_minutes"] is not None else 60
                ),
                remind_at=str(row["remind_at"]) if row["remind_at"] else None,
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def _list_recurring_rules(self, conn: sqlite3.Connection) -> list[RecurringScheduleRule]:
        rows = conn.execute(
            """
            SELECT id, schedule_id, start_time, repeat_interval_minutes, repeat_times,
                   remind_start_time, enabled, created_at
            FROM recurring_schedules
            ORDER BY id ASC
            """
        ).fetchall()
        return [
            RecurringScheduleRule(
                id=int(row["id"]),
                schedule_id=int(row["schedule_id"]),
                start_time=str(row["start_time"]),
                repeat_interval_minutes=int(row["repeat_interval_minutes"]),
                repeat_times=int(row["repeat_times"]),
                remind_start_time=str(row["remind_start_time"]) if row["remind_start_time"] else None,
                enabled=bool(row["enabled"]) if row["enabled"] is not None else True,
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def list_base_schedules(self) -> list[ScheduleItem]:
        with self._connect() as conn:
            return self._list_base_schedules(conn)

    def list_recurring_rules(self) -> list[RecurringScheduleRule]:
        with self._connect() as conn:
            return self._list_recurring_rules(conn)

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
        return [
            ReminderDelivery(
                reminder_key=str(row["reminder_key"]),
                source_type=str(row["source_type"]),
                source_id=int(row["source_id"]),
                occurrence_time=str(row["occurrence_time"]) if row["occurrence_time"] else None,
                remind_time=str(row["remind_time"]),
                delivered_at=str(row["delivered_at"]),
                payload=str(row["payload"]) if row["payload"] is not None else None,
            )
            for row in rows
        ]

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
        return [
            ChatTurn(
                user_content=str(row["user_content"] or ""),
                assistant_content=str(row["assistant_content"] or ""),
                created_at=str(row["created_at"] or ""),
            )
            for row in rows
        ]

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
        return [
            ChatTurn(
                user_content=str(row["user_content"] or ""),
                assistant_content=str(row["assistant_content"] or ""),
                created_at=str(row["created_at"] or ""),
            )
            for row in rows
        ]

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
        return [
            ChatTurn(
                user_content=str(row["user_content"] or ""),
                assistant_content=str(row["assistant_content"] or ""),
                created_at=str(row["created_at"] or ""),
            )
            for row in rows
        ]

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
    if tag is None:
        return "default"
    normalized = tag.strip().lower()
    if not normalized:
        return "default"
    return normalized


def _normalize_priority(priority: int) -> int:
    if not isinstance(priority, int) or isinstance(priority, bool):
        raise ValueError("priority must be an integer >= 0")
    if priority < 0:
        raise ValueError("priority must be >= 0")
    return priority


def _normalize_duration_minutes(duration_minutes: int) -> int:
    if not isinstance(duration_minutes, int) or isinstance(duration_minutes, bool):
        raise ValueError("duration_minutes must be an integer >= 1")
    if duration_minutes < 1:
        raise ValueError("duration_minutes must be >= 1")
    return duration_minutes


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
        ScheduleItem(
            id=base.id,
            title=base.title,
            event_time=event_time,
            duration_minutes=base.duration_minutes,
            created_at=base.created_at,
            remind_at=base.remind_at,
            repeat_interval_minutes=rule.repeat_interval_minutes,
            repeat_times=rule.repeat_times,
            repeat_enabled=rule.enabled,
            repeat_remind_start_time=rule.remind_start_time,
        )
        for event_time in expanded_times
    ]


def _attach_recurrence_to_schedule(base: ScheduleItem, rule: RecurringScheduleRule | None) -> ScheduleItem:
    if rule is None:
        return base
    return ScheduleItem(
        id=base.id,
        title=base.title,
        event_time=base.event_time,
        duration_minutes=base.duration_minutes,
        created_at=base.created_at,
        remind_at=base.remind_at,
        repeat_interval_minutes=rule.repeat_interval_minutes,
        repeat_times=rule.repeat_times,
        repeat_enabled=rule.enabled,
        repeat_remind_start_time=rule.remind_start_time,
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
