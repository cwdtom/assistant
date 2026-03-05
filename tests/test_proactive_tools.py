from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from assistant_app.db import AssistantDB
from assistant_app.proactive_tools import ProactiveToolExecutor
from assistant_app.search import SearchResult


class _FakeSearchProvider:
    def search(self, query: str, top_k: int = 3):  # type: ignore[no-untyped-def]
        return [SearchResult(title=f"{query}-1", snippet="snippet", url="https://example.com")][:top_k]


class ProactiveToolExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "assistant_test.db")
        self.db = AssistantDB(self.db_path)
        self.now = datetime(2026, 3, 5, 9, 0, 0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_history_search_respects_chat_lookback_window(self) -> None:
        self.db.save_turn(user_content="旧记录关键词", assistant_content="old")
        self.db.save_turn(user_content="最近关键词", assistant_content="new")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE chat_history SET created_at = ? WHERE id = 1",
                ((self.now - timedelta(hours=30)).isoformat(sep=" "),),
            )
            conn.execute(
                "UPDATE chat_history SET created_at = ? WHERE id = 2",
                ((self.now - timedelta(hours=1)).isoformat(sep=" "),),
            )
            conn.commit()

        executor = ProactiveToolExecutor(
            db=self.db,
            search_provider=_FakeSearchProvider(),
            now=self.now,
            lookahead_hours=24,
            chat_lookback_hours=24,
            internet_search_top_k=3,
        )

        raw = executor.execute(
            tool_name="history_search",
            arguments={"keyword": "关键词", "limit": 20},
        )
        payload = json.loads(raw)

        self.assertEqual(payload["count"], 1)
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["user_content"], "最近关键词")

    def test_history_search_matches_user_or_assistant_content(self) -> None:
        self.db.save_turn(user_content="hello", assistant_content="今天会下雨")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE chat_history SET created_at = ? WHERE id = 1",
                ((self.now - timedelta(hours=1)).isoformat(sep=" "),),
            )
            conn.commit()

        executor = ProactiveToolExecutor(
            db=self.db,
            search_provider=_FakeSearchProvider(),
            now=self.now,
            lookahead_hours=24,
            chat_lookback_hours=24,
            internet_search_top_k=3,
        )

        raw = executor.execute(
            tool_name="history_search",
            arguments={"keyword": "下雨", "limit": 20},
        )
        payload = json.loads(raw)

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["assistant_content"], "今天会下雨")


if __name__ == "__main__":
    unittest.main()
