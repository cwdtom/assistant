from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from assistant_app.db import AssistantDB
from assistant_app.planner_thought import build_thought_tool_schemas, normalize_thought_tool_call
from assistant_app.proactive_tools import ProactiveToolExecutor, build_proactive_tool_schemas
from assistant_app.schemas.tools import (
    ProactiveHistoryListArgs,
    parse_json_object,
    validate_thought_tool_arguments,
)
from assistant_app.search import SearchResult


class _FakeSearchProvider:
    def search(self, query: str, top_k: int = 3):  # type: ignore[no-untyped-def]
        return [SearchResult(title=f"{query}-1", snippet="snippet", url="https://example.com")][:top_k]


class ToolSchemaTest(unittest.TestCase):
    def test_build_thought_tool_schemas_uses_pydantic_schema_without_current_step_for_schedule(self) -> None:
        schemas = build_thought_tool_schemas(["schedule"])
        schedule_list_schema = next(
            item for item in schemas if item["function"]["name"] == "schedule_list"
        )

        properties = schedule_list_schema["function"]["parameters"]["properties"]
        self.assertIn("tag", properties)
        self.assertNotIn("current_step", properties)
        self.assertFalse(schedule_list_schema["function"]["parameters"]["additionalProperties"])

    def test_build_thought_tool_schemas_keeps_current_step_for_ask_user(self) -> None:
        schemas = build_thought_tool_schemas(["ask_user"])
        ask_user_schema = next(item for item in schemas if item["function"]["name"] == "ask_user")

        properties = ask_user_schema["function"]["parameters"]["properties"]
        self.assertIn("question", properties)
        self.assertIn("current_step", properties)

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

    def test_validate_thought_tool_arguments_rejects_explicit_null_schedule_duration(self) -> None:
        parsed = validate_thought_tool_arguments(
            'schedule_add',
            {
                'event_time': '2026-03-07 10:00',
                'title': '站会',
                'duration_minutes': None,
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

    def test_normalize_thought_tool_call_rejects_explicit_null_thought_status(self) -> None:
        decision = normalize_thought_tool_call(
            {
                'id': 'call_thought_update',
                'type': 'function',
                'function': {
                    'name': 'thoughts_update',
                    'arguments': json.dumps({'id': 3, 'content': '更新', 'status': None}, ensure_ascii=False),
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

    def test_build_proactive_tool_schemas_uses_pydantic_schema(self) -> None:
        schemas = build_proactive_tool_schemas()
        done_schema = next(item for item in schemas if item["function"]["name"] == "done")

        properties = done_schema["function"]["parameters"]["properties"]
        self.assertIn("score", properties)
        self.assertIn("reason", properties)
        self.assertFalse(done_schema["function"]["parameters"]["additionalProperties"])

    def test_execute_accepts_prevalidated_model_arguments(self) -> None:
        result = self.executor.execute(
            tool_name='history_list',
            arguments=ProactiveHistoryListArgs(limit=5),
        )

        self.assertIn('"limit":5', result)


if __name__ == '__main__':
    unittest.main()
