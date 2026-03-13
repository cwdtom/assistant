from __future__ import annotations

import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


class FixThoughtStatusDataScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "assistant_test.db"
        self.script_path = Path(__file__).resolve().parents[1] / "scripts" / "fix_thought_status_data.sh"
        self._create_legacy_thoughts_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _create_legacy_thoughts_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE thoughts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('未完成', '完成', '删除')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def test_script_migrates_legacy_status_values_and_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO thoughts (content, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                [
                    ("想法A", "未完成", "2026-03-10 09:00:00", "2026-03-10 09:00:00"),
                    ("想法B", "完成", "2026-03-10 09:01:00", "2026-03-10 09:01:00"),
                    ("想法C", "删除", "2026-03-10 09:02:00", "2026-03-10 09:02:00"),
                ],
            )

        result = subprocess.run(
            ["bash", str(self.script_path), "--db-path", str(self.db_path), "--no-backup"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("Schema rebuild required: true", result.stdout)
        self.assertIn("Schema rebuilt: true", result.stdout)
        self.assertIn("Pending fixes: 3", result.stdout)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT status FROM thoughts ORDER BY id ASC").fetchall()
            table_sql = conn.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'table' AND name = 'thoughts'
                """
            ).fetchone()[0]

        self.assertEqual([row[0] for row in rows], ["pending", "completed", "deleted"])
        self.assertIn("'pending'", table_sql)
        self.assertIn("'completed'", table_sql)
        self.assertIn("'deleted'", table_sql)
        self.assertNotIn("未完成", table_sql)

    def test_script_dry_run_keeps_legacy_data_unchanged(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO thoughts (content, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                ("想法A", "未完成", "2026-03-10 09:00:00", "2026-03-10 09:00:00"),
            )

        result = subprocess.run(
            ["bash", str(self.script_path), "--db-path", str(self.db_path), "--dry-run", "--no-backup"],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("Mode: dry-run", result.stdout)
        self.assertIn("Schema rebuild required: true", result.stdout)
        self.assertIn("Schema rebuilt: false", result.stdout)
        self.assertIn("Pending fixes: 1", result.stdout)
        with sqlite3.connect(self.db_path) as conn:
            status = conn.execute("SELECT status FROM thoughts WHERE id = 1").fetchone()[0]
            table_sql = conn.execute(
                """
                SELECT sql
                FROM sqlite_master
                WHERE type = 'table' AND name = 'thoughts'
                """
            ).fetchone()[0]
        self.assertEqual(status, "未完成")
        self.assertIn("未完成", table_sql)


if __name__ == "__main__":
    unittest.main()
