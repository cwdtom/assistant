from __future__ import annotations

import json
import unittest

from assistant_app.planner_thought import build_thought_tool_schemas, normalize_thought_tool_call
from assistant_app.schemas.tools import (
    InternetSearchArgs,
    InternetSearchFetchUrlArgs,
    coerce_history_action_payload,
    coerce_internet_search_action_payload,
    coerce_schedule_action_payload,
    coerce_system_action_payload,
    coerce_thoughts_action_payload,
    coerce_timer_action_payload,
    coerce_user_profile_action_payload,
    parse_json_object,
    validate_thought_tool_arguments,
)


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

    def test_build_thought_tool_schemas_expands_system_group(self) -> None:
        schemas = build_thought_tool_schemas(["system"])
        system_date_schema = next(item for item in schemas if item["function"]["name"] == "system_date")

        properties = system_date_schema["function"]["parameters"]["properties"]
        self.assertEqual(properties, {})
        self.assertFalse(system_date_schema["function"]["parameters"]["additionalProperties"])

    def test_build_thought_tool_schemas_expands_timer_group(self) -> None:
        schemas = build_thought_tool_schemas(["timer"])
        timer_list_schema = next(item for item in schemas if item["function"]["name"] == "timer_list")

        properties = timer_list_schema["function"]["parameters"]["properties"]
        self.assertEqual(properties, {})
        self.assertFalse(timer_list_schema["function"]["parameters"]["additionalProperties"])
        timer_tool_names = {item["function"]["name"] for item in schemas}
        self.assertTrue(
            {"timer_add", "timer_list", "timer_get", "timer_update", "timer_delete"}.issubset(timer_tool_names)
        )

    def test_build_thought_tool_schemas_expands_user_profile_group(self) -> None:
        schemas = build_thought_tool_schemas(["user_profile"])
        tool_names = {item["function"]["name"] for item in schemas}
        overwrite_schema = next(item for item in schemas if item["function"]["name"] == "user_profile_overwrite")

        self.assertEqual(tool_names, {"user_profile_get", "user_profile_overwrite", "ask_user", "done"})
        properties = overwrite_schema["function"]["parameters"]["properties"]
        self.assertEqual(properties["content"]["type"], "string")
        self.assertNotIn("current_step", properties)

    def test_build_thought_tool_schemas_can_disable_timer_group(self) -> None:
        schemas = build_thought_tool_schemas(["timer"], allow_ask_user=False, allow_timer=False)
        tool_names = {item["function"]["name"] for item in schemas}

        self.assertEqual(tool_names, {"done"})

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

    def test_validate_thought_tool_arguments_rejects_invalid_timer_cron_expr(self) -> None:
        parsed = validate_thought_tool_arguments(
            'timer_add',
            {'task_name': 'daily-report', 'cron_expr': 'not-a-cron', 'prompt': '生成日报'},
        )

        self.assertIsNone(parsed)

    def test_validate_thought_tool_arguments_rejects_timer_update_without_mutation_fields(self) -> None:
        parsed = validate_thought_tool_arguments('timer_update', {'id': 3})

        self.assertIsNone(parsed)

    def test_validate_thought_tool_arguments_accepts_empty_user_profile_overwrite_content(self) -> None:
        parsed = validate_thought_tool_arguments('user_profile_overwrite', {'content': ''})

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.model_dump(), {'current_step': '', 'content': ''})

    def test_validate_thought_tool_arguments_rejects_null_user_profile_overwrite_content(self) -> None:
        parsed = validate_thought_tool_arguments('user_profile_overwrite', {'content': None})

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

    def test_normalize_thought_tool_call_schedule_payload_matches_system_action_contract(self) -> None:
        decision = normalize_thought_tool_call(
            {
                'id': 'call_schedule_add',
                'type': 'function',
                'function': {
                    'name': 'schedule_add',
                    'arguments': json.dumps(
                        {
                            'event_time': '2026-03-08 09:30',
                            'title': '项目同步',
                            'tag': ' Work ',
                            'duration_minutes': '45',
                        },
                        ensure_ascii=False,
                    ),
                },
            }
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        assert decision.next_action is not None
        assert decision.next_action.payload is not None
        self.assertEqual(
            decision.next_action.payload,
            coerce_schedule_action_payload(
                {
                    'action': 'add',
                    'event_time': '2026-03-08 09:30',
                    'title': '项目同步',
                    'tag': ' Work ',
                    'duration_minutes': '45',
                }
            ),
        )

    def test_normalize_thought_tool_call_history_payload_matches_system_action_contract(self) -> None:
        decision = normalize_thought_tool_call(
            {
                'id': 'call_history_search',
                'type': 'function',
                'function': {
                    'name': 'history_search',
                    'arguments': json.dumps({'keyword': '周报', 'limit': '5'}, ensure_ascii=False),
                },
            }
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        assert decision.next_action is not None
        assert decision.next_action.payload is not None
        self.assertEqual(
            decision.next_action.payload,
            coerce_history_action_payload({'action': 'search', 'keyword': '周报', 'limit': '5'}),
        )

    def test_coerce_internet_search_action_payload_search(self) -> None:
        payload = coerce_internet_search_action_payload({'action': 'search', 'query': 'OpenAI Responses API'})

        self.assertEqual(payload.tool_name, 'internet_search_tool')
        self.assertEqual(payload.arguments, InternetSearchArgs(query='OpenAI Responses API'))

    def test_coerce_internet_search_action_payload_fetch_url(self) -> None:
        payload = coerce_internet_search_action_payload({'action': 'fetch_url', 'url': 'https://example.com'})

        self.assertEqual(payload.tool_name, 'internet_search_fetch_url')
        self.assertEqual(payload.arguments, InternetSearchFetchUrlArgs(url='https://example.com'))

    def test_normalize_thought_tool_call_thoughts_payload_matches_system_action_contract(self) -> None:
        decision = normalize_thought_tool_call(
            {
                'id': 'call_thought_update',
                'type': 'function',
                'function': {
                    'name': 'thoughts_update',
                    'arguments': json.dumps(
                        {'id': 3, 'content': '记得补周报', 'status': '完成'},
                        ensure_ascii=False,
                    ),
                },
            }
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        assert decision.next_action is not None
        assert decision.next_action.payload is not None
        self.assertEqual(
            decision.next_action.payload,
            coerce_thoughts_action_payload(
                {'action': 'update', 'id': 3, 'content': '记得补周报', 'status': '完成'}
            ),
        )

    def test_normalize_thought_tool_call_system_payload_matches_system_action_contract(self) -> None:
        decision = normalize_thought_tool_call(
            {
                'id': 'call_system_date',
                'type': 'function',
                'function': {
                    'name': 'system_date',
                    'arguments': json.dumps({}, ensure_ascii=False),
                },
            }
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        assert decision.next_action is not None
        assert decision.next_action.payload is not None
        self.assertEqual(
            decision.next_action.payload,
            coerce_system_action_payload({'action': 'date'}),
        )

    def test_normalize_thought_tool_call_timer_payload_matches_system_action_contract(self) -> None:
        decision = normalize_thought_tool_call(
            {
                'id': 'call_timer_add',
                'type': 'function',
                'function': {
                    'name': 'timer_add',
                    'arguments': json.dumps(
                        {
                            'task_name': 'daily-report',
                            'cron_expr': '0 9 * * *',
                            'prompt': '生成日报',
                            'run_limit': '3',
                        },
                        ensure_ascii=False,
                    ),
                },
            }
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        assert decision.next_action is not None
        assert decision.next_action.payload is not None
        self.assertEqual(
            decision.next_action.payload,
            coerce_timer_action_payload(
                {
                    'action': 'add',
                    'task_name': 'daily-report',
                    'cron_expr': '0 9 * * *',
                    'prompt': '生成日报',
                    'run_limit': '3',
                }
            ),
        )

    def test_normalize_thought_tool_call_user_profile_payload_matches_system_action_contract(self) -> None:
        decision = normalize_thought_tool_call(
            {
                'id': 'call_user_profile_overwrite',
                'type': 'function',
                'function': {
                    'name': 'user_profile_overwrite',
                    'arguments': json.dumps({'content': ''}, ensure_ascii=False),
                },
            }
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        assert decision.next_action is not None
        assert decision.next_action.payload is not None
        self.assertEqual(
            decision.next_action.payload,
            coerce_user_profile_action_payload({'action': 'overwrite', 'content': ''}),
        )


if __name__ == '__main__':
    unittest.main()
