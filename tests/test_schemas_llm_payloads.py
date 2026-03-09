from __future__ import annotations

import unittest

from pydantic import ValidationError

from assistant_app.schemas.llm_payloads import (
    PersonaRewriteRequestPayload,
    UserProfileRefreshPromptPayload,
)


class LLMPayloadSchemaTest(unittest.TestCase):
    def test_persona_rewrite_request_requires_non_empty_requirements(self) -> None:
        with self.assertRaises(ValidationError):
            PersonaRewriteRequestPayload.model_validate(
                {
                    "scene": "final_response",
                    "persona": "可靠同事",
                    "text": "任务完成",
                    "requirements": ["", "   "],
                }
            )

    def test_user_profile_refresh_prompt_payload_accepts_chat_turns(self) -> None:
        payload = UserProfileRefreshPromptPayload.model_validate(
            {
                "task": "refresh_user_profile",
                "time": {"now": "2026-03-09 10:00", "window_days": 30},
                "limits": {"max_turns": 100, "actual_turns": 2},
                "current_user_profile": "# 画像",
                "chat_turns": [
                    {
                        "created_at": "2026-03-09 09:00:00",
                        "user_content": "今天想喝咖啡",
                        "assistant_content": "记下来了",
                    },
                    {
                        "created_at": "2026-03-09 09:05:00",
                        "user_content": "下午提醒我开会",
                        "assistant_content": "好的",
                    },
                ],
                "output_requirements": ["输出完整新版 user_profile Markdown"],
            }
        )

        self.assertEqual(payload.time.now, "2026-03-09 10:00")
        self.assertEqual(payload.limits.actual_turns, 2)
        self.assertEqual(payload.chat_turns[0].user_content, "今天想喝咖啡")


if __name__ == "__main__":
    unittest.main()
