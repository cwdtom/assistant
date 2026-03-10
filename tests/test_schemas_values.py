from __future__ import annotations

import unittest

from assistant_app.schemas.domain import ScheduleItem
from assistant_app.schemas.proactive import ProactiveScheduleContextItem
from assistant_app.schemas.storage import (
    ScheduleCreateInput,
    ScheduleRecurrenceInput,
    ScheduleUpdateInput,
)
from assistant_app.schemas.values import (
    DefaultTagValue,
    NormalizedTagValue,
    OptionalScheduleDateTimeValue,
    OptionalTagValue,
    ScheduleDateTimeValue,
    ScheduleDurationValue,
    ScheduleRepeatTimesValue,
)


class ValueNormalizationConsistencyTest(unittest.TestCase):
    def test_tag_normalization_is_consistent_across_layers(self) -> None:
        raw_tag = "  #Work  "

        self.assertEqual(DefaultTagValue.model_validate({"tag": raw_tag}).tag, "work")
        self.assertEqual(OptionalTagValue.model_validate({"tag": raw_tag}).tag, "work")
        self.assertEqual(NormalizedTagValue.model_validate({"tag": raw_tag}).tag, "work")
        self.assertEqual(
            ScheduleCreateInput.model_validate(
                {
                    "title": "项目同步",
                    "event_time": "2026-03-09 10:00",
                    "tag": raw_tag,
                }
            ).tag,
            "work",
        )
        self.assertEqual(
            ScheduleUpdateInput.model_validate(
                {
                    "schedule_id": 1,
                    "title": "项目同步",
                    "event_time": "2026-03-09 10:00",
                    "tag": raw_tag,
                }
            ).tag,
            "work",
        )
        self.assertEqual(
            ScheduleItem.model_validate(
                {
                    "id": 1,
                    "title": "项目同步",
                    "tag": raw_tag,
                    "event_time": "2026-03-09 10:00",
                    "duration_minutes": 30,
                    "created_at": "2026-03-09 09:00:00",
                }
            ).tag,
            "work",
        )

    def test_datetime_normalization_is_consistent_across_layers(self) -> None:
        raw_event_time = " 2026-03-09   10:00 "

        self.assertEqual(ScheduleDateTimeValue.model_validate({"value": raw_event_time}).value, "2026-03-09 10:00")
        self.assertEqual(
            OptionalScheduleDateTimeValue.model_validate({"value": raw_event_time}).value,
            "2026-03-09 10:00",
        )
        self.assertEqual(
            ScheduleCreateInput.model_validate(
                {
                    "title": "项目同步",
                    "event_time": raw_event_time,
                    "remind_at": raw_event_time,
                }
            ).event_time,
            "2026-03-09 10:00",
        )
        payload = ScheduleUpdateInput.model_validate(
            {
                "schedule_id": 1,
                "title": "项目同步",
                "event_time": raw_event_time,
                "remind_at": raw_event_time,
                "repeat_remind_start_time": raw_event_time,
            }
        )
        self.assertEqual(payload.event_time, "2026-03-09 10:00")
        self.assertEqual(payload.remind_at, "2026-03-09 10:00")
        self.assertEqual(payload.repeat_remind_start_time, "2026-03-09 10:00")

        item = ScheduleItem.model_validate(
            {
                "id": 1,
                "title": "项目同步",
                "tag": "work",
                "event_time": raw_event_time,
                "duration_minutes": 30,
                "created_at": " 2026-03-09 09:00:00 ",
                "remind_at": raw_event_time,
            }
        )
        self.assertEqual(item.event_time, "2026-03-09 10:00")
        self.assertEqual(item.created_at, "2026-03-09 09:00:00")
        self.assertEqual(item.remind_at, "2026-03-09 10:00")

    def test_repeat_times_normalization_is_consistent_across_layers(self) -> None:
        raw_repeat_times = " 3 "

        self.assertEqual(ScheduleRepeatTimesValue.model_validate({"value": raw_repeat_times}).value, 3)
        self.assertEqual(
            ScheduleRecurrenceInput.model_validate(
                {
                    "schedule_id": 1,
                    "start_time": "2026-03-09 10:00",
                    "repeat_interval_minutes": 60,
                    "repeat_times": raw_repeat_times,
                }
            ).repeat_times,
            3,
        )

        schedule_item = ScheduleItem.model_validate(
            {
                "id": 1,
                "title": "项目同步",
                "tag": "work",
                "event_time": "2026-03-09 10:00",
                "duration_minutes": 30,
                "created_at": "2026-03-09 09:00:00",
                "repeat_interval_minutes": 60,
                "repeat_times": raw_repeat_times,
                "repeat_enabled": True,
            }
        )
        proactive_item = ProactiveScheduleContextItem.model_validate(
            {
                "id": 1,
                "title": "项目同步",
                "tag": "work",
                "event_time": "2026-03-09 10:00",
                "duration_minutes": 30,
                "repeat_interval_minutes": 60,
                "repeat_times": raw_repeat_times,
                "repeat_enabled": True,
            }
        )

        self.assertEqual(schedule_item.repeat_times, 3)
        self.assertEqual(proactive_item.repeat_times, 3)

    def test_duration_normalization_is_consistent_across_layers(self) -> None:
        raw_duration = " 45 "

        self.assertEqual(ScheduleDurationValue.model_validate({"duration_minutes": raw_duration}).duration_minutes, 45)
        self.assertEqual(
            ScheduleCreateInput.model_validate(
                {
                    "title": "项目同步",
                    "event_time": "2026-03-09 10:00",
                    "duration_minutes": raw_duration,
                }
            ).duration_minutes,
            45,
        )
        self.assertEqual(
            ScheduleUpdateInput.model_validate(
                {
                    "schedule_id": 1,
                    "title": "项目同步",
                    "event_time": "2026-03-09 10:00",
                    "duration_minutes": raw_duration,
                }
            ).duration_minutes,
            45,
        )


if __name__ == "__main__":
    unittest.main()
