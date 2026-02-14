from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assistant_app.agent import AssistantAgent
from assistant_app.db import AssistantDB


class FakeLLMClient:
    def __init__(self, answer: str = "默认回复") -> None:
        self.answer = answer
        self.calls: list[list[dict[str, str]]] = []

    def reply(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        return self.answer


class AssistantAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp.name) / "assistant_test.db")
        self.db = AssistantDB(db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_help_command(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        result = agent.handle_input("/help")

        self.assertIn("/todo add", result)
        self.assertIn("/schedule list", result)

    def test_todo_add_list_done(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        add_resp = agent.handle_input("/todo add 买牛奶")
        self.assertIn("已添加待办", add_resp)

        list_resp = agent.handle_input("/todo list")
        self.assertIn("买牛奶", list_resp)

        done_resp = agent.handle_input("/todo done 1")
        self.assertIn("已完成", done_resp)

    def test_schedule_add_and_list(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        invalid = agent.handle_input("/schedule add 2026-02-20 计划")
        self.assertIn("用法", invalid)

        valid = agent.handle_input("/schedule add 2026-02-20 09:30 周会")
        self.assertIn("已添加日程", valid)

        listed = agent.handle_input("/schedule list")
        self.assertIn("周会", listed)

    def test_chat_without_llm(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        result = agent.handle_input("今天要做什么")
        self.assertIn("未配置 LLM", result)

    def test_chat_with_llm(self) -> None:
        fake_llm = FakeLLMClient(answer="建议先处理高优先级事项")
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        self.db.add_todo("修复 bug")
        response = agent.handle_input("今天怎么安排")

        self.assertIn("高优先级", response)
        self.assertEqual(len(fake_llm.calls), 1)

        history = self.db.recent_messages(limit=2)
        self.assertEqual(history[0].role, "user")
        self.assertEqual(history[1].role, "assistant")


if __name__ == "__main__":
    unittest.main()
