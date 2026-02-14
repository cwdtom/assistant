from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

_UNSET = object()


@dataclass(frozen=True)
class TodoItem:
    id: int
    content: str
    tag: str
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
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

    def add_todo(
        self,
        content: str,
        tag: str = "default",
        due_at: str | None = None,
        remind_at: str | None = None,
    ) -> int:
        if remind_at and not due_at:
            raise ValueError("remind_at requires due_at")
        timestamp = _now_iso()
        normalized_tag = _normalize_tag(tag)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO todos (content, tag, done, created_at, completed_at, due_at, remind_at)
                VALUES (?, ?, 0, ?, NULL, ?, ?)
                """,
                (content, normalized_tag, timestamp, due_at, remind_at),
            )
            return int(cur.lastrowid)

    def list_todos(self, tag: str | None = None) -> list[TodoItem]:
        normalized_tag = _normalize_tag(tag) if tag is not None else None
        with self._connect() as conn:
            if normalized_tag is None:
                rows = conn.execute(
                    """
                    SELECT id, content, tag, done, created_at, completed_at, due_at, remind_at
                    FROM todos
                    ORDER BY id ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, content, tag, done, created_at, completed_at, due_at, remind_at
                    FROM todos
                    WHERE tag = ?
                    ORDER BY id ASC
                    """,
                    (normalized_tag,),
                ).fetchall()
        return [
            TodoItem(
                id=row["id"],
                content=row["content"],
                tag=row["tag"] or "default",
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
                SELECT id, content, tag, done, created_at, completed_at, due_at, remind_at
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

    def add_schedule(self, title: str, event_time: str) -> int:
        timestamp = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO schedules (title, event_time, created_at) VALUES (?, ?, ?)",
                (title, event_time, timestamp),
            )
            return int(cur.lastrowid)

    def list_schedules(self) -> list[ScheduleItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, event_time, created_at FROM schedules ORDER BY event_time ASC, id ASC"
            ).fetchall()
        return [
            ScheduleItem(
                id=row["id"],
                title=row["title"],
                event_time=row["event_time"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_schedule(self, schedule_id: int) -> ScheduleItem | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, title, event_time, created_at FROM schedules WHERE id = ?",
                (schedule_id,),
            ).fetchone()
        if row is None:
            return None
        return ScheduleItem(
            id=row["id"],
            title=row["title"],
            event_time=row["event_time"],
            created_at=row["created_at"],
        )

    def update_schedule(self, schedule_id: int, *, title: str, event_time: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE schedules SET title = ?, event_time = ? WHERE id = ?",
                (title, event_time, schedule_id),
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
