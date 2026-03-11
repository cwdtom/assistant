from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant_app.agent import AssistantAgent
from assistant_app.db import AssistantDB


class UserProfileToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = AssistantDB(str(Path(self.tmp.name) / "assistant_test.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_user_profile_tool_get_reads_existing_file(self) -> None:
        profile_file = Path(self.tmp.name) / "user_profile.md"
        profile_file.write_text("昵称: 凛\n偏好: 先结论", encoding="utf-8")
        agent = self._build_agent(user_profile_path="user_profile.md")

        observation = agent._execute_planner_tool(
            action_tool="user_profile",
            action_input='{"action":"get"}',
        )

        self.assertTrue(observation.ok)
        self.assertIn("当前 user_profile 内容", observation.result)
        self.assertIn("昵称: 凛", observation.result)

    def test_user_profile_tool_get_returns_empty_when_file_missing(self) -> None:
        agent = self._build_agent(user_profile_path="profiles/missing.md")

        observation = agent._execute_planner_tool(
            action_tool="user_profile",
            action_input='{"action":"get"}',
        )

        self.assertTrue(observation.ok)
        self.assertEqual("当前 user_profile 为空。", observation.result)

    def test_user_profile_tool_overwrite_creates_file_and_parent_dirs_and_reloads_runtime(self) -> None:
        agent = self._build_agent(user_profile_path="profiles/me.md")
        profile_file = Path(self.tmp.name) / "profiles" / "me.md"

        observation = agent._execute_planner_tool(
            action_tool="user_profile",
            action_input='{"action":"overwrite","content":"昵称: 凛"}',
        )

        self.assertTrue(observation.ok)
        self.assertTrue(profile_file.exists())
        self.assertEqual("昵称: 凛", profile_file.read_text(encoding="utf-8"))
        self.assertEqual("昵称: 凛", agent._serialize_user_profile())
        self.assertIn("已覆盖 user_profile", observation.result)

    def test_user_profile_tool_overwrite_empty_string_clears_runtime_profile(self) -> None:
        profile_file = Path(self.tmp.name) / "user_profile.md"
        profile_file.write_text("偏好: 咖啡", encoding="utf-8")
        agent = self._build_agent(user_profile_path="user_profile.md")

        observation = agent._execute_planner_tool(
            action_tool="user_profile",
            action_input='{"action":"overwrite","content":""}',
        )

        self.assertTrue(observation.ok)
        self.assertEqual("", profile_file.read_text(encoding="utf-8"))
        self.assertIsNone(agent._serialize_user_profile())
        self.assertEqual("已清空 user_profile。", observation.result)

    def test_user_profile_tool_rejects_empty_path(self) -> None:
        agent = self._build_agent(user_profile_path="")

        observation = agent._execute_planner_tool(
            action_tool="user_profile",
            action_input='{"action":"get"}',
        )

        self.assertFalse(observation.ok)
        self.assertEqual("user_profile.path 未配置。", observation.result)

    def test_user_profile_tool_logs_done_and_failed_events(self) -> None:
        profile_file = Path(self.tmp.name) / "user_profile.md"
        profile_file.write_text("偏好: 红茶", encoding="utf-8")
        agent = self._build_agent(user_profile_path="user_profile.md")

        with self.assertLogs("assistant_app.app", level="INFO") as captured_done:
            observation = agent._execute_planner_tool(
                action_tool="user_profile",
                action_input='{"action":"get"}',
            )
        self.assertTrue(observation.ok)
        merged_done = "\n".join(captured_done.output)
        self.assertIn("planner_tool_user_profile_start", merged_done)
        self.assertIn("planner_tool_user_profile_done", merged_done)

        with patch.object(Path, "write_text", side_effect=OSError("boom")):
            with self.assertLogs("assistant_app.app", level="INFO") as captured_failed:
                failed = agent._execute_planner_tool(
                    action_tool="user_profile",
                    action_input='{"action":"overwrite","content":"偏好: 茶"}',
                )
        self.assertFalse(failed.ok)
        self.assertIn("user_profile 工具执行失败", failed.result)
        merged_failed = "\n".join(captured_failed.output)
        self.assertIn("planner_tool_user_profile_start", merged_failed)
        self.assertIn("planner_tool_user_profile_failed", merged_failed)

    def _build_agent(self, *, user_profile_path: str) -> AssistantAgent:
        with patch("assistant_app.agent.PROJECT_ROOT", Path(self.tmp.name)):
            return AssistantAgent(
                db=self.db,
                llm_client=None,
                user_profile_path=user_profile_path,
            )


if __name__ == "__main__":
    unittest.main()
