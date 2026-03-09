from __future__ import annotations

import unittest
from pathlib import Path

from assistant_app.schemas.domain import ChatTurn
from assistant_app.schemas.user_profile import (
    UserProfileRefreshPreparation,
    UserProfileRefreshResult,
)
from pydantic import ValidationError


class UserProfileSchemaTest(unittest.TestCase):
    def test_refresh_result_requires_profile_content_when_successful(self) -> None:
        with self.assertRaises(ValidationError):
            UserProfileRefreshResult(
                ok=True,
                reason="刷新成功",
                profile_content=None,
                used_turns=1,
            )

    def test_refresh_result_requires_positive_used_turns_when_successful(self) -> None:
        with self.assertRaises(ValidationError):
            UserProfileRefreshResult(
                ok=True,
                reason="刷新成功",
                profile_content="# 用户画像",
                used_turns=0,
            )

    def test_refresh_preparation_requires_non_empty_turns(self) -> None:
        with self.assertRaises(ValidationError):
            UserProfileRefreshPreparation(
                profile_path=Path("/tmp/user_profile.md"),
                current_profile="# 用户画像",
                turns=[],
            )

    def test_refresh_preparation_accepts_chat_turn_models(self) -> None:
        preparation = UserProfileRefreshPreparation(
            profile_path=Path("/tmp/user_profile.md"),
            current_profile="# 用户画像",
            turns=[
                ChatTurn(
                    created_at="2026-03-05 09:00:00",
                    user_content="今天喝拿铁",
                    assistant_content="已记录你的饮品偏好",
                )
            ],
        )

        self.assertEqual(preparation.turns[0].user_content, "今天喝拿铁")


if __name__ == "__main__":
    unittest.main()
