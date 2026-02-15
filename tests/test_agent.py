from __future__ import annotations

import json
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


def _intent_json(intent: str, **overrides: object) -> str:
    payload: dict[str, object | None] = {
        "intent": intent,
        "todo_content": None,
        "todo_tag": None,
        "todo_view": None,
        "todo_priority": None,
        "todo_due_time": None,
        "todo_remind_time": None,
        "todo_id": None,
        "schedule_id": None,
        "event_time": None,
        "title": None,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


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
        self.assertIn("--priority <>=0>", result)
        self.assertIn("/todo search <关键词>", result)
        self.assertIn("/view list", result)
        self.assertIn("/todo update", result)
        self.assertIn("/todo delete", result)
        self.assertIn("/schedule list", result)
        self.assertIn("/schedule update", result)
        self.assertIn("/schedule delete", result)

    def test_slash_commands_without_llm(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        add_resp = agent.handle_input("/todo add 买牛奶")
        self.assertIn("已添加待办", add_resp)
        self.assertIn("标签:default", add_resp)
        self.assertIn("优先级:0", add_resp)

        list_resp = agent.handle_input("/todo list")
        self.assertIn("买牛奶", list_resp)
        self.assertIn("| 标签 |", list_resp)
        self.assertIn("优先级", list_resp)
        self.assertIn("| default |", list_resp)
        self.assertIn("创建时间", list_resp)
        self.assertIn("| - | - | - |", list_resp)

        search_resp = agent.handle_input("/todo search 牛奶")
        self.assertIn("搜索结果", search_resp)
        self.assertIn("买牛奶", search_resp)

        done_resp = agent.handle_input("/todo done 1")
        self.assertIn("已完成", done_resp)
        self.assertIn("完成时间:", done_resp)

    def test_slash_todo_tag_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        resp = agent.handle_input("/todo add 复盘周报 --tag work")
        self.assertIn("标签:work", resp)

        filtered = agent.handle_input("/todo list --tag work")
        self.assertIn("复盘周报", filtered)
        self.assertIn("(标签: work)", filtered)
        self.assertIn("| work |", filtered)

        invalid = agent.handle_input("/todo list --tag")
        self.assertIn("用法", invalid)

    def test_slash_todo_search_with_tag(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/todo add 修复登录 --tag work")
        agent.handle_input("/todo add 买牛奶 --tag life")

        result = agent.handle_input("/todo search 修复 --tag work")
        self.assertIn("关键词: 修复", result)
        self.assertIn("标签: work", result)
        self.assertIn("修复登录", result)
        self.assertNotIn("买牛奶", result)

        invalid = agent.handle_input("/todo search --tag work")
        self.assertIn("用法", invalid)

    def test_slash_todo_view_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/todo add 今天复盘 --tag work --due 2026-02-15 18:00")
        agent.handle_input("/todo add 明天写周报 --tag work --due 2026-02-16 10:00")
        agent.handle_input("/todo add 收件箱任务 --tag life")

        view_list = agent.handle_input("/view list")
        self.assertIn("today", view_list)
        self.assertIn("upcoming", view_list)

        today = agent.handle_input("/view today")
        self.assertIn("今天复盘", today)
        self.assertNotIn("明天写周报", today)

        upcoming = agent.handle_input("/todo list --view upcoming")
        self.assertIn("明天写周报", upcoming)
        self.assertIn("视图: upcoming", upcoming)

        inbox = agent.handle_input("/view inbox --tag life")
        self.assertIn("收件箱任务", inbox)
        self.assertIn("标签: life", inbox)

        invalid = agent.handle_input("/view week")
        self.assertIn("用法", invalid)

    def test_slash_todo_full_crud_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        add_resp = agent.handle_input("/todo add 写周报 --tag work")
        self.assertIn("已添加待办 #1", add_resp)

        get_resp = agent.handle_input("/todo get 1")
        self.assertIn("待办详情:", get_resp)
        self.assertIn("| work | 0 | 写周报 |", get_resp)

        update_resp = agent.handle_input("/todo update 1 写周报v2 --tag review")
        self.assertIn("已更新待办 #1 [标签:review]: 写周报v2", update_resp)

        update_without_tag = agent.handle_input("/todo update 1 写周报最终版")
        self.assertIn("已更新待办 #1 [标签:review]: 写周报最终版", update_without_tag)

        delete_resp = agent.handle_input("/todo delete 1")
        self.assertIn("待办 #1 已删除", delete_resp)

        missing_resp = agent.handle_input("/todo get 1")
        self.assertIn("未找到待办 #1", missing_resp)

    def test_slash_todo_due_and_remind_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        add_resp = agent.handle_input("/todo add 准备发布 --tag work --due 2026-02-25 18:00 --remind 2026-02-25 17:30")
        self.assertIn("截止:2026-02-25 18:00", add_resp)
        self.assertIn("提醒:2026-02-25 17:30", add_resp)

        get_resp = agent.handle_input("/todo get 1")
        self.assertIn("创建时间", get_resp)
        self.assertIn("完成时间", get_resp)
        self.assertIn("2026-02-25 18:00", get_resp)
        self.assertIn("2026-02-25 17:30", get_resp)

        list_resp = agent.handle_input("/todo list --tag work")
        self.assertIn("截止时间", list_resp)
        self.assertIn("提醒时间", list_resp)
        self.assertIn("2026-02-25 18:00", list_resp)
        self.assertIn("2026-02-25 17:30", list_resp)
        self.assertIn("创建时间", list_resp)

        invalid_resp = agent.handle_input("/todo add 只有提醒 --remind 2026-02-25 09:00")
        self.assertIn("用法", invalid_resp)

    def test_slash_todo_priority_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        add_resp = agent.handle_input("/todo add 写季度总结 --tag work --priority 2")
        self.assertIn("优先级:2", add_resp)

        get_resp = agent.handle_input("/todo get 1")
        self.assertIn("| work | 2 | 写季度总结 |", get_resp)

        update_resp = agent.handle_input("/todo update 1 写季度总结v2 --priority 0")
        self.assertIn("优先级:0", update_resp)

        list_resp = agent.handle_input("/todo list")
        self.assertIn("| 1 | 待办 | work | 0 | 写季度总结v2 |", list_resp)

        invalid_add = agent.handle_input("/todo add 非法优先级 --priority -1")
        self.assertIn("用法", invalid_add)

        invalid_update = agent.handle_input("/todo update 1 非法更新 --priority -3")
        self.assertIn("用法", invalid_update)

    def test_slash_schedule_full_crud_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        add_resp = agent.handle_input("/schedule add 2026-02-20 09:30 站会")
        self.assertIn("已添加日程 #1", add_resp)

        get_resp = agent.handle_input("/schedule get 1")
        self.assertIn("日程详情:", get_resp)
        self.assertIn("| 1 | 2026-02-20 09:30 | 站会 |", get_resp)

        update_resp = agent.handle_input("/schedule update 1 2026-02-21 10:00 复盘会")
        self.assertIn("已更新日程 #1: 2026-02-21 10:00 复盘会", update_resp)

        delete_resp = agent.handle_input("/schedule delete 1")
        self.assertIn("日程 #1 已删除", delete_resp)

        missing_resp = agent.handle_input("/schedule get 1")
        self.assertIn("未找到日程 #1", missing_resp)

    def test_nl_todo_flow_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("todo_add", todo_content="买牛奶", todo_tag="life", todo_priority=1),
                _intent_json("todo_list", todo_tag="life"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("修 bug", tag="work")

        add_resp = agent.handle_input("帮我记一个待办，买牛奶")
        self.assertIn("已添加待办", add_resp)

        list_resp = agent.handle_input("看一下我的待办")
        self.assertIn("买牛奶", list_resp)
        self.assertIn("| life |", list_resp)
        self.assertIn("| 1 | 买牛奶 |", list_resp)
        self.assertNotIn("修 bug", list_resp)
        self.assertEqual(len(fake_llm.calls), 2)

    def test_nl_schedule_flow_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("schedule_add", event_time="2026-02-20 09:30", title="周会"),
                _intent_json("schedule_list"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        add_resp = agent.handle_input("明天早上九点半加个周会")
        self.assertIn("已添加日程", add_resp)

        list_resp = agent.handle_input("看一下日程")
        self.assertIn("周会", list_resp)
        self.assertEqual(len(fake_llm.calls), 2)

    def test_nl_todo_search_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("todo_search", todo_content="牛奶", todo_tag="life"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("买牛奶", tag="life")
        self.db.add_todo("写周报", tag="work")

        result = agent.handle_input("帮我找一下life里和牛奶有关的待办")
        self.assertIn("搜索结果", result)
        self.assertIn("买牛奶", result)
        self.assertNotIn("写周报", result)
        self.assertEqual(len(fake_llm.calls), 1)

    def test_nl_todo_view_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("todo_view", todo_view="today"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("今天复盘", due_at="2026-02-15 18:00")
        self.db.add_todo("明天开会", due_at="2026-02-16 09:30")

        result = agent.handle_input("看一下今天待办")
        self.assertIn("今天复盘", result)
        self.assertNotIn("明天开会", result)
        self.assertEqual(len(fake_llm.calls), 1)

    def test_nl_todo_update_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json(
                    "todo_update",
                    todo_content="买牛奶和面包",
                    todo_tag="life",
                    todo_priority=2,
                    todo_due_time="2026-02-26 20:00",
                    todo_remind_time="2026-02-26 19:30",
                    todo_id=1,
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("买牛奶", tag="default")

        response = agent.handle_input("把待办1改成买牛奶和面包，标签life")
        self.assertIn("已更新待办 #1", response)
        self.assertIn("[标签:life]", response)

        todo = self.db.get_todo(1)
        self.assertIsNotNone(todo)
        assert todo is not None
        self.assertEqual(todo.content, "买牛奶和面包")
        self.assertEqual(todo.tag, "life")
        self.assertEqual(todo.priority, 2)
        self.assertEqual(todo.due_at, "2026-02-26 20:00")
        self.assertEqual(todo.remind_at, "2026-02-26 19:30")

    def test_nl_todo_update_only_remind_uses_existing_due(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json(
                    "todo_update",
                    todo_content="准备周报",
                    todo_tag="work",
                    todo_remind_time="2026-02-26 19:30",
                    todo_id=1,
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("准备周报", tag="work", due_at="2026-02-26 20:00")

        response = agent.handle_input("把待办1提醒时间改成晚上7点半")
        self.assertIn("已更新待办 #1", response)
        self.assertEqual(len(fake_llm.calls), 1)

        todo = self.db.get_todo(1)
        self.assertIsNotNone(todo)
        assert todo is not None
        self.assertEqual(todo.due_at, "2026-02-26 20:00")
        self.assertEqual(todo.remind_at, "2026-02-26 19:30")

    def test_nl_schedule_delete_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("schedule_delete", schedule_id=1),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_schedule("周会", "2026-02-20 09:30")

        response = agent.handle_input("把日程1删掉")
        self.assertIn("日程 #1 已删除", response)
        self.assertIsNone(self.db.get_schedule(1))

    def test_chat_path_requires_intent_then_chat(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("chat"),
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

    def test_intent_missing_fields_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("todo_done"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("把待办完成")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_action_missing_params_retry_then_success(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("todo_done"),
                _intent_json("todo_done", todo_id=1),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("修复登录问题")

        response = agent.handle_input("把这个待办标记完成")
        self.assertIn("待办 #1 已完成", response)
        self.assertEqual(len(fake_llm.calls), 2)

    def test_schedule_delete_missing_id_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("schedule_delete"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("删掉这个日程")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_invalid_intent_json_returns_service_unavailable(self) -> None:
        fake_llm = FakeLLMClient(responses=["不是json", "还是不是json", "依然不是json"])
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("今天天气如何")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertIn("/todo", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_non_json_model_text_is_treated_as_failure(self) -> None:
        fake_llm = FakeLLMClient(responses=["我先快速扫一遍待办项并给你汇总清单。"])
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("看一下全部待办")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_invalid_json_retry_then_success(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                "不是json",
                _intent_json("todo_add", todo_content="明天早上10 :00吃早饭"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("增加一个测试待办，明天早上10 :00吃早饭")
        self.assertIn("已添加待办", response)
        self.assertIn("明天早上10 :00吃早饭", response)
        self.assertEqual(len(fake_llm.calls), 2)

        list_resp = agent.handle_input("/todo list")
        self.assertIn("明天早上10 :00吃早饭", list_resp)

    def test_todo_add_with_remind_without_due_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json(
                    "todo_add",
                    todo_content="准备周报",
                    todo_tag="work",
                    todo_remind_time="2026-02-25 09:00",
                )
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("提醒我写周报")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_todo_add_with_negative_priority_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("todo_add", todo_content="整理文档", todo_priority=-2),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("添加一个待办，优先级负数")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_todo_search_missing_keyword_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("todo_search"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("帮我搜索待办")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_todo_view_missing_view_name_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("todo_view"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("看一下待办视图")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

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
        payload = _try_parse_json('```json\n{"intent":"todo_list"}\n```')
        self.assertIsNone(payload)

    def test_extract_args_from_text(self) -> None:
        todo_add = _extract_args_from_text("把“买牛奶”加进去。", intent="todo_add")
        self.assertEqual(todo_add["todo_content"], "买牛奶")

        todo_done = _extract_args_from_text("先完成待办 12。", intent="todo_done")
        self.assertEqual(todo_done["todo_id"], 12)

        schedule = _extract_args_from_text("已为你添加日程：2026-02-20 09:30，事项：周会。", intent="schedule_add")
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
