from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class TodoItem:
    id: int
    content: str
    done: bool
    created_at: str


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
                    done INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                )
                """
            )
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

    def add_todo(self, content: str) -> int:
        timestamp = _now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO todos (content, done, created_at) VALUES (?, 0, ?)",
                (content, timestamp),
            )
            return int(cur.lastrowid)

    def list_todos(self) -> list[TodoItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, content, done, created_at FROM todos ORDER BY id ASC"
            ).fetchall()
        return [
            TodoItem(
                id=row["id"],
                content=row["content"],
                done=bool(row["done"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def mark_todo_done(self, todo_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE todos SET done = 1 WHERE id = ?", (todo_id,))
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
