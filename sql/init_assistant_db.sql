-- CLI AI Personal Assistant: SQLite init schema
-- This script is for initializing a fresh database.

PRAGMA foreign_keys = ON;

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
);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    event_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL DEFAULT 60 CHECK (duration_minutes >= 1),
    remind_at TEXT,
    created_at TEXT NOT NULL
);

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
);

CREATE TABLE IF NOT EXISTS chat_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_content TEXT NOT NULL,
    assistant_content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
