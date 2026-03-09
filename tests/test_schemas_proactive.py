from __future__ import annotations

import unittest
from datetime import datetime

from assistant_app.config import DEFAULT_PROACTIVE_REMINDER_SCORE_THRESHOLD
from assistant_app.schemas.domain import ChatTurn, ScheduleItem
from assistant_app.schemas.proactive import (
    ProactiveContextSnapshot,
    ProactivePromptPayload,
    ProactiveScheduleGetToolResult,
    ProactiveScheduleViewToolResult,
)
from pydantic import ValidationError


class ProactiveSchemaTest(unittest.TestCase):
    def test_prompt_payload_falls_back_to_default_score_threshold(self) -> None:
        payload = ProactivePromptPayload.model_validate(
            {
                "policy": {
                    "score_threshold": True,
                }
            }
        )

        self.assertEqual(payload.score_threshold, DEFAULT_PROACTIVE_REMINDER_SCORE_THRESHOLD)

    def test_context_snapshot_builds_explicit_prompt_payload(self) -> None:
        snapshot = ProactiveContextSnapshot(
            now=datetime(2026, 3, 5, 9, 0),
            lookahead_hours=24,
            chat_lookback_hours=12,
            schedules=[
                ScheduleItem(
                    id=1,
                    title="晨会",
                    tag="work",
                    event_time="2026-03-05 10:00",
                    duration_minutes=30,
                    created_at="2026-03-05 08:00:00",
                )
            ],
            turns=[
                ChatTurn(
                    created_at="2026-03-05 08:30:00",
                    user_content="十点提醒我准备晨会",
                    assistant_content="好的，我会留意这个安排。",
                )
            ],
            user_profile_path="/tmp/user_profile.md",
            user_profile_content="用户偏好：晨会前提醒",
        )

        payload = snapshot.to_prompt_payload(
            night_quiet_hint="23:00-08:00",
            score_threshold=88,
            max_steps=5,
            internet_search_allowed=True,
        )

        self.assertEqual(payload.policy.score_threshold, 88)
        self.assertEqual(payload.context_window.schedule_forward_hours, 24)
        self.assertEqual(payload.user_profile.content, "用户偏好：晨会前提醒")
        self.assertEqual(payload.internal_context.schedules[0].title, "晨会")
        self.assertEqual(payload.internal_context.recent_chat_turns[0].assistant_content, "好的，我会留意这个安排。")
        dumped = payload.model_dump(mode="json")
        self.assertEqual(dumped["output_contract"]["terminal_action"], "done")
        self.assertEqual(dumped["internal_context"]["schedules"][0]["event_time"], "2026-03-05 10:00")

    def test_schedule_view_tool_result_normalizes_anchor(self) -> None:
        payload = ProactiveScheduleViewToolResult.model_validate(
            {
                "view": "month",
                "anchor": "2026-03",
                "count": 0,
                "items": [],
            }
        )

        self.assertEqual(payload.anchor, "2026-03")

    def test_schedule_get_tool_result_requires_item_when_found(self) -> None:
        with self.assertRaises(ValidationError):
            ProactiveScheduleGetToolResult.model_validate({"found": True})


if __name__ == "__main__":
    unittest.main()
