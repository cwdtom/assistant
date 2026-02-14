from __future__ import annotations

import io
import time
import unittest

from assistant_app.cli import _handle_input_with_feedback, _should_show_waiting


class _FakeAgent:
    def __init__(self, llm_enabled: bool, delay: float = 0.0) -> None:
        self.llm_client = object() if llm_enabled else None
        self.delay = delay

    def handle_input(self, user_input: str) -> str:
        if self.delay:
            time.sleep(self.delay)
        return f"echo:{user_input}"


class CLIFeedbackTest(unittest.TestCase):
    def test_should_show_waiting_for_natural_language_with_llm(self) -> None:
        agent = _FakeAgent(llm_enabled=True)
        self.assertTrue(_should_show_waiting(agent, "看一下全部待办"))
        self.assertFalse(_should_show_waiting(agent, "/todo list"))
        self.assertFalse(_should_show_waiting(agent, ""))

    def test_should_not_show_waiting_without_llm(self) -> None:
        agent = _FakeAgent(llm_enabled=False)
        self.assertFalse(_should_show_waiting(agent, "今天怎么安排"))

    def test_handle_input_with_feedback_renders_waiting_message(self) -> None:
        agent = _FakeAgent(llm_enabled=True, delay=0.03)
        stream = io.StringIO()

        result = _handle_input_with_feedback(agent, "看一下全部待办", stream=stream, interval=0.005)

        self.assertEqual(result, "echo:看一下全部待办")
        self.assertIn("正在思考", stream.getvalue())

    def test_handle_input_with_feedback_skips_waiting_for_command(self) -> None:
        agent = _FakeAgent(llm_enabled=True)
        stream = io.StringIO()

        result = _handle_input_with_feedback(agent, "/todo list", stream=stream)

        self.assertEqual(result, "echo:/todo list")
        self.assertEqual(stream.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
