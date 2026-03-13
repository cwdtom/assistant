from __future__ import annotations

import unittest

from assistant_app.schemas.commands import (
    parse_date_command,
    parse_history_list_command,
    parse_history_search_command,
    parse_schedule_add_command,
    parse_schedule_update_command,
    parse_schedule_view_command,
    parse_thoughts_list_command,
    parse_thoughts_update_command,
    parse_tool_command_payload,
)


class CommandSchemaTest(unittest.TestCase):
    def test_parse_date_command_builds_typed_payload(self) -> None:
        command = parse_date_command("/date")

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.tool_name, "system_date")
        self.assertEqual(command.to_runtime_payload().tool_name, "system_date")

    def test_parse_date_command_rejects_extra_args(self) -> None:
        self.assertIsNone(parse_date_command("/date now"))

    def test_parse_tool_command_payload_supports_date_command(self) -> None:
        payload = parse_tool_command_payload("/date")

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.tool_name, "system_date")

    def test_parse_history_search_command_builds_typed_payload(self) -> None:
        command = parse_history_search_command("/history search 牛奶 --limit 5")

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.tool_name, "history_search")
        self.assertEqual(command.arguments.keyword, "牛奶")
        self.assertEqual(command.arguments.limit, 5)
        self.assertEqual(command.to_runtime_payload().tool_name, "history_search")

    def test_parse_history_list_command_rejects_invalid_limit(self) -> None:
        self.assertIsNone(parse_history_list_command("/history list --limit 0"))

    def test_parse_schedule_add_command_preserves_repeat_defaults(self) -> None:
        command = parse_schedule_add_command("/schedule add 2026-03-10 09:00 站会 --interval 60")

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.arguments.event_time, "2026-03-10 09:00")
        self.assertEqual(command.arguments.interval_minutes, 60)
        self.assertEqual(command.arguments.times, -1)
        self.assertEqual(command.arguments.tag, "default")

    def test_parse_schedule_add_command_normalizes_hashtag_tag(self) -> None:
        command = parse_schedule_add_command("/schedule add 2026-03-10 09:00 站会 --tag #Work")

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.arguments.tag, "work")

    def test_parse_schedule_update_command_omits_absent_optional_fields(self) -> None:
        command = parse_schedule_update_command("/schedule update 7 2026-03-10 09:00 复盘会")

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.arguments.id, 7)
        self.assertNotIn("tag", command.arguments.model_fields_set)
        self.assertNotIn("duration_minutes", command.arguments.model_fields_set)
        self.assertNotIn("remind_at", command.arguments.model_fields_set)
        self.assertNotIn("interval_minutes", command.arguments.model_fields_set)
        self.assertNotIn("times", command.arguments.model_fields_set)
        self.assertIsNone(command.arguments.times)

    def test_parse_schedule_view_command_rejects_invalid_month_anchor(self) -> None:
        self.assertIsNone(parse_schedule_view_command("/schedule view month 2026-03-15"))

    def test_parse_thoughts_list_command_rejects_invalid_status(self) -> None:
        self.assertIsNone(parse_thoughts_list_command("/thoughts list --status 进行中"))

    def test_parse_thoughts_update_command_tracks_status_presence(self) -> None:
        command = parse_thoughts_update_command("/thoughts update 3 记得买牛奶 --status completed")

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual(command.arguments.id, 3)
        self.assertEqual(command.arguments.content, "记得买牛奶")
        self.assertEqual(command.arguments.status, "completed")
        self.assertIn("status", command.arguments.model_fields_set)


if __name__ == "__main__":
    unittest.main()
