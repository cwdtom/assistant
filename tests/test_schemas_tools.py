from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from assistant_app.db import AssistantDB
from assistant_app.planner_thought import normalize_thought_tool_call
from assistant_app.proactive_tools import ProactiveToolExecutor
from assistant_app.schemas.tools import parse_json_object, validate_thought_tool_arguments
from assistant_app.search import SearchResult


class _FakeSearchProvider:
    def search(self, query: str, top_k: int = 3):  # type: ignore[no-untyped-def]
        return [SearchResult(title=f"{query}-1", snippet="snippet", url="https://example.com")][:top_k]


class ToolSchemaTest(unittest.TestCase):
    def test_parse_json_object_rejects_non_object_json(self) -> None:
        self.assertIsNone(parse_json_object('[1,2,3]'))

    def test_validate_thought_tool_arguments_rejects_invalid_repeat_times(self) -> None:
        parsed = validate_thought_tool_arguments(
            'schedule_add',
            {
                'event_time': '2026-03-07 10:00',
                'title': '站会',
                'interval_minutes': 30,
                'times': 1,
            },
        )

        self.assertIsNone(parsed)

    def test_validate_thought_tool_arguments_rejects_invalid_fetch_url(self) -> None:
        parsed = validate_thought_tool_arguments('internet_search_fetch_url', {'url': 'ftp://example.com'})

        self.assertIsNone(parsed)

    def test_normalize_thought_tool_call_rejects_invalid_schedule_repeat_bool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                'id': 'call_schedule_repeat',
                'type': 'function',
                'function': {
                    'name': 'schedule_repeat',
                    'arguments': json.dumps({'id': 3, 'enabled': 'false'}, ensure_ascii=False),
                },
            }
        )

        self.assertIsNone(decision)

    def test_normalize_thought_tool_call_rejects_invalid_schedule_view_value(self) -> None:
        decision = normalize_thought_tool_call(
            {
                'id': 'call_schedule_view',
                'type': 'function',
                'function': {
                    'name': 'schedule_view',
                    'arguments': json.dumps({'view': 'quarter'}, ensure_ascii=False),
                },
            }
        )

        self.assertIsNone(decision)


class ProactiveToolSchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = AssistantDB(str(Path(self.tmp.name) / 'assistant_test.db'))
        self.executor = ProactiveToolExecutor(
            db=self.db,
            search_provider=_FakeSearchProvider(),
            now=datetime(2026, 3, 6, 9, 0, 0),
            lookahead_hours=24,
            chat_lookback_hours=24,
            internet_search_top_k=3,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_execute_rejects_invalid_history_list_limit(self) -> None:
        with self.assertRaises(ValueError):
            self.executor.execute(tool_name='history_list', arguments={'limit': 0})

    def test_execute_rejects_invalid_schedule_view(self) -> None:
        with self.assertRaises(ValueError):
            self.executor.execute(tool_name='schedule_view', arguments={'view': 'quarter'})


if __name__ == '__main__':
    unittest.main()
