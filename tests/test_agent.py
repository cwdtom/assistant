from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from assistant_app.agent import (
    AssistantAgent,
    _strip_think_blocks,
    _try_parse_json,
)
from assistant_app.db import AssistantDB
from assistant_app.search import SearchResult


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
        "schedule_repeat_interval_minutes": None,
        "schedule_repeat_times": None,
        "schedule_duration_minutes": None,
        "schedule_view": None,
        "schedule_view_date": None,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _planner_continue(tool: str, action_input: str, plan: list[str] | None = None) -> str:
    payload = {
        "status": "continue",
        "plan": plan or ["执行下一步"],
        "next_action": {"tool": tool, "input": action_input},
        "response": None,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _planner_done(response: str, plan: list[str] | None = None) -> str:
    payload = {
        "status": "done",
        "plan": plan or ["完成目标"],
        "next_action": None,
        "response": response,
    }
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


class FakeSearchProvider:
    def __init__(self, results: list[SearchResult] | None = None, raises: Exception | None = None) -> None:
        self.results = results or []
        self.raises = raises
        self.queries: list[tuple[str, int]] = []

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        self.queries.append((query, top_k))
        if self.raises is not None:
            raise self.raises
        return self.results[:top_k]


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
        self.assertIn("/schedule repeat", result)
        self.assertIn("/schedule delete", result)
        self.assertIn("--duration <>=1>", result)
        self.assertIn("--interval <>=1>", result)

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
        self.assertIn("(60 分钟)", add_resp)

        get_resp = agent.handle_input("/schedule get 1")
        self.assertIn("日程详情:", get_resp)
        self.assertIn("| 时长(分钟) |", get_resp)
        self.assertIn("重复间隔(分钟)", get_resp)
        self.assertIn("重复次数", get_resp)
        self.assertIn("重复启用", get_resp)
        self.assertIn("| 1 | 2026-02-20 09:30 | 60 | 站会 |", get_resp)

        update_resp = agent.handle_input("/schedule update 1 2026-02-21 10:00 复盘会")
        self.assertIn("已更新日程 #1: 2026-02-21 10:00 复盘会 (60 分钟)", update_resp)

        update_duration_resp = agent.handle_input("/schedule update 1 2026-02-21 11:00 复盘会 --duration 45")
        self.assertIn("已更新日程 #1: 2026-02-21 11:00 复盘会 (45 分钟)", update_duration_resp)
        item = self.db.get_schedule(1)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.duration_minutes, 45)

        delete_resp = agent.handle_input("/schedule delete 1")
        self.assertIn("日程 #1 已删除", delete_resp)

        missing_resp = agent.handle_input("/schedule get 1")
        self.assertIn("未找到日程 #1", missing_resp)

    def test_slash_schedule_repeat_add_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        add_resp = agent.handle_input("/schedule add 2026-02-20 09:30 站会 --duration 30 --interval 1440 --times 3")
        self.assertIn("已添加重复日程 3 条", add_resp)
        self.assertIn("duration=30m", add_resp)
        self.assertIn("interval=1440m", add_resp)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn("2026-02-20 09:30", list_resp)
        self.assertIn("2026-02-21 09:30", list_resp)
        self.assertIn("2026-02-22 09:30", list_resp)
        self.assertIn("| 1440 | 3 | on |", list_resp)
        self.assertIn("| 30 | 站会 |", list_resp)

        invalid = agent.handle_input("/schedule add 2026-02-20 09:30 站会 --times 3")
        self.assertIn("用法", invalid)

        invalid_duration = agent.handle_input("/schedule add 2026-02-20 09:30 站会 --duration 0")
        self.assertIn("用法", invalid_duration)

    def test_slash_schedule_repeat_default_times_is_infinite(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        add_resp = agent.handle_input("/schedule add 2026-02-20 09:30 站会 --interval 60")
        self.assertIn("已添加无限重复日程", add_resp)
        self.assertIn("interval=60m", add_resp)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn("2026-02-20 09:30", list_resp)
        self.assertIn("2026-02-20 10:30", list_resp)

    def test_schedule_list_default_window_from_two_days_ago(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        now = datetime.now()
        too_old = (now - timedelta(days=5)).strftime("%Y-%m-%d 09:00")
        in_window = (now + timedelta(days=3)).strftime("%Y-%m-%d 10:00")
        too_far = (now + timedelta(days=40)).strftime("%Y-%m-%d 11:00")

        agent.handle_input(f"/schedule add {too_old} 过期会")
        agent.handle_input(f"/schedule add {in_window} 窗口内会")
        agent.handle_input(f"/schedule add {too_far} 远期会")

        list_resp = agent.handle_input("/schedule list")
        self.assertIn("日程列表(前天起未来 1 个月)", list_resp)
        self.assertNotIn("过期会", list_resp)
        self.assertIn("窗口内会", list_resp)
        self.assertNotIn("远期会", list_resp)

    def test_slash_schedule_conflict_detection(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 站会")

        conflict = agent.handle_input("/schedule add 2026-02-20 09:30 周会")
        self.assertIn("日程冲突", conflict)
        self.assertIn("2026-02-20 09:30", conflict)

    def test_slash_schedule_conflict_detection_with_duration_overlap(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 站会 --duration 60")

        overlap_conflict = agent.handle_input("/schedule add 2026-02-20 10:00 周会 --duration 30")
        self.assertIn("日程冲突", overlap_conflict)

        non_overlap_ok = agent.handle_input("/schedule add 2026-02-20 10:30 复盘 --duration 30")
        self.assertIn("已添加日程 #2", non_overlap_ok)

    def test_slash_schedule_conflict_detection_with_repeat(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-27 09:30 固定会")

        conflict = agent.handle_input("/schedule add 2026-02-20 09:30 站会 --interval 10080 --times 2")
        self.assertIn("日程冲突", conflict)
        self.assertIn("固定会", conflict)

    def test_slash_schedule_conflict_detection_with_infinite_repeat_window(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-15 10:00 固定会")

        conflict = agent.handle_input("/schedule add 2026-02-15 00:00 高频循环 --interval 1")
        self.assertIn("日程冲突", conflict)
        self.assertIn("固定会", conflict)

    def test_slash_schedule_repeat_update_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 站会 --duration 50")

        update_resp = agent.handle_input("/schedule update 1 2026-02-21 10:00 复盘会 --interval 10080 --times 2")
        self.assertIn("times=2", update_resp)
        self.assertIn("duration=50m", update_resp)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn("2026-02-21 10:00", list_resp)
        self.assertIn("2026-02-28 10:00", list_resp)
        self.assertIn("| 50 | 复盘会 |", list_resp)

    def test_slash_schedule_update_clears_repeat_when_times_is_one(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 站会 --interval 10080 --times 3")

        update_resp = agent.handle_input("/schedule update 1 2026-02-21 10:00 复盘会")
        self.assertIn("已更新日程 #1", update_resp)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn("2026-02-21 10:00", list_resp)
        self.assertNotIn("2026-02-28 10:00", list_resp)

    def test_slash_schedule_repeat_toggle(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 站会 --interval 10080 --times 3")

        off_resp = agent.handle_input("/schedule repeat 1 off")
        self.assertIn("已停用日程 #1 的重复规则", off_resp)
        off_list = agent.handle_input("/schedule list")
        self.assertIn("2026-02-20 09:30", off_list)
        self.assertNotIn("2026-02-27 09:30", off_list)

        on_resp = agent.handle_input("/schedule repeat 1 on")
        self.assertIn("已启用日程 #1 的重复规则", on_resp)
        on_list = agent.handle_input("/schedule list")
        self.assertIn("2026-02-27 09:30", on_list)

    def test_slash_schedule_repeat_toggle_without_rule(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 单次会")
        resp = agent.handle_input("/schedule repeat 1 off")
        self.assertIn("没有可切换的重复规则", resp)

    def test_slash_schedule_update_conflict_detection(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 站会")
        agent.handle_input("/schedule add 2026-02-21 09:30 周会")

        conflict = agent.handle_input("/schedule update 1 2026-02-21 09:30 复盘会")
        self.assertIn("日程冲突", conflict)

    def test_slash_schedule_calendar_view_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-15 10:00 复盘")
        agent.handle_input("/schedule add 2026-02-16 10:00 周会")
        agent.handle_input("/schedule add 2026-03-01 10:00 月初会")

        day_resp = agent.handle_input("/schedule view day 2026-02-15")
        self.assertIn("日历视图(day, 2026-02-15)", day_resp)
        self.assertIn("复盘", day_resp)
        self.assertNotIn("周会", day_resp)

        week_resp = agent.handle_input("/schedule view week 2026-02-16")
        self.assertNotIn("复盘", week_resp)
        self.assertIn("周会", week_resp)
        self.assertNotIn("月初会", week_resp)

        month_resp = agent.handle_input("/schedule view month 2026-03")
        self.assertIn("月初会", month_resp)
        self.assertNotIn("复盘", month_resp)

        invalid = agent.handle_input("/schedule view quarter 2026-02")
        self.assertIn("用法", invalid)

    def test_schedule_view_anchor_can_expand_infinite_recurrence(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-03 10:00 周会 --interval 10080")

        month_resp = agent.handle_input("/schedule view month 2026-04")
        self.assertIn("周会", month_resp)
        self.assertIn("2026-04", month_resp)

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

    def test_nl_schedule_add_with_duration_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json(
                    "schedule_add",
                    event_time="2026-02-20 09:30",
                    title="周会",
                    schedule_duration_minutes=45,
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        add_resp = agent.handle_input("明天早上九点半加个45分钟周会")
        self.assertIn("(45 分钟)", add_resp)
        item = self.db.get_schedule(1)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.duration_minutes, 45)

    def test_nl_schedule_repeat_add_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json(
                    "schedule_add",
                    event_time="2026-02-20 09:30",
                    title="周会",
                    schedule_repeat_interval_minutes=10080,
                    schedule_repeat_times=3,
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        add_resp = agent.handle_input("每周加一个周会，连续三周")
        self.assertIn("已添加重复日程 3 条", add_resp)
        self.assertEqual(len(fake_llm.calls), 1)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn("2026-02-20 09:30", list_resp)
        self.assertIn("2026-02-27 09:30", list_resp)
        self.assertIn("2026-03-06 09:30", list_resp)

    def test_nl_schedule_add_conflict_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("schedule_add", event_time="2026-02-20 09:30", title="周会"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_schedule("站会", "2026-02-20 09:30")

        result = agent.handle_input("帮我加一个 2 月 20 号 9 点半周会")
        self.assertIn("日程冲突", result)
        self.assertIn("站会", result)

    def test_nl_schedule_view_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("schedule_view", schedule_view="week", schedule_view_date="2026-02-16"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_schedule("复盘", "2026-02-15 10:00")
        self.db.add_schedule("周会", "2026-02-16 10:00")
        self.db.add_schedule("月会", "2026-03-01 10:00")

        result = agent.handle_input("看一下 2 月 16 日那周的日程")
        self.assertNotIn("复盘", result)
        self.assertIn("周会", result)
        self.assertNotIn("月会", result)
        self.assertEqual(len(fake_llm.calls), 1)

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

    def test_nl_schedule_repeat_toggle_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("schedule_repeat_disable", schedule_id=1),
                _intent_json("schedule_repeat_enable", schedule_id=1),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        agent.handle_input("/schedule add 2026-02-20 09:30 站会 --interval 10080 --times 3")

        off_resp = agent.handle_input("停用日程1的重复")
        self.assertIn("已停用日程 #1 的重复规则", off_resp)

        on_resp = agent.handle_input("启用日程1的重复")
        self.assertIn("已启用日程 #1 的重复规则", on_resp)

    def test_nl_schedule_update_without_duration_keeps_existing_duration(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json(
                    "schedule_update",
                    schedule_id=1,
                    event_time="2026-02-21 11:00",
                    title="周会-改",
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_schedule("周会", "2026-02-20 09:30", duration_minutes=35)

        response = agent.handle_input("把日程1改到明天11点")
        self.assertIn("(35 分钟)", response)
        item = self.db.get_schedule(1)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.duration_minutes, 35)

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

    def test_plan_replan_multi_step_with_todo_tool(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("todo", "/todo add 买牛奶 --tag life"),
                _planner_continue("todo", "/todo list --tag life"),
                _planner_done("已完成：新增并查看了 life 标签待办。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("帮我新增一个 life 待办并确认下")
        self.assertIn("新增并查看", response)
        todo = self.db.get_todo(1)
        self.assertIsNotNone(todo)
        assert todo is not None
        self.assertEqual(todo.content, "买牛奶")
        self.assertEqual(todo.tag, "life")
        self.assertEqual(len(fake_llm.calls), 3)

    def test_plan_replan_emits_progress_messages(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("todo", "/todo add 买牛奶 --tag life"),
                _planner_done("已完成添加。"),
            ]
        )
        progress_logs: list[str] = []
        agent = AssistantAgent(
            db=self.db,
            llm_client=fake_llm,
            search_provider=FakeSearchProvider(),
            progress_callback=progress_logs.append,
        )

        response = agent.handle_input("新增待办并结束")
        self.assertIn("已完成添加", response)
        self.assertTrue(any("计划列表" in item for item in progress_logs))
        self.assertTrue(any("完成情况" in item for item in progress_logs))
        self.assertTrue(any("任务状态：已完成" in item for item in progress_logs))

    def test_plan_replan_progress_uses_planned_steps_not_max_only(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("todo", "/todo add 买牛奶 --tag life", plan=["添加待办", "总结结果"]),
                _planner_done("完成。", plan=["添加待办", "总结结果"]),
            ]
        )
        progress_logs: list[str] = []
        agent = AssistantAgent(
            db=self.db,
            llm_client=fake_llm,
            search_provider=FakeSearchProvider(),
            progress_callback=progress_logs.append,
        )

        response = agent.handle_input("新增一个待办并汇总")
        self.assertIn("完成", response)
        self.assertTrue(any("已执行 1/2" in item for item in progress_logs))
        self.assertFalse(any("已执行 1/20" in item for item in progress_logs))

    def test_plan_replan_progress_replan_shrink_wont_show_executed_over_plan(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("todo", "/todo list", plan=["步骤A", "步骤B", "步骤C"]),
                _planner_continue("todo", "/todo list", plan=["收尾"]),
                _planner_done("完成。", plan=["收尾"]),
            ]
        )
        progress_logs: list[str] = []
        agent = AssistantAgent(
            db=self.db,
            llm_client=fake_llm,
            search_provider=FakeSearchProvider(),
            progress_callback=progress_logs.append,
        )

        response = agent.handle_input("测试replan缩短计划")
        self.assertIn("完成", response)
        self.assertTrue(any("已执行 2/2" in item for item in progress_logs))
        self.assertFalse(any("已执行 2/1" in item for item in progress_logs))

    def test_plan_replan_prompt_contains_tool_contract(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_done("完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("测试工具契约上下文")
        self.assertIn("完成", response)
        self.assertGreaterEqual(len(fake_llm.calls), 1)
        first_messages = fake_llm.calls[0]
        self.assertIn("/schedule add <YYYY-MM-DD HH:MM> <标题>", first_messages[0]["content"])
        planner_user_payload = json.loads(first_messages[1]["content"])
        self.assertIn("tool_contract", planner_user_payload)
        self.assertIn("schedule", planner_user_payload["tool_contract"])

    def test_plan_replan_ask_user_flow(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("ask_user", "这个待办要用什么标签？"),
                _planner_continue("todo", "/todo add 买牛奶 --tag life"),
                _planner_done("已按 life 标签添加待办。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        first = agent.handle_input("帮我加个买牛奶待办")
        self.assertEqual(first, "请确认：这个待办要用什么标签？")

        second = agent.handle_input("life")
        self.assertIn("life 标签", second)
        todo = self.db.get_todo(1)
        self.assertIsNotNone(todo)
        assert todo is not None
        self.assertEqual(todo.tag, "life")

    def test_plan_replan_repeated_ask_user_will_replan(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("ask_user", "请提供待办tag project的名称，以便我为您创建高优先级的项目。"),
                _planner_continue("ask_user", "请提供待办tag project的名称，以便我为您创建高优先级的项目。"),
                _planner_continue("todo", "/todo add loop agent实现 --tag project --priority 0"),
                _planner_done("已创建 project 标签待办：loop agent实现。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        first = agent.handle_input("帮我新增一个待办tag project，loop agent实现")
        self.assertTrue(first.startswith("请确认："))
        second = agent.handle_input("高优先级")
        self.assertIn("已创建 project 标签待办", second)

        todo = self.db.get_todo(1)
        self.assertIsNotNone(todo)
        assert todo is not None
        self.assertEqual(todo.tag, "project")
        self.assertEqual(todo.content, "loop agent实现")

    def test_plan_replan_after_user_clarification_must_attempt_tool_before_ask(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("ask_user", "请确认标签是什么？"),
                _planner_continue("ask_user", "还需要你再确认优先级。"),
                _planner_continue("todo", "/todo add loop agent实现 --tag project --priority 0"),
                _planner_done("已完成待办创建。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        first = agent.handle_input("帮我新增一个待办tag project，loop agent实现")
        self.assertTrue(first.startswith("请确认："))

        second = agent.handle_input("标签就是 project")
        self.assertIn("已完成待办创建", second)
        todo = self.db.get_todo(1)
        self.assertIsNotNone(todo)
        assert todo is not None
        self.assertEqual(todo.tag, "project")

    def test_plan_replan_pending_task_survives_slash_command(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("ask_user", "你要看 day 还是 week 视图？"),
                _planner_continue("schedule", "/schedule list"),
                _planner_done("已查看当前窗口内日程。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        first = agent.handle_input("看一下我的日程")
        self.assertEqual(first, "请确认：你要看 day 还是 week 视图？")

        slash_result = agent.handle_input("/todo add 临时任务")
        self.assertIn("已添加待办", slash_result)

        second = agent.handle_input("week")
        self.assertIn("窗口内日程", second)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_plan_replan_internet_search_tool(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("internet_search", "OpenAI Responses API"),
                _planner_done("我找到了 3 条相关资料。"),
            ]
        )
        fake_search = FakeSearchProvider(
            results=[
                SearchResult(title="A", snippet="S1", url="https://example.com/a"),
                SearchResult(title="B", snippet="S2", url="https://example.com/b"),
                SearchResult(title="C", snippet="S3", url="https://example.com/c"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=fake_search)

        response = agent.handle_input("帮我查下 Responses API 最新资料")
        self.assertIn("3 条相关资料", response)
        self.assertEqual(fake_search.queries, [("OpenAI Responses API", 3)])

    def test_plan_replan_max_steps_fallback(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("todo", "/todo list"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("循环执行直到超限")
        self.assertIn("已达到最大执行步数（20）", response)
        self.assertIn("下一步建议", response)

    def test_cancel_pending_plan_task(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_continue("ask_user", "你想操作哪个待办 id？"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        ask = agent.handle_input("帮我更新待办")
        self.assertEqual(ask, "请确认：你想操作哪个待办 id？")
        cancel = agent.handle_input("取消当前任务")
        self.assertEqual(cancel, "已取消当前任务。")

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

    def test_schedule_repeat_invalid_combo_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json(
                    "schedule_add",
                    event_time="2026-02-20 09:30",
                    title="周会",
                    schedule_repeat_times=2,
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("帮我加一个重复日程")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_schedule_add_invalid_duration_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json(
                    "schedule_add",
                    event_time="2026-02-20 09:30",
                    title="周会",
                    schedule_duration_minutes=0,
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("帮我加个0分钟日程")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_schedule_view_invalid_date_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("schedule_view", schedule_view="month", schedule_view_date="2026-02-15"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("看 2026-02-15 的月视图")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_schedule_repeat_toggle_missing_id_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _intent_json("schedule_repeat_disable"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        response = agent.handle_input("停用重复日程")
        self.assertIn("意图识别服务暂时不可用", response)
        self.assertEqual(len(fake_llm.calls), 3)

    def test_chat_without_llm(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        result = agent.handle_input("今天要做什么")
        self.assertIn("未配置 LLM", result)

    def test_strip_think_blocks(self) -> None:
        text = "<think>abc</think>最终答案"
        self.assertEqual(_strip_think_blocks(text), "最终答案")

    def test_try_parse_json_from_fenced_block(self) -> None:
        payload = _try_parse_json('```json\n{"intent":"todo_list"}\n```')
        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
