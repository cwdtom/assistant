from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from assistant_app.chat_history_rag_search import ChatHistoryRagSearcher


class _RecordListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class _FakeSQLiteRagClient:
    def __init__(self, *, results: list[object] | None = None, fail_on_search: bool = False) -> None:
        self.results = results or []
        self.fail_on_search = fail_on_search
        self.search_calls: list[dict[str, object]] = []
        self.closed = False

    def search(self, *, query: str, top_k: int, new_context: bool) -> list[object]:
        self.search_calls.append({"query": query, "top_k": top_k, "new_context": new_context})
        if self.fail_on_search:
            raise RuntimeError("boom")
        return self.results

    def close(self) -> None:
        self.closed = True


class ChatHistoryRagSearcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.logger = logging.getLogger("tests.chat_history_rag_search")
        self.logger.handlers = []
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.handler = _RecordListHandler()
        self.logger.addHandler(self.handler)

    def tearDown(self) -> None:
        self.logger.handlers = []

    def test_unavailable_dependency_returns_unavailable_status(self) -> None:
        with patch(
            "assistant_app.chat_history_rag_search.import_module",
            side_effect=ModuleNotFoundError("sqlite_rag not installed"),
        ):
            searcher = ChatHistoryRagSearcher(rag_db_path="tmp/rag.sqlite", logger=self.logger)
            result = searcher.search_chat_ids(keyword="牛奶", limit=3)
            searcher.close()

        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.chat_ids, [])
        events = [getattr(record, "event", "") for record in self.handler.records]
        self.assertIn("chat_history_rag_search_unavailable", events)

    def test_search_extracts_chat_ids_from_metadata_and_uri(self) -> None:
        client = _FakeSQLiteRagClient(
            results=[
                SimpleNamespace(
                    document=SimpleNamespace(metadata={"chat_id": "42"}, uri="assistant://chat_history/42")
                ),
                SimpleNamespace(document=SimpleNamespace(metadata={}, uri="assistant://chat_history/7")),
                SimpleNamespace(document=SimpleNamespace(metadata={"chat_id": 42}, uri="assistant://chat_history/42")),
            ]
        )
        fake_module = SimpleNamespace(
            SQLiteRag=SimpleNamespace(create=lambda **_: client),
        )

        with patch("assistant_app.chat_history_rag_search.import_module", return_value=fake_module):
            searcher = ChatHistoryRagSearcher(rag_db_path="tmp/rag.sqlite", logger=self.logger)
            result = searcher.search_chat_ids(keyword="牛奶", limit=3)
            searcher.close()

        self.assertEqual(result.status, "hit")
        self.assertEqual(result.chat_ids, [42, 7])
        self.assertEqual(
            client.search_calls,
            [{"query": "牛奶", "top_k": 3, "new_context": True}],
        )
        self.assertTrue(client.closed)

    def test_search_failure_returns_error_status(self) -> None:
        client = _FakeSQLiteRagClient(fail_on_search=True)
        fake_module = SimpleNamespace(
            SQLiteRag=SimpleNamespace(create=lambda **_: client),
        )

        with patch("assistant_app.chat_history_rag_search.import_module", return_value=fake_module):
            searcher = ChatHistoryRagSearcher(rag_db_path="tmp/rag.sqlite", logger=self.logger)
            result = searcher.search_chat_ids(keyword="牛奶", limit=3)
            searcher.close()

        self.assertEqual(result.status, "error")
        self.assertEqual(result.chat_ids, [])
        events = [getattr(record, "event", "") for record in self.handler.records]
        self.assertIn("chat_history_rag_search_failed", events)
        self.assertTrue(client.closed)

    def test_search_with_unmappable_results_returns_empty_status(self) -> None:
        client = _FakeSQLiteRagClient(
            results=[
                SimpleNamespace(document=SimpleNamespace(metadata={"chat_id": "x"}, uri="https://example.com")),
                SimpleNamespace(document=SimpleNamespace(metadata={}, uri="assistant://chat_history/not-int")),
            ]
        )
        fake_module = SimpleNamespace(
            SQLiteRag=SimpleNamespace(create=lambda **_: client),
        )

        with patch("assistant_app.chat_history_rag_search.import_module", return_value=fake_module):
            searcher = ChatHistoryRagSearcher(rag_db_path="tmp/rag.sqlite", logger=self.logger)
            result = searcher.search_chat_ids(keyword="牛奶", limit=3)
            searcher.close()

        self.assertEqual(result.status, "empty")
        self.assertEqual(result.chat_ids, [])


if __name__ == "__main__":
    unittest.main()
