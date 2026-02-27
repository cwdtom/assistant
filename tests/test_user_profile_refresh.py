from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from assistant_app.db import AssistantDB
from assistant_app.user_profile_refresh import UserProfileRefreshService


class _MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _FakeLLMClient:
    def __init__(self, replies: list[str] | None = None, raises: Exception | None = None) -> None:
        self.replies = list(replies or [])
        self.raises = raises
        self.temperature_calls: list[float] = []
        self.messages: list[list[dict[str, str]]] = []

    def reply(self, messages: list[dict[str, str]]) -> str:
        self.messages.append(messages)
        if self.raises is not None:
            raise self.raises
        if not self.replies:
            return ""
        return self.replies.pop(0)

    def reply_with_temperature(self, messages: list[dict[str, str]], *, temperature: float) -> str:
        self.temperature_calls.append(temperature)
        return self.reply(messages)


class UserProfileRefreshServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "assistant_test.db"
        self.profile_path = Path(self.tmp.name) / "user_profile.md"
        self.profile_path.write_text("# 用户画像\n- 偏好: 咖啡\n", encoding="utf-8")
        self.db = AssistantDB(str(self.db_path))
        self.db.save_turn(user_content="今天喝拿铁", assistant_content="已记录你喜欢拿铁")
        self._set_turn_time(row_id=1, created_at="2026-02-26 10:00:00")
        self.reload_count = 0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _set_turn_time(self, *, row_id: int, created_at: str) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("UPDATE chat_history SET created_at = ? WHERE id = ?", (created_at, row_id))
            conn.commit()
        finally:
            conn.close()

    def _reload_agent(self) -> bool:
        self.reload_count += 1
        return True

    def test_poll_scheduled_runs_once_per_day_when_crossing_four_am(self) -> None:
        clock = _MutableClock(datetime(2026, 2, 27, 3, 55))
        llm = _FakeLLMClient(replies=["# 新画像A", "# 新画像B"])
        service = UserProfileRefreshService(
            db=self.db,
            llm_client=llm,
            user_profile_path=str(self.profile_path),
            agent_reloader=self._reload_agent,
            clock=clock,
            scheduled_hour=4,
        )

        clock.now = datetime(2026, 2, 27, 3, 59)
        service.poll_scheduled()
        self.assertEqual(self.reload_count, 0)

        clock.now = datetime(2026, 2, 27, 4, 0)
        service.poll_scheduled()
        self.assertEqual(self.reload_count, 1)

        clock.now = datetime(2026, 2, 27, 4, 30)
        service.poll_scheduled()
        self.assertEqual(self.reload_count, 1)

        clock.now = datetime(2026, 2, 28, 4, 1)
        service.poll_scheduled()
        self.assertEqual(self.reload_count, 2)
        self.assertEqual(llm.temperature_calls, [0.0, 0.0])

    def test_poll_scheduled_does_not_catch_up_when_started_after_four_am(self) -> None:
        clock = _MutableClock(datetime(2026, 2, 27, 4, 10))
        llm = _FakeLLMClient(replies=["# 新画像"])
        service = UserProfileRefreshService(
            db=self.db,
            llm_client=llm,
            user_profile_path=str(self.profile_path),
            agent_reloader=self._reload_agent,
            clock=clock,
            scheduled_hour=4,
        )

        clock.now = datetime(2026, 2, 27, 4, 11)
        service.poll_scheduled()

        self.assertEqual(self.reload_count, 0)
        self.assertEqual(llm.temperature_calls, [])

    def test_manual_refresh_returns_latest_profile_content(self) -> None:
        clock = _MutableClock(datetime(2026, 2, 27, 12, 0))
        llm = _FakeLLMClient(replies=["# 新画像\n- 偏好: 乌龙茶"])
        service = UserProfileRefreshService(
            db=self.db,
            llm_client=llm,
            user_profile_path=str(self.profile_path),
            agent_reloader=self._reload_agent,
            clock=clock,
            scheduled_hour=4,
        )

        result = service.run_manual_refresh()

        self.assertEqual(result, "# 新画像\n- 偏好: 乌龙茶")
        self.assertEqual(self.profile_path.read_text(encoding="utf-8"), "# 新画像\n- 偏好: 乌龙茶")
        self.assertEqual(self.reload_count, 1)
        self.assertEqual(llm.temperature_calls, [0.0])

    def test_manual_refresh_is_not_blocked_by_daily_dedup(self) -> None:
        clock = _MutableClock(datetime(2026, 2, 27, 3, 50))
        llm = _FakeLLMClient(replies=["# 定时刷新", "# 手动刷新"])
        service = UserProfileRefreshService(
            db=self.db,
            llm_client=llm,
            user_profile_path=str(self.profile_path),
            agent_reloader=self._reload_agent,
            clock=clock,
            scheduled_hour=4,
        )

        clock.now = datetime(2026, 2, 27, 4, 0)
        service.poll_scheduled()
        self.assertEqual(self.reload_count, 1)

        clock.now = datetime(2026, 2, 27, 4, 10)
        manual_result = service.run_manual_refresh()

        self.assertEqual(manual_result, "# 手动刷新")
        self.assertEqual(self.reload_count, 2)

    def test_manual_refresh_skips_when_profile_path_missing(self) -> None:
        llm = _FakeLLMClient(replies=["# 不应执行"])
        service = UserProfileRefreshService(
            db=self.db,
            llm_client=llm,
            user_profile_path=str(Path(self.tmp.name) / "missing.md"),
            agent_reloader=self._reload_agent,
        )

        result = service.run_manual_refresh()

        self.assertIn("未找到 user_profile 文件", result)
        self.assertEqual(self.reload_count, 0)
        self.assertEqual(llm.temperature_calls, [])

    def test_manual_refresh_skips_when_no_recent_turns(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute("DELETE FROM chat_history")
            conn.commit()
        finally:
            conn.close()

        llm = _FakeLLMClient(replies=["# 不应执行"])
        service = UserProfileRefreshService(
            db=self.db,
            llm_client=llm,
            user_profile_path=str(self.profile_path),
            agent_reloader=self._reload_agent,
            clock=_MutableClock(datetime(2026, 2, 27, 12, 0)),
        )

        result = service.run_manual_refresh()

        self.assertIn("暂无可用对话", result)
        self.assertEqual(self.reload_count, 0)
        self.assertEqual(llm.temperature_calls, [])

    def test_manual_refresh_keeps_file_when_llm_fails(self) -> None:
        original = self.profile_path.read_text(encoding="utf-8")
        llm = _FakeLLMClient(raises=RuntimeError("api down"))
        service = UserProfileRefreshService(
            db=self.db,
            llm_client=llm,
            user_profile_path=str(self.profile_path),
            agent_reloader=self._reload_agent,
            clock=_MutableClock(datetime(2026, 2, 27, 12, 0)),
        )

        result = service.run_manual_refresh()

        self.assertIn("调用 LLM 刷新 user_profile 失败", result)
        self.assertEqual(self.profile_path.read_text(encoding="utf-8"), original)
        self.assertEqual(self.reload_count, 0)
        self.assertEqual(llm.temperature_calls, [0.0])


if __name__ == "__main__":
    unittest.main()
