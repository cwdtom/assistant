from __future__ import annotations

import unittest

from assistant_app.schemas.storage import (
    ScheduleCreateInput,
    ScheduleRecurrenceInput,
    ScheduleUpdateInput,
    ThoughtUpdateInput,
)
from pydantic import ValidationError


class StorageSchemaTest(unittest.TestCase):
    def test_schedule_create_input_rejects_invalid_event_time(self) -> None:
        with self.assertRaises(ValidationError):
            ScheduleCreateInput.model_validate(
                {
                    "title": "项目同步",
                    "event_time": "2026-03-09",
                    "duration_minutes": 30,
                }
            )

    def test_schedule_update_input_preserves_explicit_optional_fields(self) -> None:
        payload = ScheduleUpdateInput.model_validate(
            {
                "schedule_id": 1,
                "title": "项目同步",
                "event_time": "2026-03-09 10:00",
                "tag": None,
                "remind_at": "",
            }
        )

        self.assertEqual(payload.tag, "default")
        self.assertIsNone(payload.remind_at)
        self.assertIn("tag", payload.model_fields_set)
        self.assertIn("remind_at", payload.model_fields_set)
        self.assertNotIn("repeat_remind_start_time", payload.model_fields_set)

    def test_schedule_update_input_rejects_none_duration(self) -> None:
        with self.assertRaises(ValidationError):
            ScheduleUpdateInput.model_validate(
                {
                    "schedule_id": 1,
                    "title": "项目同步",
                    "event_time": "2026-03-09 10:00",
                    "duration_minutes": None,
                }
            )

    def test_schedule_recurrence_input_rejects_bool_repeat_times(self) -> None:
        with self.assertRaises(ValidationError):
            ScheduleRecurrenceInput.model_validate(
                {
                    "schedule_id": 1,
                    "start_time": "2026-03-09 10:00",
                    "repeat_interval_minutes": 60,
                    "repeat_times": True,
                }
            )

    def test_thought_update_input_rejects_explicit_none_status(self) -> None:
        with self.assertRaises(ValidationError):
            ThoughtUpdateInput.model_validate({"content": "更新", "status": None})


if __name__ == "__main__":
    unittest.main()
