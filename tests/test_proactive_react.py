from __future__ import annotations

import json
import logging
import unittest

from assistant_app.proactive_react import PROACTIVE_REACT_SYSTEM_PROMPT, ProactiveReactRunner
from assistant_app.schemas.proactive import ProactivePromptPayload


class _FakeLLM:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, object]] = []

    def reply_with_tools(self, messages, *, tools, tool_choice="auto"):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        if not self._payloads:
            raise RuntimeError("no payload")
        return self._payloads.pop(0)


class _FakeToolExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, *, tool_name: str, arguments: dict[str, object]) -> str:
        self.calls.append((tool_name, dict(arguments)))
        return json.dumps({"ok": True, "tool": tool_name}, ensure_ascii=False)


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


class ProactiveReactRunnerTest(unittest.TestCase):
    def test_system_prompt_does_not_include_disallowed_tool_names(self) -> None:
        lowered = PROACTIVE_REACT_SYSTEM_PROMPT.lower()
        self.assertNotIn("ask_user", lowered)
        self.assertNotIn("schedule_add", lowered)

    def test_run_once_executes_tool_then_done(self) -> None:
        llm = _FakeLLM(
            [
                _tool_payload("schedule_list", {}, call_id="call_1"),
                _tool_payload(
                    "done",
                    {
                        "score": 81,
                        "message": "你明早有会议，建议现在准备三点要点。",
                        "reason": "存在未来24小时关键日程。",
                    },
                    call_id="call_2",
                ),
            ]
        )
        tools = _FakeToolExecutor()
        runner = ProactiveReactRunner(
            llm_client=llm,
            tool_executor=tools,
            max_steps=5,
            logger=logging.getLogger("test.proactive_react.success"),
        )

        decision = runner.run_once(
            context_payload={
                "policy": {"score_threshold": 80},
                "user_profile": {"content": "用户偏好：晨会前提醒"},
                "internal_context": {"schedules": [], "recent_chat_turns": []},
            }
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.score, 81)
        self.assertEqual(decision.reason, "存在未来24小时关键日程。")
        self.assertEqual(tools.calls, [("schedule_list", {})])
        self.assertGreaterEqual(len(llm.calls), 1)
        first_messages = llm.calls[0]["messages"]
        self.assertIsInstance(first_messages, list)
        self.assertIn("用户偏好：晨会前提醒", first_messages[1]["content"])

    def test_run_once_accepts_prompt_payload_model(self) -> None:
        llm = _FakeLLM(
            [
                _tool_payload(
                    "done",
                    {
                        "score": 85,
                        "message": "你明早有重要事项，建议今晚提前准备。",
                        "reason": "用户画像显示偏好提前提醒。",
                    },
                    call_id="call_model",
                ),
            ]
        )
        runner = ProactiveReactRunner(
            llm_client=llm,
            tool_executor=_FakeToolExecutor(),
            max_steps=2,
            logger=logging.getLogger("test.proactive_react.model_payload"),
        )

        decision = runner.run_once(
            context_payload=ProactivePromptPayload(
                policy={"score_threshold": 80},
                user_profile={"loaded": True, "content": "用户偏好：重要事项提前提醒"},
            )
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.score, 85)
        self.assertEqual(decision.reason, "用户画像显示偏好提前提醒。")

    def test_run_once_ignores_invalid_done_and_retries(self) -> None:
        llm = _FakeLLM(
            [
                _tool_payload("done", {"score": 80, "message": "", "reason": ""}, call_id="call_1"),
                _tool_payload("done", {"score": 20, "message": "", "reason": "当前无需提醒"}, call_id="call_2"),
            ]
        )
        runner = ProactiveReactRunner(
            llm_client=llm,
            tool_executor=_FakeToolExecutor(),
            max_steps=3,
            logger=logging.getLogger("test.proactive_react.retry"),
        )

        decision = runner.run_once(context_payload={"policy": {"score_threshold": 80}, "user_profile": {"content": ""}})

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.score, 20)
        self.assertEqual(decision.reason, "当前无需提醒")

    def test_run_once_returns_none_when_max_steps_reached(self) -> None:
        llm = _FakeLLM([_tool_payload("schedule_list", {}, call_id="call_1")])
        runner = ProactiveReactRunner(
            llm_client=llm,
            tool_executor=_FakeToolExecutor(),
            max_steps=1,
            logger=logging.getLogger("test.proactive_react.max_steps"),
        )

        decision = runner.run_once(context_payload={"user_profile": {"content": ""}})

        self.assertIsNone(decision)

    def test_run_once_allows_internet_search_tool(self) -> None:
        llm = _FakeLLM(
            [
                _tool_payload("internet_search", {"query": "OpenAI Responses API"}, call_id="call_1"),
                _tool_payload("done", {"score": 30, "message": "", "reason": "无需提醒"}, call_id="call_2"),
            ]
        )
        tools = _FakeToolExecutor()
        runner = ProactiveReactRunner(
            llm_client=llm,
            tool_executor=tools,
            max_steps=3,
            logger=logging.getLogger("test.proactive_react.internet_search"),
        )

        decision = runner.run_once(context_payload={"policy": {"score_threshold": 80}, "user_profile": {"content": ""}})

        self.assertIsNotNone(decision)
        self.assertEqual(tools.calls[0], ("internet_search", {"query": "OpenAI Responses API"}))

    def test_run_once_rejects_disallowed_action_name(self) -> None:
        llm = _FakeLLM(
            [
                _tool_payload("ask_user", {"question": "请确认"}, call_id="call_1"),
                _tool_payload("done", {"score": 10, "message": "", "reason": "等待后续"}, call_id="call_2"),
            ]
        )
        tools = _FakeToolExecutor()
        runner = ProactiveReactRunner(
            llm_client=llm,
            tool_executor=tools,
            max_steps=3,
            logger=logging.getLogger("test.proactive_react.disallowed"),
        )

        decision = runner.run_once(context_payload={"policy": {"score_threshold": 80}, "user_profile": {"content": ""}})

        self.assertIsNotNone(decision)

    def test_run_once_rejects_out_of_range_score(self) -> None:
        llm = _FakeLLM(
            [
                _tool_payload("done", {"score": 101, "message": "提醒", "reason": "过高"}, call_id="call_1"),
                _tool_payload("done", {"score": 0, "message": "", "reason": "无需提醒"}, call_id="call_2"),
            ]
        )
        tools = _FakeToolExecutor()
        runner = ProactiveReactRunner(
            llm_client=llm,
            tool_executor=tools,
            max_steps=3,
            logger=logging.getLogger("test.proactive_react.invalid_score"),
        )

        decision = runner.run_once(context_payload={"policy": {"score_threshold": 80}, "user_profile": {"content": ""}})

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.score, 0)
        self.assertEqual(tools.calls, [])


if __name__ == "__main__":
    unittest.main()
