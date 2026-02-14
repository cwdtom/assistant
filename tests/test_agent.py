from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assistant_app.agent import (
    AssistantAgent,
    _extract_args_from_text,
    _extract_intent_label,
    _extract_todo_content,
    _strip_think_blocks,
    _try_parse_json,
)
from assistant_app.db import AssistantDB


class FakeLLMClient:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[list[dict[str, str]]] = []

    def reply(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        idx = len(self.calls) - 1
        if idx < len(self.responses):
            return self.responses[idx]
        if self.responses:
            return self.responses[-1]
        return '{"intent":"chat"}'


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

    def test_slash_commands_without_llm(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        add_resp = agent.handle_input("/todo add 买牛奶")
        self.assertIn("已添加待办", add_resp)
        self.assertIn("标签:default", add_resp)

        list_resp = agent.handle_input("/todo list")
        self.assertIn("买牛奶", list_resp)
        self.assertIn("[default]", list_resp)

        done_resp = agent.handle_input("/todo done 1")
        self.assertIn("已完成", done_resp)

    def test_slash_todo_tag_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        resp = agent.handle_input("/todo add 复盘周报 --tag work")
        self.assertIn("标签:work", resp)

        filtered = agent.handle_input("/todo list --tag work")
        self.assertIn("复盘周报", filtered)
        self.assertIn("(标签: work)", filtered)

        invalid = agent.handle_input("/todo list --tag")
        self.assertIn("用法", invalid)

    def test_nl_todo_flow_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                '{"intent":"todo_add","todo_content":"买牛奶","todo_tag":"life","todo_id":null,"event_time":null,"title":null}',
                '{"intent":"todo_list","todo_content":null,"todo_tag":"life","todo_id":null,"event_time":null,"title":null}',
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("修 bug", tag="work")

        add_resp = agent.handle_input("帮我记一个待办，买牛奶")
        self.assertIn("已添加待办", add_resp)

        list_resp = agent.handle_input("看一下我的待办")
        self.assertIn("买牛奶", list_resp)
        self.assertIn("[life]", list_resp)
        self.assertNotIn("修 bug", list_resp)
        self.assertEqual(len(fake_llm.calls), 2)

    def test_nl_schedule_flow_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                '{"intent":"schedule_add","todo_content":null,"todo_id":null,"event_time":"2026-02-20 09:30","title":"周会"}',
                '{"intent":"schedule_list","todo_content":null,"todo_id":null,"event_time":null,"title":null}',
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        add_resp = agent.handle_input("明天早上九点半加个周会")
        self.assertIn("已添加日程", add_resp)

        list_resp = agent.handle_input("看一下日程")
        self.assertIn("周会", list_resp)
        self.assertEqual(len(fake_llm.calls), 2)

    def test_chat_path_requires_intent_then_chat(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                '{"intent":"chat","todo_content":null,"todo_id":null,"event_time":null,"title":null}',
                "建议先处理高优先级事项",
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        self.db.add_todo("修复 bug")
        response = agent.handle_input("今天怎么安排")

        self.assertIn("高优先级", response)
        # First call: analyze intent JSON; second call: chat response.
        self.assertEqual(len(fake_llm.calls), 2)

        history = self.db.recent_messages(limit=2)
        self.assertEqual(history[0].role, "user")
        self.assertEqual(history[1].role, "assistant")

    def test_intent_missing_fields_gives_hint(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                '{"intent":"todo_done","todo_content":null,"todo_id":null,"event_time":null,"title":null}',
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("把待办完成")
        self.assertIn("缺少编号", response)

    def test_invalid_intent_json_returns_service_unavailable(self) -> None:
        fake_llm = FakeLLMClient(responses=["不是json", "还是不是json", "依然不是json"])
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("今天天气如何")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertIn("/todo", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_non_json_model_text_can_still_route_todo_list(self) -> None:
        fake_llm = FakeLLMClient(
            responses=["我先快速扫一遍待办项并给你汇总清单。"]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("买牛奶")

        response = agent.handle_input("看一下全部待办")
        self.assertIn("待办列表", response)
        self.assertIn("买牛奶", response)
        self.assertEqual(len(fake_llm.calls), 1)

    def test_invalid_json_retry_then_success(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                "不是json",
                "```json\n{\"intent\":\"todo_add\",\"todo_content\":\"明天早上10 :00吃早饭\",\"todo_id\":null,\"event_time\":null,\"title\":null}\n```",
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("增加一个测试待办，明天早上10 :00吃早饭")
        self.assertIn("已添加待办", response)
        self.assertIn("明天早上10 :00吃早饭", response)
        self.assertEqual(len(fake_llm.calls), 2)

        list_resp = agent.handle_input("/todo list")
        self.assertIn("明天早上10 :00吃早饭", list_resp)

    def test_chat_without_llm(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        result = agent.handle_input("今天要做什么")
        self.assertIn("未配置 LLM", result)

    def test_strip_think_blocks(self) -> None:
        text = "<think>abc</think>最终答案"
        self.assertEqual(_strip_think_blocks(text), "最终答案")

    def test_extract_intent_label(self) -> None:
        self.assertEqual(_extract_intent_label("todo_list"), "todo_list")
        self.assertEqual(_extract_intent_label("intent: schedule_add"), "schedule_add")
        self.assertEqual(_extract_intent_label('{"intent":"chat"}'), "chat")
        self.assertEqual(_extract_intent_label("当前待办记录如下："), "todo_list")
        self.assertIsNone(_extract_intent_label("你好"))

    def test_try_parse_json_from_fenced_block(self) -> None:
        payload = _try_parse_json("```json\n{\"intent\":\"todo_list\"}\n```")
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["intent"], "todo_list")

    def test_extract_args_from_text(self) -> None:
        todo_add = _extract_args_from_text("把“买牛奶”加进去。", intent="todo_add")
        self.assertEqual(todo_add["todo_content"], "买牛奶")

        todo_done = _extract_args_from_text("先完成待办 12。", intent="todo_done")
        self.assertEqual(todo_done["todo_id"], 12)

        schedule = _extract_args_from_text(
            "已为你添加日程：2026-02-20 09:30，事项：周会。", intent="schedule_add"
        )
        self.assertEqual(schedule["event_time"], "2026-02-20 09:30")
        self.assertEqual(schedule["title"], "周会")

    def test_extract_todo_content(self) -> None:
        self.assertEqual(
            _extract_todo_content("增加一个测试待办，明天早上10 :00吃早饭"),
            "明天早上10 :00吃早饭",
        )
        self.assertIsNone(_extract_todo_content("我先看看有没有 .ics 文件"))

if __name__ == "__main__":
    unittest.main()
