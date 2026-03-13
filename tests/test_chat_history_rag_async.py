from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from assistant_app.chat_history_rag_async import AsyncChatHistoryRagIndexer, ChatHistoryInsertEvent


class _RecordListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _FakeSQLiteRagClient:
    def __init__(self, *, fail_on_add: bool = False) -> None:
        self.fail_on_add = fail_on_add
        self.add_calls: list[dict[str, object]] = []
        self.closed = False

    def add_text(self, *, text: str, uri: str | None = None, metadata: dict | None = None) -> None:
        if self.fail_on_add:
            raise RuntimeError("boom")
        self.add_calls.append({"text": text, "uri": uri, "metadata": metadata or {}})

    def close(self) -> None:
        self.closed = True


class AsyncChatHistoryRagIndexerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("tests.chat_history_rag_async")
        self.logger.handlers = []
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.handler = _RecordListHandler()
        self.logger.addHandler(self.handler)

    def tearDown(self) -> None:
        self.logger.handlers = []

    def test_unavailable_dependency_is_logged_and_skipped(self) -> None:
        with patch(
            "assistant_app.chat_history_rag_async.import_module",
            side_effect=ModuleNotFoundError("sqlite_rag not installed"),
        ):
            indexer = AsyncChatHistoryRagIndexer(rag_db_path="tmp/rag.sqlite", logger=self.logger)
            indexer.enqueue(
                ChatHistoryInsertEvent(
                    chat_id=1,
                    user_content="u",
                    assistant_content="a",
                    created_at="2026-03-13 12:00:00",
                )
            )
            indexer.close(wait=True)

        events = [getattr(record, "event", "") for record in self.handler.records]
        self.assertIn("chat_history_rag_unavailable", events)
        self.assertNotIn("chat_history_rag_enqueue", events)

    def test_enqueue_indexes_chat_history_event(self) -> None:
        client = _FakeSQLiteRagClient()
        fake_module = SimpleNamespace(
            SQLiteRag=SimpleNamespace(create=lambda **_: client),
        )
        with patch("assistant_app.chat_history_rag_async.import_module", return_value=fake_module):
            indexer = AsyncChatHistoryRagIndexer(rag_db_path="tmp/rag.sqlite", logger=self.logger)
            indexer.enqueue(
                ChatHistoryInsertEvent(
                    chat_id=42,
                    user_content="用户提问",
                    assistant_content="助手回答",
                    created_at="2026-03-13 12:01:00",
                )
            )
            indexer.close(wait=True)

        self.assertEqual(len(client.add_calls), 1)
        payload = client.add_calls[0]
        self.assertEqual(payload["uri"], "assistant://chat_history/42")
        self.assertIn("chat_id: 42", str(payload["text"]))
        self.assertIn("用户: 用户提问", str(payload["text"]))
        self.assertIn("助手: 助手回答", str(payload["text"]))
        self.assertEqual(
            payload["metadata"],
            {
                "source": "chat_history",
                "chat_id": 42,
                "created_at": "2026-03-13 12:01:00",
            },
        )
        events = [getattr(record, "event", "") for record in self.handler.records]
        self.assertIn("chat_history_rag_enqueue", events)
        self.assertIn("chat_history_rag_index_success", events)
        self.assertTrue(client.closed)

    def test_index_failure_is_logged_without_raising(self) -> None:
        client = _FakeSQLiteRagClient(fail_on_add=True)
        fake_module = SimpleNamespace(
            SQLiteRag=SimpleNamespace(create=lambda **_: client),
        )
        with patch("assistant_app.chat_history_rag_async.import_module", return_value=fake_module):
            indexer = AsyncChatHistoryRagIndexer(rag_db_path="tmp/rag.sqlite", logger=self.logger)
            indexer.enqueue(
                ChatHistoryInsertEvent(
                    chat_id=7,
                    user_content="u",
                    assistant_content="a",
                    created_at="2026-03-13 12:02:00",
                )
            )
            indexer.close(wait=True)

        events = [getattr(record, "event", "") for record in self.handler.records]
        self.assertIn("chat_history_rag_enqueue", events)
        self.assertIn("chat_history_rag_index_failed", events)


if __name__ == "__main__":
    unittest.main()
