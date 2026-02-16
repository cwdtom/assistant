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
from assistant_app.planner_thought import normalize_thought_decision
from assistant_app.search import SearchResult


def _thought_continue(tool: str, action_input: str, plan: list[str] | None = None) -> str:
    payload = {
        "status": "continue",
        "plan": plan or ["执行下一步"],
        "next_action": {"tool": tool, "input": action_input},
        "response": None,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _thought_ask_user(question: str, current_step: str = "待澄清") -> str:
    payload = {
        "status": "ask_user",
        "current_step": current_step,
        "next_action": None,
        "question": question,
        "response": None,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _planner_planned(plan: list[str] | None = None) -> str:
    payload = {
        "status": "planned",
        "plan": plan or ["执行下一步"],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _planner_replanned(plan: list[str] | None = None) -> str:
    payload = {
        "status": "replanned",
        "plan": plan or ["执行下一步"],
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


def _planner_done_without_response(current_step: str = "执行完成") -> str:
    payload = {
        "status": "done",
        "current_step": current_step,
        "next_action": None,
        "question": None,
        "response": None,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _extract_phase_from_messages(messages: list[dict[str, str]]) -> str:
    if not messages:
        return ""
    payload = _try_parse_json(messages[-1].get("content", ""))
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("phase") or "").strip().lower()


def _fallback_replan_from_messages(messages: list[dict[str, str]]) -> str:
    if not messages:
        return _planner_done("已完成。")
    payload = _try_parse_json(messages[-1].get("content", ""))
    if not isinstance(payload, dict):
        return _planner_done("已完成。")
    pending_final = str(payload.get("pending_final_response") or "").strip()
    if pending_final:
        return _planner_done(pending_final)
    raw_plan = payload.get("latest_plan")
    if isinstance(raw_plan, list):
        plan = [str(item).strip() for item in raw_plan if str(item).strip()]
        if plan:
            raw_index = payload.get("current_plan_index")
            if isinstance(raw_index, int) and 0 <= raw_index < len(plan):
                remaining = plan[raw_index:]
                if remaining:
                    return _planner_replanned(remaining)
            return _planner_replanned(plan)
    return _planner_done("已完成。")


class FakeLLMClient:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[list[dict[str, str]]] = []
        self._cursor = 0
        self.model_call_count = 0

    def reply(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        phase = _extract_phase_from_messages(messages)

        if phase == "replan":
            if self._cursor < len(self.responses):
                candidate = self.responses[self._cursor]
                parsed = _try_parse_json(candidate)
                if isinstance(parsed, dict):
                    status = str(parsed.get("status") or "").strip().lower()
                    if status in {"replanned", "done"}:
                        self.model_call_count += 1
                        self._cursor += 1
                        return candidate
                    # Backward-compatible tests may omit explicit replanned payload.
                    if status in {"planned", "continue"}:
                        return _fallback_replan_from_messages(messages)
                self.model_call_count += 1
                self._cursor += 1
                return candidate
            return _fallback_replan_from_messages(messages)

        self.model_call_count += 1
        if self._cursor < len(self.responses):
            result = self.responses[self._cursor]
            self._cursor += 1
        elif self.responses:
            result = self.responses[-1]
        else:
            result = _planner_done("未提供可用的计划输出，请重试。")
        return result


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
        now = datetime.now()
        today_due = now.replace(hour=18, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        tomorrow_due = (now + timedelta(days=1)).replace(
            hour=10,
            minute=0,
            second=0,
            microsecond=0,
        ).strftime("%Y-%m-%d %H:%M")
        agent.handle_input(f"/todo add 今天复盘 --tag work --due {today_due}")
        agent.handle_input(f"/todo add 明天写周报 --tag work --due {tomorrow_due}")
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

    def test_slash_schedule_remind_fields_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        add_resp = agent.handle_input(
            "/schedule add 2026-02-20 09:30 站会 --remind 2026-02-20 09:00 "
            "--interval 1440 --times 3 --remind-start 2026-02-20 08:30"
        )
        self.assertIn("提醒:2026-02-20 09:00", add_resp)
        self.assertIn("重复提醒开始:2026-02-20 08:30", add_resp)

        detail = agent.handle_input("/schedule get 1")
        self.assertIn("提醒时间", detail)
        self.assertIn("重复提醒开始", detail)
        self.assertIn("2026-02-20 09:00", detail)
        self.assertIn("2026-02-20 08:30", detail)

        update_resp = agent.handle_input(
            "/schedule update 1 2026-02-21 09:30 站会 --remind 2026-02-21 09:10 "
            "--interval 1440 --times 3 --remind-start 2026-02-21 08:40"
        )
        self.assertIn("提醒:2026-02-21 09:10", update_resp)
        self.assertIn("重复提醒开始:2026-02-21 08:40", update_resp)

        invalid = agent.handle_input(
            "/schedule add 2026-02-22 09:30 单次会 --remind-start 2026-02-22 09:00"
        )
        self.assertIn("用法", invalid)

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
        self.assertIn("日程列表(前天起未来 31 天)", list_resp)
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
                _planner_planned(["新增待办", "总结结果"]),
                _thought_continue("todo", "/todo add 买牛奶 --tag life --priority 1"),
                _planner_done("已添加待办。"),
                _planner_planned(["查看待办", "总结结果"]),
                _thought_continue("todo", "/todo list --tag life"),
                _planner_done("已查看待办：买牛奶。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("修 bug", tag="work")

        add_resp = agent.handle_input("帮我记一个待办，买牛奶")
        self.assertIn("已添加待办", add_resp)

        list_resp = agent.handle_input("看一下我的待办")
        self.assertIn("买牛奶", list_resp)
        life_todos = self.db.list_todos(tag="life")
        self.assertEqual(len(life_todos), 1)
        self.assertEqual(life_todos[0].content, "买牛奶")
        self.assertEqual(fake_llm.model_call_count, 6)

    def test_nl_schedule_flow_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增日程", "总结结果"]),
                _thought_continue("schedule", "/schedule add 2026-02-20 09:30 周会"),
                _planner_done("已添加日程。"),
                _planner_planned(["查看日程", "总结结果"]),
                _thought_continue("schedule", "/schedule list"),
                _planner_done("已查看日程：周会。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        add_resp = agent.handle_input("明天早上九点半加个周会")
        self.assertIn("已添加日程", add_resp)

        list_resp = agent.handle_input("看一下日程")
        self.assertIn("周会", list_resp)
        self.assertEqual(fake_llm.model_call_count, 6)

    def test_nl_schedule_add_with_duration_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增日程", "总结结果"]),
                _thought_continue("schedule", "/schedule add 2026-02-20 09:30 周会 --duration 45"),
                _planner_done("已添加日程 (45 分钟)。"),
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
                _planner_planned(["新增重复日程", "总结结果"]),
                _thought_continue("schedule", "/schedule add 2026-02-20 09:30 周会 --interval 10080 --times 3"),
                _planner_done("已添加重复日程 3 条。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        add_resp = agent.handle_input("每周加一个周会，连续三周")
        self.assertIn("已添加重复日程 3 条", add_resp)
        self.assertEqual(fake_llm.model_call_count, 3)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn("2026-02-20 09:30", list_resp)
        self.assertIn("2026-02-27 09:30", list_resp)
        self.assertIn("2026-03-06 09:30", list_resp)

    def test_nl_schedule_add_conflict_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增日程", "总结结果"]),
                _thought_continue("schedule", "/schedule add 2026-02-20 09:30 周会"),
                _planner_done("日程冲突：站会"),
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
                _planner_planned(["查看周视图", "总结结果"]),
                _thought_continue("schedule", "/schedule view week 2026-02-16"),
                _planner_done("已查看周会。"),
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
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_nl_todo_search_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["搜索待办", "总结结果"]),
                _thought_continue("todo", "/todo search 牛奶 --tag life"),
                _planner_done("已返回搜索结果：买牛奶。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("买牛奶", tag="life")
        self.db.add_todo("写周报", tag="work")

        result = agent.handle_input("帮我找一下life里和牛奶有关的待办")
        self.assertIn("搜索结果", result)
        self.assertIn("买牛奶", result)
        self.assertNotIn("写周报", result)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_nl_todo_view_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["查看 today 视图", "总结结果"]),
                _thought_continue("todo", "/todo list --view today"),
                _planner_done("已查看 today 视图：今天复盘。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("今天复盘", due_at="2026-02-15 18:00")
        self.db.add_todo("明天开会", due_at="2026-02-16 09:30")

        result = agent.handle_input("看一下今天待办")
        self.assertIn("今天复盘", result)
        self.assertNotIn("明天开会", result)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_nl_todo_update_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["更新待办", "总结结果"]),
                _thought_continue(
                    "todo",
                    "/todo update 1 买牛奶和面包 --tag life --priority 2 "
                    "--due 2026-02-26 20:00 --remind 2026-02-26 19:30",
                ),
                _planner_done("已更新待办 #1 [标签:life]。"),
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
                _planner_planned(["更新待办提醒", "总结结果"]),
                _thought_continue("todo", "/todo update 1 准备周报 --tag work --remind 2026-02-26 19:30"),
                _planner_done("已更新待办 #1。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("准备周报", tag="work", due_at="2026-02-26 20:00")

        response = agent.handle_input("把待办1提醒时间改成晚上7点半")
        self.assertIn("已更新待办 #1", response)
        self.assertEqual(fake_llm.model_call_count, 3)

        todo = self.db.get_todo(1)
        self.assertIsNotNone(todo)
        assert todo is not None
        self.assertEqual(todo.due_at, "2026-02-26 20:00")
        self.assertEqual(todo.remind_at, "2026-02-26 19:30")

    def test_nl_schedule_delete_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["删除日程", "总结结果"]),
                _thought_continue("schedule", "/schedule delete 1"),
                _planner_done("日程 #1 已删除。"),
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
                _planner_planned(["停用重复", "总结结果"]),
                _thought_continue("schedule", "/schedule repeat 1 off"),
                _planner_done("已停用日程 #1 的重复规则。"),
                _planner_planned(["启用重复", "总结结果"]),
                _thought_continue("schedule", "/schedule repeat 1 on"),
                _planner_done("已启用日程 #1 的重复规则。"),
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
                _planner_planned(["更新日程", "总结结果"]),
                _thought_continue("schedule", "/schedule update 1 2026-02-21 11:00 周会-改"),
                _planner_done("已更新日程 (35 分钟)。"),
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

    def test_chat_path_returns_disabled_message(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["收集目标", "给出结论"]),
                _planner_done("当前版本已关闭 chat 直聊分支。请明确待办/日程目标，或使用 /todo、/schedule 命令。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        self.db.add_todo("修复 bug")
        response = agent.handle_input("今天怎么安排")

        self.assertIn("已关闭 chat 直聊分支", response)
        self.assertEqual(fake_llm.model_call_count, 2)

        history = self.db.recent_messages(limit=2)
        self.assertEqual(history, [])

    def test_plan_replan_multi_step_with_todo_tool(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增待办", "查看列表", "总结"]),
                _thought_continue("todo", "/todo add 买牛奶 --tag life"),
                _thought_continue("todo", "/todo list --tag life"),
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
        self.assertEqual(fake_llm.model_call_count, 4)

    def test_replan_runs_after_each_subtask_loop(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["查看今天日程"]),
                _thought_continue("schedule", "/schedule view day 2026-02-16"),
                _planner_done_without_response("查看今天日程"),
                _planner_replanned(["总结结果"]),
                _planner_done("已查看今天日程。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())
        self.db.add_schedule("周会", "2026-02-16 09:00")

        response = agent.handle_input("看一下今天的日程")
        self.assertIn("已查看今天日程", response)
        phases = [_extract_phase_from_messages(call) for call in fake_llm.calls]
        self.assertGreaterEqual(len(phases), 5)
        self.assertEqual(phases[:5], ["plan", "thought", "thought", "replan", "thought"])

    def test_thought_done_only_marks_subtask_and_replan_decides_final(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["汇总结果"]),
                _planner_done_without_response("汇总结果"),
                _planner_done("最终结论：今天有 1 条日程。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())
        self.db.add_schedule("站会", "2026-02-16 10:00")

        response = agent.handle_input("看一下今天的日程并总结")
        self.assertIn("最终结论", response)
        phases = [_extract_phase_from_messages(call) for call in fake_llm.calls]
        self.assertEqual(phases[:3], ["plan", "thought", "replan"])

    def test_plan_replan_emits_progress_messages(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["添加待办", "总结结果"]),
                _thought_continue("todo", "/todo add 买牛奶 --tag life"),
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

    def test_plan_replan_emits_current_plan_item_progress(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["添加待办", "总结结果"]),
                _thought_continue("todo", "/todo add 买牛奶 --tag life", plan=["添加待办", "总结结果"]),
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
        self.assertTrue(any("当前计划项：1/2 - 添加待办" in item for item in progress_logs))

    def test_plan_replan_done_advances_current_plan_item(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["整理输入", "执行添加"]),
                _planner_done_without_response("整理输入"),
                _thought_continue("todo", "/todo add 买牛奶 --tag life", plan=["整理输入", "执行添加"]),
                _planner_done("完成。", plan=["整理输入", "执行添加"]),
            ]
        )
        progress_logs: list[str] = []
        agent = AssistantAgent(
            db=self.db,
            llm_client=fake_llm,
            search_provider=FakeSearchProvider(),
            progress_callback=progress_logs.append,
        )

        response = agent.handle_input("先整理再创建待办")
        self.assertIn("完成", response)
        self.assertTrue(any("当前计划项：1/2 - 整理输入" in item for item in progress_logs))
        self.assertTrue(
            any("当前计划项：2/2 - 执行添加" in item for item in progress_logs)
            or any("当前计划项：1/1 - 执行添加" in item for item in progress_logs)
        )
        todo = self.db.get_todo(1)
        self.assertIsNotNone(todo)
        assert todo is not None
        self.assertEqual(todo.content, "买牛奶")

    def test_done_loop_after_plan_completed_returns_guarded_summary(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["查看待办"]),
                _thought_continue("todo", "/todo list"),
                _planner_done_without_response("查看待办"),
                _planner_done_without_response("查看待办"),
                _planner_done_without_response("查看待办"),
            ]
        )
        agent = AssistantAgent(
            db=self.db,
            llm_client=fake_llm,
            search_provider=FakeSearchProvider(),
            plan_replan_max_steps=100,
            plan_continuous_failure_limit=2,
        )

        response = agent.handle_input("看一下全部待办")
        self.assertIn("计划步骤已执行完毕，但模型未返回可用的子任务结论", response)
        self.assertNotIn("已达到最大执行步数", response)
        self.assertEqual(fake_llm.model_call_count, 6)

    def test_plan_replan_progress_uses_planned_steps_not_max_only(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["添加待办", "总结结果"]),
                _thought_continue("todo", "/todo add 买牛奶 --tag life", plan=["添加待办", "总结结果"]),
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
        self.assertTrue(any("已执行 2/2" in item for item in progress_logs))
        self.assertFalse(any("已执行 1/20" in item for item in progress_logs))

    def test_plan_replan_progress_replan_shrink_wont_show_executed_over_plan(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["步骤A", "步骤B", "步骤C"]),
                _thought_continue("todo", "/todo list", plan=["步骤A", "步骤B", "步骤C"]),
                _thought_continue("todo", "/todo list", plan=["收尾"]),
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
        self.assertTrue(any("已执行 4/4" in item for item in progress_logs))
        self.assertFalse(any("已执行 2/1" in item for item in progress_logs))

    def test_plan_progress_list_not_repeated_when_plan_unchanged(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["步骤A", "步骤B"]),
                _thought_continue("todo", "/todo list", plan=["步骤A", "步骤B"]),
                _thought_continue("todo", "/todo list", plan=["步骤A", "步骤B"]),
                _planner_done("完成。", plan=["步骤A", "步骤B"]),
            ]
        )
        progress_logs: list[str] = []
        agent = AssistantAgent(
            db=self.db,
            llm_client=fake_llm,
            search_provider=FakeSearchProvider(),
            progress_callback=progress_logs.append,
        )

        response = agent.handle_input("测试不重复输出计划列表")
        self.assertIn("完成", response)
        plan_logs = [item for item in progress_logs if "计划列表" in item]
        self.assertEqual(len(plan_logs), 1)

    def test_plan_replan_prompt_contains_tool_contract(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["收尾"]),
                _planner_done("完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("测试工具契约上下文")
        self.assertIn("完成", response)
        self.assertEqual(fake_llm.model_call_count, 2)
        first_messages = fake_llm.calls[0]
        self.assertIn("plan 模块", first_messages[0]["content"])
        planner_user_payload = json.loads(first_messages[1]["content"])
        self.assertIn("tool_contract", planner_user_payload)
        self.assertIn("schedule", planner_user_payload["tool_contract"])
        self.assertIn("time_unit_contract", planner_user_payload)
        self.assertIn("schedule_duration_unit", planner_user_payload["time_unit_contract"])
        self.assertIn("180", planner_user_payload["time_unit_contract"]["schedule_duration_unit"])

    def test_plan_replan_ask_user_flow(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["确认标签", "创建待办", "总结"]),
                _thought_ask_user("这个待办要用什么标签？", current_step="确认标签"),
                _planner_replanned(["创建待办", "总结"]),
                _thought_continue("todo", "/todo add 买牛奶 --tag life"),
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
                _planner_planned(["确认 project 信息", "执行创建", "总结结果"]),
                _thought_ask_user(
                    "请提供待办tag project的名称，以便我为您创建高优先级的项目。",
                    current_step="确认 project 信息",
                ),
                _planner_replanned(["补充信息确认", "执行创建", "总结结果"]),
                _thought_ask_user(
                    "请提供待办tag project的名称，以便我为您创建高优先级的项目。",
                    current_step="补充信息确认",
                ),
                _thought_continue("todo", "/todo add loop agent实现 --tag project --priority 0"),
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

    def test_plan_replan_after_user_clarification_can_reask_then_complete(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["确认标签", "确认优先级", "创建待办", "总结"]),
                _thought_ask_user("请确认标签是什么？", current_step="确认标签"),
                _planner_replanned(["确认优先级", "创建待办", "总结"]),
                _thought_ask_user("还需要你再确认优先级。", current_step="确认优先级"),
                _planner_replanned(["创建待办", "总结"]),
                _thought_continue("todo", "/todo add loop agent实现 --tag project --priority 0"),
                _planner_done("已完成待办创建。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        first = agent.handle_input("帮我新增一个待办tag project，loop agent实现")
        self.assertTrue(first.startswith("请确认："))

        second = agent.handle_input("标签就是 project")
        self.assertTrue(second.startswith("请确认："))

        third = agent.handle_input("优先级 0")
        self.assertIn("已完成待办创建", third)
        todo = self.db.get_todo(1)
        self.assertIsNotNone(todo)
        assert todo is not None
        self.assertEqual(todo.tag, "project")

    def test_plan_replan_pending_task_survives_slash_command(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["确认视图类型", "查看日程", "总结"]),
                _thought_ask_user("你要看 day 还是 week 视图？", current_step="确认视图类型"),
                _planner_replanned(["查看日程", "总结"]),
                _thought_continue("schedule", "/schedule list"),
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
        self.assertEqual(fake_llm.model_call_count, 5)

    def test_thought_parse_failure_counts_step_limit(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["仅一步"]),
                "not-json",
                "still-not-json",
            ]
        )
        agent = AssistantAgent(
            db=self.db,
            llm_client=fake_llm,
            search_provider=FakeSearchProvider(),
            plan_replan_max_steps=2,
            plan_replan_retry_count=0,
            plan_continuous_failure_limit=99,
        )

        response = agent.handle_input("测试 thought 解析失败计步")
        self.assertIn("已达到最大执行步数（2）", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_replan_parse_failure_counts_step_limit(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["收集信息", "执行创建"]),
                _thought_ask_user("请确认标签是什么？", current_step="收集信息"),
                "not-json",
            ]
        )
        agent = AssistantAgent(
            db=self.db,
            llm_client=fake_llm,
            search_provider=FakeSearchProvider(),
            plan_replan_max_steps=2,
            plan_replan_retry_count=0,
            plan_continuous_failure_limit=99,
        )

        first = agent.handle_input("帮我新增一个待办")
        self.assertTrue(first.startswith("请确认："))
        second = agent.handle_input("标签 project")
        self.assertIn("已达到最大执行步数（2）", second)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_thought_contract_rejects_continue_with_ask_user_tool(self) -> None:
        decision = normalize_thought_decision(
            {
                "status": "continue",
                "current_step": "需要澄清",
                "next_action": {"tool": "ask_user", "input": "补充标签信息"},
                "question": None,
                "response": None,
            }
        )
        self.assertIsNone(decision)

    def test_plan_replan_internet_search_tool(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["搜索资料", "总结结果"]),
                _thought_continue("internet_search", "OpenAI Responses API"),
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
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_plan_replan_max_steps_fallback(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["循环查看待办"]),
                _thought_continue("todo", "/todo list"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("循环执行直到超限")
        self.assertIn("已达到最大执行步数（20）", response)
        self.assertIn("下一步建议", response)

    def test_cancel_pending_plan_task(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["确认目标待办", "执行更新"]),
                _thought_ask_user("你想操作哪个待办 id？", current_step="确认目标待办"),
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
                json.dumps({"intent": "todo_done"}, ensure_ascii=False, separators=(",", ":")),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("把待办完成")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_action_missing_params_retry_then_success(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["确认待办编号", "执行完成", "总结结果"]),
                _thought_continue("todo", "/todo done"),
                _thought_continue("todo", "/todo done 1"),
                _planner_done("待办 #1 已完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_todo("修复登录问题")

        response = agent.handle_input("把这个待办标记完成")
        self.assertIn("待办 #1 已完成", response)
        self.assertEqual(fake_llm.model_call_count, 4)

    def test_schedule_delete_missing_id_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                json.dumps({"intent": "schedule_delete"}, ensure_ascii=False, separators=(",", ":")),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("删掉这个日程")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_invalid_intent_json_returns_service_unavailable(self) -> None:
        fake_llm = FakeLLMClient(responses=["不是json", "还是不是json", "依然不是json"])
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("今天天气如何")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertIn("/todo", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_non_json_model_text_is_treated_as_failure(self) -> None:
        fake_llm = FakeLLMClient(responses=["我先快速扫一遍待办项并给你汇总清单。"])
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("看一下全部待办")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_invalid_json_retry_then_success(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                "不是json",
                _planner_planned(["新增待办", "总结结果"]),
                _thought_continue("todo", "/todo add 明天早上10 :00吃早饭"),
                _planner_done("已添加待办：明天早上10 :00吃早饭。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("增加一个测试待办，明天早上10 :00吃早饭")
        self.assertIn("已添加待办", response)
        self.assertIn("明天早上10 :00吃早饭", response)
        self.assertEqual(fake_llm.model_call_count, 4)

        list_resp = agent.handle_input("/todo list")
        self.assertIn("明天早上10 :00吃早饭", list_resp)

    def test_todo_add_with_remind_without_due_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增待办", "总结结果"]),
                _thought_continue("todo", "/todo add 准备周报 --tag work --remind 2026-02-25 09:00"),
                _planner_done(
                    "用法: /todo add <内容> [--tag <标签>] [--priority <>=0>] "
                    "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]"
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("提醒我写周报")
        self.assertIn("用法: /todo add", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_todo_add_with_negative_priority_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增待办", "总结结果"]),
                _thought_continue("todo", "/todo add 整理文档 --priority -2"),
                _planner_done(
                    "用法: /todo add <内容> [--tag <标签>] [--priority <>=0>] "
                    "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]"
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("添加一个待办，优先级负数")
        self.assertIn("用法: /todo add", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_todo_search_missing_keyword_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                json.dumps({"intent": "todo_search"}, ensure_ascii=False, separators=(",", ":")),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("帮我搜索待办")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_todo_view_missing_view_name_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                json.dumps({"intent": "todo_view"}, ensure_ascii=False, separators=(",", ":")),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("看一下待办视图")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_schedule_repeat_invalid_combo_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增日程", "总结结果"]),
                _thought_continue("schedule", "/schedule add 2026-02-20 09:30 周会 --times 2"),
                _planner_done(
                    "用法: /schedule add <YYYY-MM-DD HH:MM> <标题> "
                    "[--duration <>=1>] [--interval <>=1>] [--times <-1|>=2>]"
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("帮我加一个重复日程")
        self.assertIn("用法: /schedule add", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_schedule_add_invalid_duration_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增日程", "总结结果"]),
                _thought_continue("schedule", "/schedule add 2026-02-20 09:30 周会 --duration 0"),
                _planner_done(
                    "用法: /schedule add <YYYY-MM-DD HH:MM> <标题> "
                    "[--duration <>=1>] [--interval <>=1>] [--times <-1|>=2>]"
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("帮我加个0分钟日程")
        self.assertIn("用法: /schedule add", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_schedule_view_invalid_date_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["查看月视图", "总结结果"]),
                _thought_continue("schedule", "/schedule view month 2026-02-15"),
                _planner_done("用法: /schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM]"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("看 2026-02-15 的月视图")
        self.assertIn("用法: /schedule view", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_schedule_repeat_toggle_missing_id_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                json.dumps({"intent": "schedule_repeat_disable"}, ensure_ascii=False, separators=(",", ":")),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        response = agent.handle_input("停用重复日程")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertEqual(fake_llm.model_call_count, 3)

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
