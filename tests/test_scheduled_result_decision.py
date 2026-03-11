from __future__ import annotations

import json
import logging
import unittest

from assistant_app.scheduled_result_decision import ScheduledResultDecisionRunner


class _FakeLLM:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, object]] = []

    def reply_with_tools(self, messages, *, tools, tool_choice="auto"):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        if not self._payloads:
            raise RuntimeError("no payload")
        return self._payloads.pop(0)


def _tool_payload(name: str, arguments: dict[str, object], *, call_id: str) -> dict[str, object]:
    return {
        "assistant_message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
                }
            ],
        }
    }


class ScheduledResultDecisionRunnerTest(unittest.TestCase):
    def test_run_once_returns_sendable_message_when_model_approves(self) -> None:
        llm = _FakeLLM(
            [
                _tool_payload(
                    "done",
                    {"should_send": True, "message": "今天的日报已生成，请查看。"},
                    call_id="call_done",
                )
            ]
        )
        runner = ScheduledResultDecisionRunner(
            llm_client=llm,
            max_steps=2,
            logger=logging.getLogger("test.scheduled_result_decision.approve"),
        )

        decision = runner.run_once(
            context_payload={
                "result": {
                    "task_name": "daily-report",
                    "prompt": "生成日报",
                    "final_response": "日报已完成",
                    "started_at": "2026-03-11 09:00:00",
                    "finished_at": "2026-03-11 09:00:10",
                    "duration_seconds": 10,
                }
            }
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertTrue(decision.should_send)
        self.assertEqual(decision.message, "今天的日报已生成，请查看。")
        self.assertEqual(llm.calls[0]["tool_choice"], "auto")

    def test_run_once_retries_invalid_payload_and_accepts_decline(self) -> None:
        llm = _FakeLLM(
            [
                _tool_payload("done", {"should_send": True, "message": ""}, call_id="call_invalid"),
                _tool_payload("done", {"should_send": False, "message": "ignored"}, call_id="call_decline"),
            ]
        )
        runner = ScheduledResultDecisionRunner(
            llm_client=llm,
            max_steps=3,
            logger=logging.getLogger("test.scheduled_result_decision.retry"),
        )

        decision = runner.run_once(
            context_payload={
                "result": {
                    "task_name": "daily-report",
                    "prompt": "生成日报",
                    "final_response": "日报已完成",
                    "started_at": "2026-03-11 09:00:00",
                    "finished_at": "2026-03-11 09:00:10",
                    "duration_seconds": 10,
                }
            }
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertFalse(decision.should_send)
        self.assertEqual(decision.message, "")

    def test_run_once_returns_none_when_model_never_calls_done(self) -> None:
        llm = _FakeLLM(
            [
                {"assistant_message": {"role": "assistant", "content": "plain text", "tool_calls": []}},
            ]
        )
        runner = ScheduledResultDecisionRunner(
            llm_client=llm,
            max_steps=1,
            logger=logging.getLogger("test.scheduled_result_decision.max_steps"),
        )

        decision = runner.run_once(
            context_payload={
                "result": {
                    "task_name": "daily-report",
                    "prompt": "生成日报",
                    "final_response": "日报已完成",
                    "started_at": "2026-03-11 09:00:00",
                    "finished_at": "2026-03-11 09:00:10",
                    "duration_seconds": 10,
                }
            }
        )

        self.assertIsNone(decision)


if __name__ == "__main__":
    unittest.main()
