from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
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


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


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
                    created_at TEXT NOT NULL
                )
                """
            )
            self._ensure_schedule_duration_column(conn)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
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

    def add_schedule(self, title: str, event_time: str, duration_minutes: int = 60) -> int:
        timestamp = _now_iso()
        normalized_duration = _normalize_duration_minutes(duration_minutes)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO schedules (title, event_time, duration_minutes, created_at) VALUES (?, ?, ?, ?)",
                (title, event_time, normalized_duration, timestamp),
            )
            if cur.lastrowid is None:
                raise RuntimeError("failed to insert schedule")
            return int(cur.lastrowid)

    def add_schedules(self, title: str, event_times: list[str], duration_minutes: int = 60) -> list[int]:
        if not event_times:
            return []
        timestamp = _now_iso()
        normalized_duration = _normalize_duration_minutes(duration_minutes)
        created_ids: list[int] = []
        with self._connect() as conn:
            for event_time in event_times:
                cur = conn.execute(
                    "INSERT INTO schedules (title, event_time, duration_minutes, created_at) VALUES (?, ?, ?, ?)",
                    (title, event_time, normalized_duration, timestamp),
                )
                if cur.lastrowid is None:
                    raise RuntimeError("failed to insert schedule")
                created_ids.append(int(cur.lastrowid))
        return created_ids

    def list_schedules(self) -> list[ScheduleItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, event_time, duration_minutes, created_at "
                "FROM schedules ORDER BY event_time ASC, id ASC"
            ).fetchall()
        return [
            ScheduleItem(
                id=row["id"],
                title=row["title"],
                event_time=row["event_time"],
                duration_minutes=_normalize_duration_minutes(
                    row["duration_minutes"] if row["duration_minutes"] is not None else 60
                ),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def find_schedule_conflicts(
        self,
        event_times: list[str],
        *,
        exclude_schedule_id: int | None = None,
    ) -> list[ScheduleItem]:
        if not event_times:
            return []

        unique_times = sorted({item.strip() for item in event_times if item.strip()})
        if not unique_times:
            return []
        placeholders = ", ".join("?" for _ in unique_times)
        query = (
            "SELECT id, title, event_time, duration_minutes, created_at FROM schedules "
            f"WHERE event_time IN ({placeholders})"
        )
        params: list[object] = list(unique_times)
        if exclude_schedule_id is not None:
            query += " AND id != ?"
            params.append(exclude_schedule_id)
        query += " ORDER BY event_time ASC, id ASC"

        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            ScheduleItem(
                id=row["id"],
                title=row["title"],
                event_time=row["event_time"],
                duration_minutes=_normalize_duration_minutes(
                    row["duration_minutes"] if row["duration_minutes"] is not None else 60
                ),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_schedule(self, schedule_id: int) -> ScheduleItem | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, event_time, duration_minutes, created_at FROM schedules WHERE id = ?",
                (schedule_id,),
            ).fetchone()
        if row is None:
            return None
        return ScheduleItem(
            id=row["id"],
            title=row["title"],
            event_time=row["event_time"],
            duration_minutes=_normalize_duration_minutes(
                row["duration_minutes"] if row["duration_minutes"] is not None else 60
            ),
            created_at=row["created_at"],
        )

    def update_schedule(
        self,
        schedule_id: int,
        *,
        title: str,
        event_time: str,
        duration_minutes: int | object = _UNSET,
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

        values.append(schedule_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE schedules SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            return cur.rowcount > 0

    def delete_schedule(self, schedule_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            return cur.rowcount > 0

    def save_message(self, role: str, content: str) -> None:
        timestamp = _now_iso()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chat_history (role, content, created_at) VALUES (?, ?, ?)",
                (role, content, timestamp),
            )

    def recent_messages(self, limit: int = 8) -> list[ChatMessage]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content
                FROM chat_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        # Reverse to keep chronological order for model input.
        rows.reverse()
        return [ChatMessage(role=row["role"], content=row["content"]) for row in rows]


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
