from __future__ import annotations

import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


class FixDirtyDatetimeDataScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "assistant_test.db"
        self.script_path = Path(__file__).resolve().parents[1] / "scripts" / "fix_dirty_datetime_data.sh"
        self._create_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    duration_minutes INTEGER NOT NULL DEFAULT 60,
                    remind_at TEXT,
                    created_at TEXT NOT NULL,
                    tag TEXT NOT NULL DEFAULT 'default'
                );
                CREATE TABLE recurring_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schedule_id INTEGER NOT NULL UNIQUE,
                    start_time TEXT NOT NULL,
                    repeat_interval_minutes INTEGER NOT NULL,
                    repeat_times INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    remind_start_time TEXT
                );
                CREATE TABLE reminder_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reminder_key TEXT NOT NULL UNIQUE,
                    source_type TEXT NOT NULL,
                    source_id INTEGER NOT NULL,
                    occurrence_time TEXT,
                    remind_time TEXT NOT NULL,
                    delivered_at TEXT NOT NULL,
                    payload TEXT
                );
                """
            )

    def test_script_normalizes_second_precision_values(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO schedules (title, event_time, duration_minutes, remind_at, created_at, tag) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "周常更新",
                    "2026-03-02 10:00:00",
                    60,
                    "2026-03-02 09:45:00",
                    "2026-02-28 15:49:00",
                    "me",
                ),
            )
            conn.execute(
                "INSERT INTO recurring_schedules (schedule_id, start_time, repeat_interval_minutes, repeat_times, enabled, created_at, remind_start_time) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (1, "2026-03-02 10:00:00", 10080, -1, 1, "2026-02-28 15:49:00", "2026-03-02 09:45:00"),
            )
            conn.execute(
                "INSERT INTO reminder_deliveries (reminder_key, source_type, source_id, occurrence_time, remind_time, delivered_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    "schedule:1",
                    "schedule",
                    1,
                    "2026-03-02 10:00:00",
                    "2026-03-02 09:45:00",
                    "2026-03-02 09:45:30",
                    None,
                ),
            )

        result = subprocess.run(
            ["bash", str(self.script_path), "--db-path", str(self.db_path), "--no-backup"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("Pending fixes: 6", result.stdout)
        with sqlite3.connect(self.db_path) as conn:
            schedule = conn.execute(
                "SELECT event_time, remind_at FROM schedules WHERE id = 1"
            ).fetchone()
            recurring = conn.execute(
                "SELECT start_time, remind_start_time FROM recurring_schedules WHERE id = 1"
            ).fetchone()
            reminder = conn.execute(
                "SELECT occurrence_time, remind_time, delivered_at FROM reminder_deliveries WHERE id = 1"
            ).fetchone()

        self.assertEqual(schedule, ("2026-03-02 10:00", "2026-03-02 09:45"))
        self.assertEqual(recurring, ("2026-03-02 10:00", "2026-03-02 09:45"))
        self.assertEqual(reminder, ("2026-03-02 10:00", "2026-03-02 09:45", "2026-03-02 09:45:30"))

    def test_script_dry_run_leaves_database_unchanged(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO schedules (title, event_time, duration_minutes, remind_at, created_at, tag) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    "会议",
                    "2026-03-03 11:00:00",
                    30,
                    None,
                    "2026-03-01 08:00:00",
                    "work",
                ),
            )

        result = subprocess.run(
            ["bash", str(self.script_path), "--db-path", str(self.db_path), "--dry-run", "--no-backup"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("Mode: dry-run", result.stdout)
        self.assertIn("Pending fixes: 1", result.stdout)
        with sqlite3.connect(self.db_path) as conn:
            event_time = conn.execute(
                "SELECT event_time FROM schedules WHERE id = 1"
            ).fetchone()[0]
        self.assertEqual(event_time, "2026-03-03 11:00:00")


if __name__ == "__main__":
    unittest.main()
