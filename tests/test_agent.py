from __future__ import annotations

import io
import json
import logging
import sqlite3
import tempfile
import threading
import unittest
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from assistant_app.agent import (
    AssistantAgent,
    _strip_think_blocks,
    _try_parse_json,
)
from assistant_app.agent_components.models import PlannerObservation
from assistant_app.agent_components.tools.planner_tool_routing import build_json_planner_tool_executor
from assistant_app.db import AssistantDB
from assistant_app.planner_thought import normalize_thought_decision, normalize_thought_tool_call
from assistant_app.schemas.commands import parse_tool_command_payload
from assistant_app.schemas.planner import ToolReplyPayload
from assistant_app.schemas.routing import JsonPlannerToolRoute, RuntimePlannerActionPayload
from assistant_app.schemas.tools import (
    HistorySearchArgs,
    InternetSearchFetchUrlArgs,
    ScheduleAddArgs,
    ScheduleUpdateArgs,
    ThoughtsUpdateArgs,
    coerce_history_action_payload,
    coerce_schedule_action_payload,
    coerce_thoughts_action_payload,
)
from assistant_app.search import SearchResult

_DEFAULT_PLAN_TOOLS = ["schedule", "internet_search", "history"]


def _build_plan_objects(
    plan: list[str] | None = None,
    *,
    completed: set[str] | None = None,
    tools_by_task: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    completed_tasks = completed or set()
    mapping = tools_by_task or {}
    plan_items = ["执行下一步"] if plan is None else plan
    return [
        {
            "task": item,
            "completed": item in completed_tasks,
            "tools": list(mapping.get(item, _DEFAULT_PLAN_TOOLS)),
        }
        for item in plan_items
    ]


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


def _planner_planned(
    plan: list[str] | None = None,
    *,
    goal: str = "扩展后的目标",
    tools_by_task: dict[str, list[str]] | None = None,
) -> str:
    payload = {
        "status": "planned",
        "goal": goal,
        "plan": _build_plan_objects(plan, completed=set(), tools_by_task=tools_by_task),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _planner_replanned(
    plan: list[str] | None = None,
    *,
    completed: set[str] | None = None,
    tools_by_task: dict[str, list[str]] | None = None,
) -> str:
    payload = {
        "status": "replanned",
        "plan": _build_plan_objects(plan, completed=completed, tools_by_task=tools_by_task),
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


def _parse_json_lines(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _extract_payload_from_messages(messages: list[dict[str, str]]) -> dict[str, Any]:
    parsed = _try_parse_json(messages[-1].get("content", ""))
    if isinstance(parsed, dict):
        return parsed
    return {}


def _extract_history_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if len(messages) <= 2:
        return []
    return messages[1:-1]


def _extract_tool_names_from_schemas(tool_schemas: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for item in tool_schemas:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if name:
            names.append(name)
    return names


def _message_payloads_by_phase(messages: list[dict[str, str]], phase: str) -> list[dict[str, Any]]:
    matched: list[dict[str, Any]] = []
    for item in messages:
        payload = _try_parse_json(item.get("content", ""))
        if not isinstance(payload, dict):
            continue
        if str(payload.get("phase") or "").strip().lower() != phase:
            continue
        matched.append(payload)
    return matched


def _extract_plan_items_from_latest_plan(raw_plan: Any) -> list[str]:
    if not isinstance(raw_plan, list):
        return []
    extracted: list[str] = []
    for item in raw_plan:
        if isinstance(item, dict):
            task = str(item.get("task") or item.get("item") or "").strip()
            if task:
                extracted.append(task)
            continue
        step = str(item).strip()
        if step:
            extracted.append(step)
    return extracted


def _legacy_command_to_tool_arguments(_tool: str, action_input: str) -> dict[str, Any] | None:
    parsed = _try_parse_json(action_input)
    if isinstance(parsed, dict):
        return dict(parsed)
    return None


def _fallback_replan_from_messages(messages: list[dict[str, str]]) -> str:
    if not messages:
        return _planner_done("已完成。")
    payload = _try_parse_json(messages[-1].get("content", ""))
    if not isinstance(payload, dict):
        return _planner_done("已完成。")
    raw_plan = payload.get("latest_plan")
    if isinstance(raw_plan, list):
        plan = _extract_plan_items_from_latest_plan(raw_plan)
        if plan:
            raw_index = payload.get("current_plan_index")
            if isinstance(raw_index, int):
                if 0 <= raw_index < len(plan):
                    remaining = plan[raw_index:]
                    if remaining:
                        return _planner_replanned(remaining)
                if raw_index >= len(plan):
                    completed_subtasks = payload.get("completed_subtasks")
                    if isinstance(completed_subtasks, list) and completed_subtasks:
                        last = completed_subtasks[-1]
                        if isinstance(last, dict):
                            result = str(last.get("result") or "").strip()
                            if result:
                                return _planner_done(result)
                    return _planner_done("已完成。")
            return _planner_replanned(plan)
    completed_subtasks = payload.get("completed_subtasks")
    if isinstance(completed_subtasks, list) and completed_subtasks:
        last = completed_subtasks[-1]
        if isinstance(last, dict):
            result = str(last.get("result") or "").strip()
            if result:
                return _planner_done(result)
    return _planner_done("已完成。")


class FakeLLMClient:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[list[dict[str, str]]] = []
        self.tool_schema_calls: list[list[dict[str, Any]]] = []
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


class FakeToolCallingLLMClient(FakeLLMClient):
    def reply_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        del tool_choice
        self.calls.append(messages)
        self.tool_schema_calls.append(deepcopy(tools))
        self.model_call_count += 1
        if self._cursor < len(self.responses):
            candidate = self.responses[self._cursor]
            self._cursor += 1
        elif self.responses:
            candidate = self.responses[-1]
        else:
            candidate = _planner_done("未提供可用的计划输出，请重试。")
        parsed = _try_parse_json(candidate)
        if not isinstance(parsed, dict):
            return {
                "assistant_message": {"role": "assistant", "content": candidate, "tool_calls": []},
                "reasoning_content": None,
            }

        status = str(parsed.get("status") or "").strip().lower()
        current_step = str(parsed.get("current_step") or "").strip()
        tool_call_payload: dict[str, Any] | None = None
        if status == "continue":
            next_action = parsed.get("next_action")
            if isinstance(next_action, dict):
                action_tool = str(next_action.get("tool") or "").strip().lower()
                action_input = str(next_action.get("input") or "").strip()
                tool_call_name = action_tool
                if action_tool == "internet_search":
                    tool_call_name = "internet_search_tool"
                    arguments = {"query": action_input}
                elif action_tool == "history":
                    history_arguments = _legacy_command_to_tool_arguments(action_tool, action_input)
                    if history_arguments is None:
                        history_arguments = {"action": "list"}
                    tool_call_name, arguments = _history_tool_name_and_arguments(history_arguments)
                elif action_tool == "schedule":
                    schedule_arguments = _legacy_command_to_tool_arguments(action_tool, action_input)
                    if schedule_arguments is None:
                        schedule_arguments = {"action": "list"}
                    tool_call_name, arguments = _schedule_tool_name_and_arguments(schedule_arguments)
                elif action_tool == "history_search":
                    arguments = {"keyword": "牛奶", "limit": 20}
                    parsed_history = _try_parse_json(action_input)
                    if isinstance(parsed_history, dict):
                        arguments = dict(parsed_history)
                else:
                    arguments = _legacy_command_to_tool_arguments(action_tool, action_input)
                    if arguments is None:
                        arguments = {"action": "list"}
                if current_step:
                    arguments["current_step"] = current_step
                tool_call_payload = {
                    "name": tool_call_name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                }
        elif status == "ask_user":
            question = str(parsed.get("question") or "").strip()
            arguments: dict[str, Any] = {"question": question}
            if current_step:
                arguments["current_step"] = current_step
            tool_call_payload = {
                "name": "ask_user",
                "arguments": json.dumps(arguments, ensure_ascii=False),
            }
        elif status == "done":
            response = str(parsed.get("response") or "").strip()
            arguments = {"response": response}
            if current_step:
                arguments["current_step"] = current_step
            tool_call_payload = {
                "name": "done",
                "arguments": json.dumps(arguments, ensure_ascii=False),
            }

        if tool_call_payload is None:
            return {
                "assistant_message": {"role": "assistant", "content": candidate, "tool_calls": []},
                "reasoning_content": None,
            }

        return {
            "assistant_message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{self.model_call_count}",
                        "type": "function",
                        "function": tool_call_payload,
                    }
                ],
            },
            "reasoning_content": None,
        }


class FakeThinkingToolCallingLLMClient(FakeToolCallingLLMClient):
    def reply_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        payload = super().reply_with_tools(messages, tools=tools, tool_choice=tool_choice)
        payload["reasoning_content"] = "thinking trace"
        return payload


class FakeTypedToolCallingLLMClient(FakeToolCallingLLMClient):
    def reply_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> ToolReplyPayload:
        payload = super().reply_with_tools(messages, tools=tools, tool_choice=tool_choice)
        return ToolReplyPayload.model_validate(payload)


class FakeMultiToolCallingLLMClient(FakeToolCallingLLMClient):
    def reply_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        del tools, tool_choice
        self.calls.append(messages)
        self.model_call_count += 1
        phase = _extract_phase_from_messages(messages)
        if phase != "thought":
            return super().reply_with_tools(messages, tools=[], tool_choice="auto")
        return {
            "assistant_message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{self.model_call_count}_1",
                        "type": "function",
                        "function": {
                            "name": "schedule_list",
                            "arguments": json.dumps({}, ensure_ascii=False),
                        },
                    },
                    {
                        "id": f"call_{self.model_call_count}_2",
                        "type": "function",
                        "function": {
                            "name": "history_list",
                            "arguments": json.dumps({"limit": 10}, ensure_ascii=False),
                        },
                    },
                ],
            },
            "reasoning_content": None,
        }


def _history_tool_name_and_arguments(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    action = str(arguments.get("action") or "").strip().lower()
    tool_name = {"list": "history_list", "search": "history_search"}.get(action, "history_list")
    fields_by_tool: dict[str, tuple[str, ...]] = {
        "history_list": ("limit",),
        "history_search": ("keyword", "limit"),
    }
    payload: dict[str, Any] = {}
    for key in fields_by_tool.get(tool_name, ()):
        if key in arguments:
            payload[key] = arguments.get(key)
    return tool_name, payload


def _schedule_tool_name_and_arguments(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    action = str(arguments.get("action") or "").strip().lower()
    tool_name = {
        "add": "schedule_add",
        "list": "schedule_list",
        "view": "schedule_view",
        "get": "schedule_get",
        "update": "schedule_update",
        "delete": "schedule_delete",
        "repeat": "schedule_repeat",
    }.get(action, "schedule_list")
    fields_by_tool: dict[str, tuple[str, ...]] = {
        "schedule_add": (
            "event_time",
            "title",
            "tag",
            "duration_minutes",
            "remind_at",
            "interval_minutes",
            "times",
            "remind_start_time",
        ),
        "schedule_list": ("tag",),
        "schedule_view": ("view", "anchor", "tag"),
        "schedule_get": ("id",),
        "schedule_update": (
            "id",
            "event_time",
            "title",
            "tag",
            "duration_minutes",
            "remind_at",
            "interval_minutes",
            "times",
            "remind_start_time",
        ),
        "schedule_delete": ("id",),
        "schedule_repeat": ("id", "enabled"),
    }
    payload: dict[str, Any] = {}
    for key in fields_by_tool.get(tool_name, ()):
        if key in arguments:
            payload[key] = arguments.get(key)
    return tool_name, payload


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


class _BlockingLLMClient:
    def __init__(self, response: str) -> None:
        self._response = response
        self.started = threading.Event()
        self.release = threading.Event()

    def reply(self, _messages: list[dict[str, str]]) -> str:
        self.started.set()
        self.release.wait(timeout=2.0)
        return self._response


class AssistantAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp.name) / "assistant_test.db")
        self.db = AssistantDB(db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_version_command(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None, app_version="1.2.3")

        result = agent.handle_input("/version")

        self.assertEqual(result, "当前版本：v1.2.3")

    def test_version_command_rejects_extra_args(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None, app_version="1.2.3")

        result = agent.handle_input("/version verbose")

        self.assertEqual(result, "用法: /version")

    def test_version_command_returns_unknown_when_version_unavailable(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None, app_version="")

        result = agent.handle_input("/version")

        self.assertEqual(result, "当前版本：unknown")

    def test_handle_input_with_task_status_returns_false_for_slash_command(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        response, task_completed = agent.handle_input_with_task_status("/schedule list")

        self.assertIn("暂无日程", response)
        self.assertFalse(task_completed)

    def test_profile_refresh_command_returns_runner_output(self) -> None:
        profile_content = "# 最新画像\n- 偏好: 咖啡"
        call_count = 0

        def _runner() -> str:
            nonlocal call_count
            call_count += 1
            return profile_content

        agent = AssistantAgent(
            db=self.db,
            llm_client=None,
            user_profile_refresh_runner=_runner,
        )

        result = agent.handle_input("/profile refresh")

        self.assertEqual(result, profile_content)
        self.assertEqual(call_count, 1)

    def test_profile_refresh_command_handles_runner_error(self) -> None:
        def _runner() -> str:
            raise RuntimeError("refresh failed")

        agent = AssistantAgent(
            db=self.db,
            llm_client=None,
            user_profile_refresh_runner=_runner,
        )

        result = agent.handle_input("/profile refresh")

        self.assertIn("刷新 user_profile 失败", result)

    def test_profile_refresh_command_requires_runner(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        result = agent.handle_input("/profile refresh")

        self.assertIn("当前未启用 user_profile 刷新服务", result)

    def test_interrupt_current_task_stops_inflight_planner_loop(self) -> None:
        blocking_llm = _BlockingLLMClient(response=_planner_planned(["查看日程"]))
        agent = AssistantAgent(db=self.db, llm_client=blocking_llm)
        holder: dict[str, str] = {}

        def run_handle_input() -> None:
            holder["response"] = agent.handle_input("看一下日程")

        worker = threading.Thread(target=run_handle_input)
        worker.start()
        self.assertTrue(blocking_llm.started.wait(timeout=2.0))
        agent.interrupt_current_task()
        blocking_llm.release.set()
        worker.join(timeout=2.0)

        self.assertIn("已被新消息中断", holder.get("response", ""))

    def test_handle_input_persists_user_and_assistant_turns_for_non_slash_input(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        response = agent.handle_input("今天怎么安排")
        self.assertIn("当前未配置 LLM", response)

        history = self.db.recent_messages(limit=2)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].role, "user")
        self.assertEqual(history[0].content, "今天怎么安排")
        self.assertEqual(history[1].role, "assistant")
        self.assertIn("当前未配置 LLM", history[1].content)

    def test_slash_commands_are_not_persisted_to_history(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        response = agent.handle_input("/schedule list")
        self.assertIn("暂无日程", response)

        history = self.db.recent_messages(limit=2)
        self.assertEqual(history, [])

    def test_history_list_command_returns_recent_turns(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        first = agent.handle_input("/history list")
        self.assertEqual(first, "暂无历史会话。")

        agent.handle_input("今天怎么安排")
        second = agent.handle_input("/history list --limit 2")
        self.assertIn("历史会话(最近 1 轮)", second)
        self.assertIn("| # | 用户输入 | 最终回答 | 时间 |", second)
        self.assertIn("| 1 | 今天怎么安排 | 当前未配置 LLM。请设置 DEEPSEEK_API_KEY 后重试。 |", second)

        invalid = agent.handle_input("/history list --limit 0")
        self.assertIn("用法: /history list", invalid)

    def test_history_search_command_supports_fuzzy_keyword_match(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        self.db.save_turn(user_content="我要买牛奶", assistant_content="已记录你的购物日程")
        self.db.save_turn(user_content="今天安排", assistant_content="你今天 10:00 有站会")

        result = agent.handle_input("/history search 牛奶")
        self.assertIn("历史搜索(关键词: 牛奶, 命中 1 轮)", result)
        self.assertIn("| 我要买牛奶 | 已记录你的购物日程 |", result)

        by_assistant = agent.handle_input("/history search 10:00")
        self.assertIn("历史搜索(关键词: 10:00, 命中 1 轮)", by_assistant)
        self.assertIn("| 今天安排 | 你今天 10:00 有站会 |", by_assistant)

        limited = agent.handle_input("/history search 今天 --limit 1")
        self.assertIn("命中 1 轮", limited)

        missing = agent.handle_input("/history search 不存在的关键词")
        self.assertIn("未找到包含", missing)

        invalid = agent.handle_input("/history search --limit 2")
        self.assertIn("用法: /history search <关键词> [--limit <>=1>]", invalid)

    def test_thoughts_crud_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        empty = agent.handle_input("/thoughts list")
        self.assertEqual(empty, "暂无想法记录。")

        added = agent.handle_input("/thoughts add 记得买牛奶")
        self.assertIn("已记录想法 #1", added)
        self.assertIn("记得买牛奶", added)

        listed = agent.handle_input("/thoughts list")
        self.assertIn("想法列表(状态: 未完成|完成)", listed)
        self.assertIn("记得买牛奶", listed)
        self.assertIn("| 未完成 |", listed)

        detail = agent.handle_input("/thoughts get 1")
        self.assertIn("想法详情:", detail)
        self.assertIn("| 1 | 记得买牛奶 | 未完成 |", detail)

        updated = agent.handle_input("/thoughts update 1 记得买牛奶和鸡蛋 --status 完成")
        self.assertIn("已更新想法 #1: 记得买牛奶和鸡蛋 [状态:完成]", updated)

        filtered_done = agent.handle_input("/thoughts list --status 完成")
        self.assertIn("想法列表(状态: 完成)", filtered_done)
        self.assertIn("记得买牛奶和鸡蛋", filtered_done)

        deleted = agent.handle_input("/thoughts delete 1")
        self.assertEqual(deleted, "想法 #1 已删除。")

        listed_after_delete = agent.handle_input("/thoughts list")
        self.assertEqual(listed_after_delete, "暂无想法记录。")

        deleted_only = agent.handle_input("/thoughts list --status 删除")
        self.assertIn("想法列表(状态: 删除)", deleted_only)
        self.assertIn("记得买牛奶和鸡蛋", deleted_only)

    def test_thoughts_commands_validate_usage(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        invalid_add = agent.handle_input("/thoughts add")
        self.assertEqual(invalid_add, "用法: /thoughts add <内容>")

        invalid_list = agent.handle_input("/thoughts list --status 进行中")
        self.assertEqual(invalid_list, "用法: /thoughts list [--status <未完成|完成|删除>]")

        invalid_get = agent.handle_input("/thoughts get abc")
        self.assertEqual(invalid_get, "用法: /thoughts get <id>")

        invalid_update = agent.handle_input("/thoughts update 1 --status 完成")
        self.assertEqual(invalid_update, "用法: /thoughts update <id> <内容> [--status <未完成|完成|删除>]")

        invalid_delete = agent.handle_input("/thoughts delete nope")
        self.assertEqual(invalid_delete, "用法: /thoughts delete <id>")

    def test_thoughts_commands_emit_logs(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        with self.assertLogs("assistant_app.app", level="INFO") as captured:
            result = agent.handle_input("/thoughts add 记录日志")

        self.assertIn("已记录想法 #1", result)
        merged = "\n".join(captured.output)
        self.assertIn("thoughts_command_start", merged)
        self.assertIn("thoughts_command_done", merged)

    def test_thoughts_commands_delegate_to_shared_executor(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        observation = PlannerObservation(
            tool="thoughts",
            input_text="/thoughts add 记得买牛奶",
            ok=True,
            result="已记录想法 #9: 记得买牛奶",
        )

        with patch(
            "assistant_app.agent_components.command_handlers.execute_thoughts_system_action",
            return_value=observation,
        ) as mocked:
            result = agent.handle_input("/thoughts add 记得买牛奶")

        self.assertEqual(result, observation.result)
        mocked.assert_called_once()
        call_kwargs = mocked.call_args.kwargs
        self.assertEqual(call_kwargs["raw_input"], "/thoughts add 记得买牛奶")
        payload = call_kwargs["payload"]
        self.assertIsInstance(payload, RuntimePlannerActionPayload)
        self.assertEqual(payload.tool_name, "thoughts_add")
        self.assertEqual(payload.arguments.content, "记得买牛奶")

    def test_view_alias_commands_removed(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        alias_list = agent.handle_input("/view list")
        alias_today = agent.handle_input("/view today")

        self.assertEqual(alias_list, "未知命令。输入 /help 查看可用命令。")
        self.assertEqual(alias_today, "未知命令。输入 /help 查看可用命令。")

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
        self.assertIn("| 1 | 2026-02-20 09:30 | 60 | default | 站会 |", get_resp)

        update_resp = agent.handle_input("/schedule update 1 2026-02-21 10:00 复盘会")
        self.assertIn("已更新日程 #1 [标签:default]: 2026-02-21 10:00 复盘会 (60 分钟)", update_resp)

        update_duration_resp = agent.handle_input("/schedule update 1 2026-02-21 11:00 复盘会 --duration 45")
        self.assertIn("已更新日程 #1 [标签:default]: 2026-02-21 11:00 复盘会 (45 分钟)", update_duration_resp)
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
        base_time = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        second_time = base_time + timedelta(days=1)
        third_time = base_time + timedelta(days=2)
        base_text = base_time.strftime("%Y-%m-%d %H:%M")
        second_text = second_time.strftime("%Y-%m-%d %H:%M")
        third_text = third_time.strftime("%Y-%m-%d %H:%M")

        add_resp = agent.handle_input(f"/schedule add {base_text} 站会 --duration 30 --interval 1440 --times 3")
        self.assertIn("已添加重复日程 3 条", add_resp)
        self.assertIn("duration=30m", add_resp)
        self.assertIn("interval=1440m", add_resp)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn(base_text, list_resp)
        self.assertIn(second_text, list_resp)
        self.assertIn(third_text, list_resp)
        self.assertIn("| 1440 | 3 | on |", list_resp)
        self.assertIn("| 30 | default | 站会 |", list_resp)

        invalid = agent.handle_input(f"/schedule add {base_text} 站会 --times 3")
        self.assertIn("用法", invalid)

        invalid_times_one = agent.handle_input(f"/schedule add {base_text} 站会 --times 1")
        self.assertIn("用法", invalid_times_one)

        invalid_interval_times_one = agent.handle_input(f"/schedule add {base_text} 站会 --interval 1440 --times 1")
        self.assertIn("用法", invalid_interval_times_one)

        invalid_duration = agent.handle_input(f"/schedule add {base_text} 站会 --duration 0")
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

        invalid = agent.handle_input("/schedule add 2026-02-22 09:30 单次会 --remind-start 2026-02-22 09:00")
        self.assertIn("用法", invalid)

    def test_slash_schedule_repeat_default_times_is_infinite(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        base_time = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        next_time = base_time + timedelta(minutes=60)
        base_text = base_time.strftime("%Y-%m-%d %H:%M")
        next_text = next_time.strftime("%Y-%m-%d %H:%M")

        add_resp = agent.handle_input(f"/schedule add {base_text} 站会 --interval 60")
        self.assertIn("已添加无限重复日程", add_resp)
        self.assertIn("interval=60m", add_resp)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn(base_text, list_resp)
        self.assertIn(next_text, list_resp)

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

    def test_slash_schedule_allows_same_time_events(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 站会")

        added = agent.handle_input("/schedule add 2026-02-20 09:30 周会")
        self.assertIn("已添加日程 #2", added)
        self.assertEqual([item.title for item in self.db.list_schedules()], ["站会", "周会"])

    def test_slash_schedule_allows_duration_overlap(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 站会 --duration 60")

        overlap_added = agent.handle_input("/schedule add 2026-02-20 10:00 周会 --duration 30")
        self.assertIn("已添加日程 #2", overlap_added)

        non_overlap_ok = agent.handle_input("/schedule add 2026-02-20 10:30 复盘 --duration 30")
        self.assertIn("已添加日程 #3", non_overlap_ok)

    def test_slash_schedule_allows_repeat_overlap(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-27 09:30 固定会")

        repeated = agent.handle_input("/schedule add 2026-02-20 09:30 站会 --interval 10080 --times 2")
        self.assertIn("已添加重复日程 2 条", repeated)

    def test_slash_schedule_allows_infinite_repeat_overlap(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-15 10:00 固定会")

        repeated = agent.handle_input("/schedule add 2026-02-15 00:00 高频循环 --interval 1")
        self.assertIn("已添加无限重复日程 #2", repeated)

    def test_slash_schedule_repeat_update_commands(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        base_time = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        update_time = (base_time + timedelta(days=1)).replace(hour=10, minute=0)
        repeated_time = update_time + timedelta(days=7)
        base_text = base_time.strftime("%Y-%m-%d %H:%M")
        update_text = update_time.strftime("%Y-%m-%d %H:%M")
        repeated_text = repeated_time.strftime("%Y-%m-%d %H:%M")

        agent.handle_input(f"/schedule add {base_text} 站会 --duration 50")

        update_resp = agent.handle_input(f"/schedule update 1 {update_text} 复盘会 --interval 10080 --times 2")
        self.assertIn("times=2", update_resp)
        self.assertIn("duration=50m", update_resp)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn(update_text, list_resp)
        self.assertIn(repeated_text, list_resp)
        self.assertIn("| 50 | default | 复盘会 |", list_resp)

    def test_slash_schedule_tag_filter_and_update(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        base_time = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        work_text = base_time.strftime("%Y-%m-%d %H:%M")
        review_text = (base_time + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
        life_text = (base_time + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")

        agent.handle_input(f"/schedule add {work_text} 项目站会 --tag work")
        agent.handle_input(f"/schedule add {life_text} 生活采购 --tag life")

        work_list = agent.handle_input("/schedule list --tag work")
        self.assertIn("日程列表(前天起未来 31 天，标签:work)", work_list)
        self.assertIn("项目站会", work_list)
        self.assertNotIn("生活采购", work_list)

        detail = agent.handle_input("/schedule get 1")
        self.assertIn(f"| 1 | {work_text} | 60 | work | 项目站会 |", detail)

        update_resp = agent.handle_input(f"/schedule update 1 {review_text} 项目复盘 --tag review")
        self.assertIn("已更新日程 #1 [标签:review]", update_resp)

        review_day = review_text.split(" ", maxsplit=1)[0]
        review_view = agent.handle_input(f"/schedule view day {review_day} --tag review")
        self.assertIn(f"日历视图(day, {review_day}) [标签:review]", review_view)
        self.assertIn("项目复盘", review_view)
        self.assertNotIn("生活采购", review_view)

        invalid = agent.handle_input("/schedule list --tag")
        self.assertIn("用法: /schedule list [--tag <标签>]", invalid)

    def test_slash_schedule_update_clears_repeat_when_times_is_one(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        base_time = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        update_time = (base_time + timedelta(days=1)).replace(hour=10, minute=0)
        repeated_time = update_time + timedelta(days=7)
        base_text = base_time.strftime("%Y-%m-%d %H:%M")
        update_text = update_time.strftime("%Y-%m-%d %H:%M")
        repeated_text = repeated_time.strftime("%Y-%m-%d %H:%M")

        agent.handle_input(f"/schedule add {base_text} 站会 --interval 10080 --times 3")

        update_resp = agent.handle_input(f"/schedule update 1 {update_text} 复盘会")
        self.assertIn("已更新日程 #1", update_resp)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn(update_text, list_resp)
        self.assertNotIn(repeated_text, list_resp)

    def test_slash_schedule_repeat_toggle(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        base_time = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        repeated_time = base_time + timedelta(days=7)
        base_text = base_time.strftime("%Y-%m-%d %H:%M")
        repeated_text = repeated_time.strftime("%Y-%m-%d %H:%M")

        agent.handle_input(f"/schedule add {base_text} 站会 --interval 10080 --times 3")

        off_resp = agent.handle_input("/schedule repeat 1 off")
        self.assertIn("已停用日程 #1 的重复规则", off_resp)
        off_list = agent.handle_input("/schedule list")
        self.assertIn(base_text, off_list)
        self.assertNotIn(repeated_text, off_list)

        on_resp = agent.handle_input("/schedule repeat 1 on")
        self.assertIn("已启用日程 #1 的重复规则", on_resp)
        on_list = agent.handle_input("/schedule list")
        self.assertIn(repeated_text, on_list)

    def test_slash_schedule_repeat_toggle_without_rule(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 单次会")
        resp = agent.handle_input("/schedule repeat 1 off")
        self.assertIn("没有可切换的重复规则", resp)

    def test_slash_schedule_update_allows_overlap(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)
        agent.handle_input("/schedule add 2026-02-20 09:30 站会")
        agent.handle_input("/schedule add 2026-02-21 09:30 周会")

        updated = agent.handle_input("/schedule update 1 2026-02-21 09:30 复盘会")
        self.assertIn("已更新日程 #1", updated)

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

    def test_nl_schedule_flow_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增日程"]),
                _thought_continue("schedule", "/schedule add 2026-02-20 09:30 周会"),
                _planner_done("已添加日程。"),
                _planner_planned(["查看日程"]),
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
        base_time = (datetime.now() + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
        second_time = base_time + timedelta(days=7)
        third_time = base_time + timedelta(days=14)
        base_text = base_time.strftime("%Y-%m-%d %H:%M")
        second_text = second_time.strftime("%Y-%m-%d %H:%M")
        third_text = third_time.strftime("%Y-%m-%d %H:%M")

        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增重复日程"]),
                _thought_continue("schedule", f"/schedule add {base_text} 周会 --interval 10080 --times 3"),
                _planner_done("已添加重复日程 3 条。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        add_resp = agent.handle_input("每周加一个周会，连续三周")
        self.assertIn("已添加重复日程 3 条", add_resp)
        self.assertEqual(fake_llm.model_call_count, 3)

        list_resp = agent.handle_input("/schedule list")
        self.assertIn(base_text, list_resp)
        self.assertIn(second_text, list_resp)
        self.assertIn(third_text, list_resp)

    def test_nl_schedule_add_allows_overlap_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增日程", "总结结果"]),
                _thought_continue("schedule", "/schedule add 2026-02-20 09:30 周会"),
                _planner_done("已添加周会。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)
        self.db.add_schedule("站会", "2026-02-20 09:30")

        result = agent.handle_input("帮我加一个 2 月 20 号 9 点半周会")
        self.assertIn("已添加周会", result)
        self.assertEqual(len(self.db.list_schedules()), 2)

    def test_nl_schedule_view_via_intent_model(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["查看周视图"]),
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
                _planner_planned(["停用重复"]),
                _thought_continue("schedule", "/schedule repeat 1 off"),
                _planner_done("已停用日程 #1 的重复规则。"),
                _planner_planned(["启用重复"]),
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

    def test_replan_runs_after_each_subtask_loop(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["查看今天日程"]),
                _thought_continue("schedule", "/schedule view day 2026-02-16"),
                _planner_done("查看今天日程完成。"),
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

    def test_plan_initialization_notifies_expanded_goal_once(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["整理日程", "总结"], goal="先整理今天的日程，再给出执行建议"),
                _planner_done("整理完成。"),
                _planner_done("最终完成。"),
            ]
        )
        progress_updates: list[str] = []
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())
        agent.set_subtask_result_callback(progress_updates.append)

        response = agent.handle_input("帮我安排今天")

        self.assertIn("最终完成", response)
        self.assertEqual(progress_updates.count("任务目标：先整理今天的日程，再给出执行建议"), 1)

    def test_replan_does_not_emit_plan_goal_again(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["第一步", "第二步"], goal="先完成第一步，再完成第二步"),
                _planner_done("第一步完成。"),
                _planner_replanned(["第二步"]),
                _planner_done("第二步完成。"),
                _planner_done("最终完成。"),
            ]
        )
        progress_updates: list[str] = []
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())
        agent.set_subtask_result_callback(progress_updates.append)

        response = agent.handle_input("按顺序执行两步")

        self.assertIn("最终完成", response)
        goal_updates = [item for item in progress_updates if item.startswith("任务目标：")]
        self.assertEqual(goal_updates, ["任务目标：先完成第一步，再完成第二步"])
        self.assertIn("第一步已完成", progress_updates)

    def test_replan_done_does_not_notify_subtask_completion_result(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["收尾"]),
                _planner_done("收尾步骤完成。"),
                _planner_done("任务已完成。"),
            ]
        )
        progress_updates: list[str] = []
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())
        agent.set_subtask_result_callback(progress_updates.append)

        response = agent.handle_input("直接收尾")

        self.assertIn("任务已完成", response)
        self.assertEqual(progress_updates, ["任务目标：扩展后的目标"])

    def test_replan_premature_done_with_pending_non_summary_step_is_ignored(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["查询明天日程", "添加临时日程"]),
                _thought_continue("schedule", "/schedule view day 2026-03-07"),
                _planner_done("已查询明天日程。"),
                _planner_done("任务完成。明天的临时日程已添加，ID是#999。"),
                _thought_continue(
                    "schedule",
                    "/schedule add 2026-03-07 09:00 临时日程 --remind 2026-03-07 08:50",
                ),
                _planner_done("已添加临时日程。"),
                _planner_done("最终完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("明天先查一下日程，再加一个临时日程")

        self.assertIn("最终完成", response)
        matched = [
            item
            for item in self.db.list_schedules()
            if item.title == "临时日程" and item.event_time == "2026-03-07 09:00"
        ]
        self.assertEqual(len(matched), 1)
        phases = [_extract_phase_from_messages(call) for call in fake_llm.calls]
        self.assertIn("replan", phases)
        replan_index = phases.index("replan")
        self.assertIn("thought", phases[replan_index + 1 :])

    def test_replan_continue_without_completed_subtask_does_not_notify_subtask_completion_result(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["确认信息", "执行创建"]),
                _thought_ask_user("要添加什么日程？"),
                _planner_replanned(["执行创建"]),
                _planner_done("执行创建完成。"),
                _planner_done("最终完成。"),
            ]
        )
        progress_updates: list[str] = []
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())
        agent.set_subtask_result_callback(progress_updates.append)

        first = agent.handle_input("帮我加一个日程")
        self.assertIn("请确认", first)
        final = agent.handle_input("买牛奶")

        self.assertIn("最终完成", final)
        self.assertEqual(progress_updates, ["任务目标：扩展后的目标"])

    def test_thought_done_only_marks_subtask_and_replan_decides_final(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["汇总结果"]),
                _planner_done("汇总结果已完成。"),
                _planner_done("最终结论：今天有 1 条日程。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())
        self.db.add_schedule("站会", "2026-02-16 10:00")

        response = agent.handle_input("看一下今天的日程并总结")
        self.assertIn("最终结论", response)
        phases = [_extract_phase_from_messages(call) for call in fake_llm.calls]
        self.assertEqual(phases[:3], ["plan", "thought", "replan"])

    def test_plan_ack_only_empty_plan_skips_followup_and_history(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned([], goal="用户仅确认收到，无需额外动作"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response, task_completed = agent.handle_input_with_task_status("谢谢")

        self.assertEqual(response, "")
        self.assertTrue(task_completed)
        self.assertEqual(fake_llm.model_call_count, 1)
        phases = [_extract_phase_from_messages(call) for call in fake_llm.calls]
        self.assertEqual(phases, ["plan"])
        self.assertEqual(self.db.recent_messages(limit=2), [])

    def test_plan_ack_only_empty_plan_does_not_notify_expanded_goal(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned([], goal="仅确认收到，不需要任何执行"),
            ]
        )
        progress_updates: list[str] = []
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())
        agent.set_subtask_result_callback(progress_updates.append)

        response = agent.handle_input("谢谢")

        self.assertEqual(response, "")
        self.assertEqual(progress_updates, [])

    def test_plan_ack_only_accepts_missing_plan_field(self) -> None:
        payload = json.dumps(
            {
                "status": "planned",
                "goal": "用户仅确认收到，无需额外动作",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        fake_llm = FakeLLMClient(responses=[payload])
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response, task_completed = agent.handle_input_with_task_status("好的")

        self.assertEqual(response, "")
        self.assertTrue(task_completed)
        self.assertEqual(fake_llm.model_call_count, 1)
        self.assertEqual([_extract_phase_from_messages(call) for call in fake_llm.calls], ["plan"])
        self.assertEqual(self.db.recent_messages(limit=2), [])

    def test_plan_ack_only_accepts_null_plan_field(self) -> None:
        payload = json.dumps(
            {
                "status": "planned",
                "goal": "用户仅确认收到，无需额外动作",
                "plan": None,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        fake_llm = FakeLLMClient(responses=[payload])
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response, task_completed = agent.handle_input_with_task_status("明白了")

        self.assertEqual(response, "")
        self.assertTrue(task_completed)
        self.assertEqual(fake_llm.model_call_count, 1)
        self.assertEqual([_extract_phase_from_messages(call) for call in fake_llm.calls], ["plan"])
        self.assertEqual(self.db.recent_messages(limit=2), [])

    def test_plan_prompt_excludes_tool_and_time_contract(self) -> None:
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
        self.assertIn("看一下/看看/查一下", first_messages[0]["content"])
        self.assertIn("查询并列出来给用户查看", first_messages[0]["content"])
        self.assertIn("历史对话 messages 与 user_profile 补全默认信息", first_messages[0]["content"])
        self.assertIn("查询用户默认城市的明天天气，并输出天气结果与衣着建议", first_messages[0]["content"])
        self.assertIn("例如“谢谢”“好的”“明白了”", first_messages[0]["content"])
        self.assertIn("例如“好的，顺便帮我查明天天气”", first_messages[0]["content"])
        self.assertIn("tag（标签）", first_messages[0]["content"])
        self.assertNotIn("view（all|today|overdue|upcoming|inbox）", first_messages[0]["content"])
        self.assertIn("interval_minutes/times/remind_start_time（重复规则）", first_messages[0]["content"])
        self.assertIn("history：历史会话检索", first_messages[0]["content"])
        self.assertIn("历史对话", first_messages[0]["content"])
        planner_user_payload = _extract_payload_from_messages(first_messages)
        self.assertNotIn("tool_contract", planner_user_payload)
        self.assertNotIn("observations", planner_user_payload)
        self.assertNotIn("time_unit_contract", planner_user_payload)
        self.assertNotIn("pending_final_response", planner_user_payload)
        self.assertIn("completed_subtasks", planner_user_payload)
        self.assertNotIn("recent_chat_turns", planner_user_payload)

    def test_replan_prompt_excludes_tool_context_and_uses_completed_subtasks(self) -> None:
        expanded_goal = "查询默认城市天气并给出明日出行建议"
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["步骤一", "步骤二"], goal=expanded_goal),
                _planner_done("步骤一已完成。"),
                _planner_done("最终完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("测试 replan 上下文")
        self.assertIn("最终完成", response)

        replan_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "replan"]
        self.assertGreaterEqual(len(replan_calls), 1)
        replan_payload = _extract_payload_from_messages(replan_calls[0])
        self.assertNotIn("tool_contract", replan_payload)
        self.assertNotIn("observations", replan_payload)
        self.assertNotIn("time_unit_contract", replan_payload)
        self.assertNotIn("pending_final_response", replan_payload)
        self.assertNotIn("recent_chat_turns", replan_payload)
        self.assertEqual(replan_payload.get("goal"), expanded_goal)
        latest_plan = replan_payload.get("latest_plan", [])
        self.assertEqual(
            [(item.get("task"), item.get("completed")) for item in latest_plan],
            [("步骤一", True), ("步骤二", False)],
        )
        self.assertTrue(all(isinstance(item.get("tools"), list) for item in latest_plan))
        completed = replan_payload.get("completed_subtasks", [])
        self.assertTrue(completed)
        self.assertEqual(completed[0].get("item"), "步骤一")
        self.assertIn("步骤一已完成", completed[0].get("result", ""))

    def test_replan_payload_completed_subtasks_appends_history(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["步骤一", "步骤二"]),
                _planner_done("步骤一已完成。"),
                _planner_replanned(["步骤二"]),
                _planner_done("步骤二已完成。"),
                _planner_done("全部完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("测试 completed_subtasks 覆盖策略")
        self.assertIn("全部完成", response)

        replan_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "replan"]
        self.assertEqual(len(replan_calls), 2)
        second_replan_history_messages = _extract_history_messages(replan_calls[-1])
        self.assertIn(
            {"role": "assistant", "content": _planner_replanned(["步骤二"])},
            second_replan_history_messages,
        )
        last_replan_payload = _extract_payload_from_messages(replan_calls[-1])
        completed = last_replan_payload.get("completed_subtasks", [])
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[0].get("item"), "步骤一")
        self.assertEqual(completed[1].get("item"), "步骤二")
        self.assertIn("步骤一已完成", completed[0].get("result", ""))
        self.assertIn("步骤二已完成", completed[1].get("result", ""))

    def test_plan_and_replan_messages_include_recent_chat_turns_with_window_and_limit(self) -> None:
        for idx in range(1, 61):
            self.db.save_turn(user_content=f"问{idx}", assistant_content=f"答{idx}")
        conn = sqlite3.connect(self.db.db_path)
        try:
            conn.execute(
                "UPDATE chat_history SET created_at = ? WHERE id = 1",
                ((datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),),
            )
            conn.commit()
        finally:
            conn.close()

        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["步骤一"]),
                _planner_done("步骤一完成。"),
                _planner_done("最终完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("测试 plan/replan 历史窗口")
        self.assertIn("最终完成", response)

        plan_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "plan"]
        thought_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "thought"]
        replan_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "replan"]
        self.assertEqual(len(plan_calls), 1)
        self.assertEqual(len(thought_calls), 1)
        self.assertEqual(len(replan_calls), 1)

        plan_payload = _extract_payload_from_messages(plan_calls[0])
        thought_payload = _extract_payload_from_messages(thought_calls[0])
        replan_payload = _extract_payload_from_messages(replan_calls[0])
        self.assertNotIn("recent_chat_turns", plan_payload)
        self.assertNotIn("recent_chat_turns", thought_payload)
        self.assertNotIn("recent_chat_turns", replan_payload)

        plan_history_messages = _extract_history_messages(plan_calls[0])
        thought_history_messages = _extract_history_messages(thought_calls[0])
        replan_history_messages = _extract_history_messages(replan_calls[0])
        self.assertEqual(len(plan_history_messages), 100)
        self.assertEqual(len(thought_history_messages), 102)
        self.assertEqual(len(replan_history_messages), 102)
        self.assertEqual(plan_history_messages[0], {"role": "user", "content": "问11"})
        self.assertEqual(plan_history_messages[1], {"role": "assistant", "content": "答11"})
        self.assertEqual(plan_history_messages[-2], {"role": "user", "content": "问60"})
        self.assertEqual(plan_history_messages[-1], {"role": "assistant", "content": "答60"})
        self.assertNotIn({"role": "user", "content": "问1"}, plan_history_messages)
        self.assertEqual(
            thought_history_messages[-2],
            {
                "role": "user",
                "content": json.dumps(plan_payload, ensure_ascii=False),
            },
        )
        self.assertEqual(
            thought_history_messages[-1],
            {
                "role": "assistant",
                "content": _planner_planned(["步骤一"]),
            },
        )
        self.assertEqual(replan_history_messages, thought_history_messages)

    def test_plan_and_replan_payload_include_user_profile_when_configured(self) -> None:
        profile_file = Path(self.tmp.name) / "user_profile.md"
        profile_file.write_text("昵称: 凛\n偏好: 先结论后细节", encoding="utf-8")
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["步骤一"]),
                _planner_done("步骤一完成。"),
                _planner_done("最终完成。"),
            ]
        )
        with patch("assistant_app.agent.PROJECT_ROOT", Path(self.tmp.name)):
            agent = AssistantAgent(
                db=self.db,
                llm_client=fake_llm,
                search_provider=FakeSearchProvider(),
                user_profile_path="user_profile.md",
            )

        response = agent.handle_input("测试 plan/replan 用户画像注入")
        self.assertIn("最终完成", response)

        plan_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "plan"]
        replan_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "replan"]
        thought_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "thought"]
        self.assertEqual(len(plan_calls), 1)
        self.assertEqual(len(replan_calls), 1)
        self.assertTrue(thought_calls)

        expected_profile = "昵称: 凛\n偏好: 先结论后细节"
        plan_payload = json.loads(plan_calls[0][-1]["content"])
        replan_payload = json.loads(replan_calls[0][-1]["content"])
        first_thought_payload = json.loads(thought_calls[0][-1]["content"])
        self.assertEqual(plan_payload.get("user_profile"), expected_profile)
        self.assertEqual(replan_payload.get("user_profile"), expected_profile)
        self.assertEqual(first_thought_payload.get("user_profile"), expected_profile)

    def test_plan_and_replan_payload_user_profile_is_none_when_file_missing(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["步骤一"]),
                _planner_done("步骤一完成。"),
                _planner_done("最终完成。"),
            ]
        )
        with patch("assistant_app.agent.PROJECT_ROOT", Path(self.tmp.name)):
            agent = AssistantAgent(
                db=self.db,
                llm_client=fake_llm,
                search_provider=FakeSearchProvider(),
                user_profile_path="missing_profile.md",
            )

        response = agent.handle_input("测试缺失画像文件降级")
        self.assertIn("最终完成", response)

        plan_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "plan"]
        thought_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "thought"]
        replan_calls = [call for call in fake_llm.calls if _extract_phase_from_messages(call) == "replan"]
        self.assertEqual(len(plan_calls), 1)
        self.assertEqual(len(thought_calls), 1)
        self.assertEqual(len(replan_calls), 1)
        plan_payload = json.loads(plan_calls[0][-1]["content"])
        thought_payload = json.loads(thought_calls[0][-1]["content"])
        replan_payload = json.loads(replan_calls[0][-1]["content"])
        self.assertIsNone(plan_payload.get("user_profile"))
        self.assertIsNone(thought_payload.get("user_profile"))
        self.assertIsNone(replan_payload.get("user_profile"))

    def test_reload_user_profile_refreshes_content(self) -> None:
        profile_file = Path(self.tmp.name) / "user_profile.md"
        profile_file.write_text("偏好: 咖啡", encoding="utf-8")
        with patch("assistant_app.agent.PROJECT_ROOT", Path(self.tmp.name)):
            agent = AssistantAgent(
                db=self.db,
                llm_client=None,
                user_profile_path="user_profile.md",
            )
        self.assertIn("咖啡", agent._serialize_user_profile() or "")

        profile_file.write_text("偏好: 红茶", encoding="utf-8")
        reloaded = agent.reload_user_profile()

        self.assertTrue(reloaded)
        self.assertIn("红茶", agent._serialize_user_profile() or "")

    def test_user_profile_too_long_raises_on_agent_init(self) -> None:
        profile_file = Path(self.tmp.name) / "user_profile.md"
        profile_file.write_text("a" * 6001, encoding="utf-8")

        with self.assertRaises(ValueError):
            AssistantAgent(
                db=self.db,
                llm_client=None,
                user_profile_path=str(profile_file),
            )

    def test_thought_tool_calling_expands_internet_search_group_tools(self) -> None:
        fake_llm = FakeToolCallingLLMClient(
            responses=[
                _planner_planned(
                    ["搜索信息", "总结"],
                    tools_by_task={"搜索信息": ["internet_search"], "总结": []},
                ),
                _thought_continue("internet_search", "OpenAI Responses API"),
                _planner_done("搜索完成。"),
                _planner_done("全部完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("帮我搜索 Responses API")
        self.assertIn("全部完成", response)
        self.assertTrue(fake_llm.tool_schema_calls)
        first_tool_names = _extract_tool_names_from_schemas(fake_llm.tool_schema_calls[0])
        self.assertEqual(
            set(first_tool_names),
            {"internet_search_tool", "internet_search_fetch_url", "ask_user", "done"},
        )
        self.assertNotIn("internet_search", first_tool_names)

    def test_thought_tool_calling_expands_schedule_group_tools(self) -> None:
        fake_llm = FakeToolCallingLLMClient(
            responses=[
                _planner_planned(
                    ["查看日程", "总结"],
                    tools_by_task={"查看日程": ["schedule"], "总结": []},
                ),
                _thought_continue("schedule", "/schedule list"),
                _planner_done("日程已查看。"),
                _planner_done("全部完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("帮我看一下日程")
        self.assertIn("全部完成", response)
        self.assertTrue(fake_llm.tool_schema_calls)
        first_tool_names = _extract_tool_names_from_schemas(fake_llm.tool_schema_calls[0])
        expected_schedule_tools = {
            "schedule_add",
            "schedule_list",
            "schedule_view",
            "schedule_get",
            "schedule_update",
            "schedule_delete",
            "schedule_repeat",
        }
        self.assertTrue(expected_schedule_tools.issubset(set(first_tool_names)))
        self.assertNotIn("schedule", first_tool_names)
        self.assertEqual(first_tool_names.count("ask_user"), 1)
        self.assertEqual(first_tool_names.count("done"), 1)

    def test_thought_tool_calling_expands_thoughts_group_tools(self) -> None:
        fake_llm = FakeToolCallingLLMClient(
            responses=[
                _planner_planned(
                    ["记录想法", "总结"],
                    tools_by_task={"记录想法": ["thoughts"], "总结": []},
                ),
                _planner_done("想法记录完成。"),
                _planner_done("全部完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("帮我记一条碎片想法")
        self.assertIn("全部完成", response)
        self.assertTrue(fake_llm.tool_schema_calls)
        first_tool_names = _extract_tool_names_from_schemas(fake_llm.tool_schema_calls[0])
        expected_thoughts_tools = {
            "thoughts_add",
            "thoughts_list",
            "thoughts_get",
            "thoughts_update",
            "thoughts_delete",
            "ask_user",
            "done",
        }
        self.assertEqual(set(first_tool_names), expected_thoughts_tools)
        self.assertNotIn("thoughts", first_tool_names)

    def test_thought_tool_calling_accepts_typed_tool_reply_payload(self) -> None:
        fake_llm = FakeTypedToolCallingLLMClient(
            responses=[
                _planner_planned(["查看日程", "总结"]),
                _thought_continue("schedule", "/schedule list"),
                _planner_done("日程已查看。"),
                _planner_done("全部完成。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("帮我用 typed payload 看日程")

        self.assertIn("全部完成", response)
        self.assertTrue(fake_llm.tool_schema_calls)

    def test_thought_tool_calling_rejects_multiple_tool_calls(self) -> None:
        fake_llm = FakeMultiToolCallingLLMClient(
            responses=[
                _planner_planned(["查看日程", "总结"]),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("测试 multi tool call 拒绝")

        self.assertIn("每轮最多调用 1 个工具", response)

    def test_llm_request_and_response_are_logged(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["收尾"]),
                _planner_done("完成。"),
            ]
        )
        stream = io.StringIO()
        logger = logging.getLogger("tests.llm_trace")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        try:
            agent = AssistantAgent(
                db=self.db,
                llm_client=fake_llm,
                search_provider=FakeSearchProvider(),
                llm_trace_logger=logger,
            )
            response = agent.handle_input("测试 llm 交互日志")
            self.assertIn("完成", response)
        finally:
            logger.removeHandler(handler)
            handler.close()

        records = _parse_json_lines(stream.getvalue())
        events = [item.get("event") for item in records]
        self.assertIn("llm_request", events)
        self.assertIn("llm_response", events)
        self.assertNotIn("planner_payload_validation_failed", events)
        self.assertNotIn("thought_tool_arguments_validation_failed", events)
        phases = [str(item.get("phase")) for item in records if item.get("event") == "llm_request"]
        self.assertIn("plan", phases)
        self.assertIn("thought", phases)
        self.assertIn("replan", phases)

    def test_planner_payload_validation_failure_is_logged_for_invalid_json_response(self) -> None:
        fake_llm = FakeLLMClient(responses=["not-json"])
        stream = io.StringIO()
        logger = logging.getLogger("tests.llm_trace.invalid_plan")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        try:
            agent = AssistantAgent(
                db=self.db,
                llm_client=fake_llm,
                search_provider=FakeSearchProvider(),
                llm_trace_logger=logger,
                plan_replan_retry_count=0,
            )
            agent.handle_input("测试 planner 非法响应")
        finally:
            logger.removeHandler(handler)
            handler.close()

        records = _parse_json_lines(stream.getvalue())
        failure_events = [item for item in records if item.get("event") == "planner_payload_validation_failed"]
        self.assertEqual(len(failure_events), 1)
        self.assertEqual(failure_events[0].get("phase"), "plan")
        self.assertEqual(failure_events[0].get("reason"), "response_not_json_object")
        self.assertEqual(failure_events[0].get("payload_type"), "invalid_json")

    def test_thought_tool_argument_validation_failure_is_logged(self) -> None:
        class _InvalidThoughtToolArgsLLM(FakeToolCallingLLMClient):
            def reply_with_tools(
                self,
                messages: list[dict[str, Any]],
                *,
                tools: list[dict[str, Any]],
                tool_choice: str = "auto",
            ) -> dict[str, Any]:
                phase = _extract_phase_from_messages(messages)
                if phase != "thought":
                    return super().reply_with_tools(messages, tools=tools, tool_choice=tool_choice)
                self.calls.append(messages)
                self.tool_schema_calls.append(deepcopy(tools))
                self.model_call_count += 1
                return {
                    "assistant_message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_invalid",
                                "type": "function",
                                "function": {
                                    "name": "internet_search_fetch_url",
                                    "arguments": json.dumps({"url": "ftp://example.com"}, ensure_ascii=False),
                                },
                            }
                        ],
                    },
                    "reasoning_content": None,
                }

        fake_llm = _InvalidThoughtToolArgsLLM(responses=[_planner_planned(["抓取网页"])])
        stream = io.StringIO()
        logger = logging.getLogger("tests.llm_trace.invalid_thought_args")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        try:
            agent = AssistantAgent(
                db=self.db,
                llm_client=fake_llm,
                search_provider=FakeSearchProvider(),
                llm_trace_logger=logger,
                plan_replan_retry_count=0,
                plan_replan_max_steps=1,
            )
            agent.handle_input("测试 thought 工具参数非法")
        finally:
            logger.removeHandler(handler)
            handler.close()

        records = _parse_json_lines(stream.getvalue())
        failure_events = [item for item in records if item.get("event") == "thought_tool_arguments_validation_failed"]
        self.assertEqual(len(failure_events), 1)
        self.assertEqual(failure_events[0].get("phase"), "thought")
        self.assertEqual(failure_events[0].get("tool_name"), "internet_search_fetch_url")
        self.assertEqual(failure_events[0].get("reason"), "arguments_schema_invalid_or_unknown_tool")

    def test_agent_default_trace_logger_uses_null_handler_without_propagation(self) -> None:
        logger = logging.getLogger("assistant_app.llm_trace")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            logger.propagate = True
            AssistantAgent(db=self.db, llm_client=None)
            self.assertFalse(logger.propagate)
            self.assertTrue(logger.handlers)
            self.assertIsInstance(logger.handlers[0], logging.NullHandler)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

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

        first = agent.handle_input("帮我新增一个日程")
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

    def test_thought_contract_accepts_continue_with_history_tool(self) -> None:
        decision = normalize_thought_decision(
            {
                "status": "continue",
                "current_step": "检索历史",
                "next_action": {"tool": "history", "input": "/history search 牛奶"},
                "question": None,
                "response": None,
            }
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.model_dump()["next_action"]["tool"], "history")
        assert decision.next_action.payload is not None
        self.assertEqual(decision.next_action.payload.tool_name, "history_search")
        self.assertEqual(decision.next_action.payload.arguments.keyword, "牛奶")

    def test_thought_tool_call_contract_maps_internet_search_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "internet_search_tool",
                    "arguments": json.dumps({"query": "OpenAI Responses API"}, ensure_ascii=False),
                },
            }
        )
        self.assertEqual(
            decision.model_dump(),
            {
                "status": "continue",
                "current_step": "",
                "next_action": {"tool": "internet_search", "input": "OpenAI Responses API"},
                "question": None,
                "response": None,
            },
        )

    def test_thought_tool_call_contract_maps_internet_search_fetch_url(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_1_fetch_url",
                "type": "function",
                "function": {
                    "name": "internet_search_fetch_url",
                    "arguments": json.dumps({"url": "https://example.com"}, ensure_ascii=False),
                },
            }
        )
        self.assertEqual(
            decision.model_dump(),
            {
                "status": "continue",
                "current_step": "",
                "next_action": {
                    "tool": "internet_search",
                    "input": json.dumps(
                        {"action": "fetch_url", "url": "https://example.com"},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
                "question": None,
                "response": None,
            },
        )

    def test_thought_tool_call_contract_rejects_legacy_internet_search_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_legacy_internet_search",
                "type": "function",
                "function": {
                    "name": "internet_search",
                    "arguments": json.dumps({"query": "OpenAI Responses API"}, ensure_ascii=False),
                },
            }
        )
        self.assertIsNone(decision)

    def test_thought_tool_call_contract_maps_done_action(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "done",
                    "arguments": json.dumps({"response": "步骤完成。", "current_step": "总结"}, ensure_ascii=False),
                },
            }
        )
        self.assertEqual(
            decision.model_dump(),
            {
                "status": "done",
                "current_step": "总结",
                "next_action": None,
                "question": None,
                "response": "步骤完成。",
            },
        )

    def test_thought_tool_call_contract_maps_history_list_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_history_list",
                "type": "function",
                "function": {
                    "name": "history_list",
                    "arguments": json.dumps({"limit": 5}, ensure_ascii=False),
                },
            }
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        next_action = decision.model_dump()["next_action"]
        self.assertEqual(next_action["tool"], "history")
        input_payload = _try_parse_json(str(next_action["input"] or ""))
        self.assertEqual(input_payload, {"action": "list", "limit": 5})

    def test_thought_tool_call_contract_maps_history_search_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_history_search",
                "type": "function",
                "function": {
                    "name": "history_search",
                    "arguments": json.dumps({"keyword": "牛奶", "limit": 3}, ensure_ascii=False),
                },
            }
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        next_action = decision.model_dump()["next_action"]
        self.assertEqual(next_action["tool"], "history")
        input_payload = _try_parse_json(str(next_action["input"] or ""))
        self.assertEqual(input_payload, {"action": "search", "keyword": "牛奶", "limit": 3})
        assert decision.next_action.payload is not None
        self.assertEqual(decision.next_action.payload.tool_name, "history_search")

    def test_thought_tool_call_contract_rejects_legacy_history_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_legacy_history",
                "type": "function",
                "function": {
                    "name": "history",
                    "arguments": json.dumps({"action": "search", "keyword": "牛奶"}, ensure_ascii=False),
                },
            }
        )
        self.assertIsNone(decision)

    def test_thought_tool_call_contract_maps_thoughts_add_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_thoughts_add",
                "type": "function",
                "function": {
                    "name": "thoughts_add",
                    "arguments": json.dumps({"content": "记得补充周报"}, ensure_ascii=False),
                },
            }
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        next_action = decision.model_dump()["next_action"]
        self.assertEqual(next_action["tool"], "thoughts")
        input_payload = _try_parse_json(str(next_action["input"] or ""))
        self.assertEqual(input_payload, {"action": "add", "content": "记得补充周报"})
        assert decision.next_action.payload is not None
        self.assertEqual(decision.next_action.payload.tool_name, "thoughts_add")

    def test_thought_tool_call_contract_maps_thoughts_update_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_thoughts_update",
                "type": "function",
                "function": {
                    "name": "thoughts_update",
                    "arguments": json.dumps(
                        {"id": 2, "content": "记得补充周报并发给团队", "status": "完成"},
                        ensure_ascii=False,
                    ),
                },
            }
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        next_action = decision.model_dump()["next_action"]
        self.assertEqual(next_action["tool"], "thoughts")
        input_payload = _try_parse_json(str(next_action["input"] or ""))
        self.assertEqual(
            input_payload,
            {"action": "update", "id": 2, "content": "记得补充周报并发给团队", "status": "完成"},
        )
        assert decision.next_action.payload is not None
        self.assertEqual(decision.next_action.payload.tool_name, "thoughts_update")

    def test_thought_tool_call_contract_rejects_legacy_thoughts_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_legacy_thoughts",
                "type": "function",
                "function": {
                    "name": "thoughts",
                    "arguments": json.dumps({"action": "list"}, ensure_ascii=False),
                },
            }
        )
        self.assertIsNone(decision)

    def test_thought_tool_call_contract_maps_schedule_update_tool_with_tag(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_4",
                "type": "function",
                "function": {
                    "name": "schedule_update",
                    "arguments": json.dumps(
                        {
                            "id": 1,
                            "event_time": "2026-03-01 10:00",
                            "title": "站会",
                            "tag": "work",
                        },
                        ensure_ascii=False,
                    ),
                },
            }
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        next_action = decision.model_dump()["next_action"]
        self.assertEqual(next_action["tool"], "schedule")
        input_payload = _try_parse_json(str(next_action["input"] or ""))
        self.assertEqual(
            input_payload,
            {
                "action": "update",
                "id": 1,
                "event_time": "2026-03-01 10:00",
                "title": "站会",
                "tag": "work",
            },
        )

    def test_thought_tool_call_contract_maps_schedule_view_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_schedule_view",
                "type": "function",
                "function": {
                    "name": "schedule_view",
                    "arguments": json.dumps(
                        {"view": "week", "anchor": "2026-03-02", "tag": "work"},
                        ensure_ascii=False,
                    ),
                },
            }
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        next_action = decision.model_dump()["next_action"]
        self.assertEqual(next_action["tool"], "schedule")
        input_payload = _try_parse_json(str(next_action["input"] or ""))
        self.assertEqual(input_payload, {"action": "view", "view": "week", "anchor": "2026-03-02", "tag": "work"})

    def test_thought_tool_call_contract_maps_schedule_list_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_schedule_list",
                "type": "function",
                "function": {
                    "name": "schedule_list",
                    "arguments": json.dumps({"tag": "work"}, ensure_ascii=False),
                },
            }
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        next_action = decision.model_dump()["next_action"]
        self.assertEqual(next_action["tool"], "schedule")
        input_payload = _try_parse_json(str(next_action["input"] or ""))
        self.assertEqual(input_payload, {"action": "list", "tag": "work"})

    def test_thought_tool_call_contract_maps_schedule_repeat_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_schedule_repeat",
                "type": "function",
                "function": {
                    "name": "schedule_repeat",
                    "arguments": json.dumps({"id": 3, "enabled": False}, ensure_ascii=False),
                },
            }
        )
        self.assertIsNotNone(decision)
        assert decision is not None
        next_action = decision.model_dump()["next_action"]
        self.assertEqual(next_action["tool"], "schedule")
        input_payload = _try_parse_json(str(next_action["input"] or ""))
        self.assertEqual(input_payload, {"action": "repeat", "id": 3, "enabled": False})

    def test_thought_tool_call_contract_rejects_legacy_schedule_tool(self) -> None:
        decision = normalize_thought_tool_call(
            {
                "id": "call_legacy_schedule",
                "type": "function",
                "function": {
                    "name": "schedule",
                    "arguments": json.dumps({"action": "list"}, ensure_ascii=False),
                },
            }
        )
        self.assertIsNone(decision)

    def test_plan_replan_internet_search_tool(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["搜索资料"]),
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

    def test_internet_search_observation_respects_configured_top_k(self) -> None:
        fake_search = FakeSearchProvider(
            results=[
                SearchResult(title="A", snippet="S1", url="https://example.com/a"),
                SearchResult(title="B", snippet="S2", url="https://example.com/b"),
                SearchResult(title="C", snippet="S3", url="https://example.com/c"),
                SearchResult(title="D", snippet="S4", url="https://example.com/d"),
                SearchResult(title="E", snippet="S5", url="https://example.com/e"),
            ]
        )
        agent = AssistantAgent(
            db=self.db,
            llm_client=FakeLLMClient(),
            search_provider=fake_search,
            internet_search_top_k=5,
        )

        with self.assertLogs("assistant_app.app", level="INFO") as captured:
            observation = agent._execute_planner_tool(action_tool="internet_search", action_input="OpenAI")

        self.assertTrue(observation.ok)
        self.assertEqual(fake_search.queries, [("OpenAI", 5)])
        self.assertIn("互联网搜索结果（返回 5 条，目标 Top 5）", observation.result)
        self.assertIn("5. E", observation.result)
        merged = "\n".join(captured.output)
        self.assertIn("planner_tool_internet_search_start", merged)
        self.assertIn("planner_tool_internet_search_done", merged)

    def test_internet_search_observation_logs_failed(self) -> None:
        fake_search = FakeSearchProvider(raises=RuntimeError("timeout"))
        agent = AssistantAgent(
            db=self.db,
            llm_client=FakeLLMClient(),
            search_provider=fake_search,
            internet_search_top_k=4,
        )

        with self.assertLogs("assistant_app.app", level="INFO") as captured:
            observation = agent._execute_planner_tool(action_tool="internet_search", action_input="OpenAI")

        self.assertFalse(observation.ok)
        self.assertIn("搜索失败", observation.result)
        merged = "\n".join(captured.output)
        self.assertIn("planner_tool_internet_search_start", merged)
        self.assertIn("planner_tool_internet_search_failed", merged)

    def test_internet_search_url_input_auto_routes_to_fetch_url(self) -> None:
        fake_search = FakeSearchProvider(
            results=[
                SearchResult(title="A", snippet="S1", url="https://example.com/a"),
            ]
        )
        agent = AssistantAgent(
            db=self.db,
            llm_client=FakeLLMClient(),
            search_provider=fake_search,
        )
        with patch("assistant_app.agent.fetch_webpage_main_text") as mocked_fetch:
            mocked_fetch.return_value = SimpleNamespace(url="https://example.com", main_text="网页正文")
            observation = agent._execute_planner_tool(
                action_tool="internet_search",
                action_input="https://example.com",
            )

        self.assertTrue(observation.ok)
        result_payload = _try_parse_json(observation.result)
        self.assertEqual(result_payload, {"url": "https://example.com", "main_text": "网页正文"})
        self.assertEqual(fake_search.queries, [])
        mocked_fetch.assert_called_once_with("https://example.com")

    def test_internet_search_url_input_rejects_malformed_direct_url(self) -> None:
        agent = AssistantAgent(
            db=self.db,
            llm_client=FakeLLMClient(),
            search_provider=FakeSearchProvider(),
        )

        observation = agent._execute_planner_tool(
            action_tool="internet_search",
            action_input="http://",
        )

        self.assertFalse(observation.ok)
        self.assertEqual(
            observation.result,
            "internet_search.fetch_url url 非法，需为 http:// 或 https:// 开头。",
        )

    def test_internet_search_fetch_url_observation_success(self) -> None:
        agent = AssistantAgent(
            db=self.db,
            llm_client=FakeLLMClient(),
            search_provider=FakeSearchProvider(),
        )
        action_input = json.dumps(
            {"action": "fetch_url", "url": "https://example.com"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with patch("assistant_app.agent.fetch_webpage_main_text") as mocked_fetch:
            mocked_fetch.return_value = SimpleNamespace(url="https://example.com", main_text="网页正文")
            with self.assertLogs("assistant_app.app", level="INFO") as captured:
                observation = agent._execute_planner_tool(action_tool="internet_search", action_input=action_input)

        self.assertTrue(observation.ok)
        result_payload = _try_parse_json(observation.result)
        self.assertEqual(result_payload, {"url": "https://example.com", "main_text": "网页正文"})
        mocked_fetch.assert_called_once_with("https://example.com")
        merged = "\n".join(captured.output)
        self.assertIn("planner_tool_internet_search_fetch_url_start", merged)
        self.assertIn("planner_tool_internet_search_fetch_url_done", merged)

    def test_internet_search_fetch_url_observation_rejects_invalid_url(self) -> None:
        agent = AssistantAgent(
            db=self.db,
            llm_client=FakeLLMClient(),
            search_provider=FakeSearchProvider(),
        )
        action_input = json.dumps(
            {"action": "fetch_url", "url": "ftp://example.com/resource"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        observation = agent._execute_planner_tool(action_tool="internet_search", action_input=action_input)

        self.assertFalse(observation.ok)
        self.assertIn("url 非法", observation.result)

    def test_internet_search_fetch_url_observation_logs_failed(self) -> None:
        agent = AssistantAgent(
            db=self.db,
            llm_client=FakeLLMClient(),
            search_provider=FakeSearchProvider(),
        )
        action_input = json.dumps(
            {"action": "fetch_url", "url": "https://example.com"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with patch("assistant_app.agent.fetch_webpage_main_text", side_effect=RuntimeError("timeout")):
            with self.assertLogs("assistant_app.app", level="INFO") as captured:
                observation = agent._execute_planner_tool(action_tool="internet_search", action_input=action_input)

        self.assertFalse(observation.ok)
        self.assertIn("网页抓取失败", observation.result)
        merged = "\n".join(captured.output)
        self.assertIn("planner_tool_internet_search_fetch_url_start", merged)
        self.assertIn("planner_tool_internet_search_fetch_url_failed", merged)

    def test_internet_search_fetch_url_supports_runtime_typed_payload(self) -> None:
        agent = AssistantAgent(
            db=self.db,
            llm_client=FakeLLMClient(),
            search_provider=FakeSearchProvider(),
        )
        with patch("assistant_app.agent.fetch_webpage_main_text") as mocked_fetch:
            mocked_fetch.return_value = SimpleNamespace(url="https://example.com", main_text="网页正文")
            with self.assertLogs("assistant_app.app", level="INFO") as captured:
                observation = agent._execute_planner_tool(
                    action_tool="internet_search",
                    action_input="not-json",
                    action_payload=RuntimePlannerActionPayload(
                        tool_name="internet_search_fetch_url",
                        arguments=InternetSearchFetchUrlArgs(url="https://example.com"),
                    ),
                )

        self.assertTrue(observation.ok)
        result_payload = _try_parse_json(observation.result)
        self.assertEqual(result_payload, {"url": "https://example.com", "main_text": "网页正文"})
        mocked_fetch.assert_called_once_with("https://example.com")
        merged = "\n".join(captured.output)
        self.assertIn("planner_tool_internet_search_fetch_url_start", merged)
        self.assertIn("planner_tool_internet_search_fetch_url_done", merged)

    def test_plan_replan_history_tool(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["检索历史"]),
                _thought_continue("history", "/history search 牛奶"),
                _planner_done("我找到了 1 条相关历史。"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())
        self.db.save_turn(user_content="我要买牛奶", assistant_content="已记录买牛奶日程")

        response = agent.handle_input("帮我查下之前关于牛奶的记录")
        self.assertIn("1 条相关历史", response)
        self.assertEqual(fake_llm.model_call_count, 3)

    def test_cancel_pending_plan_task(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["确认目标日程", "执行更新"]),
                _thought_ask_user("你想操作哪个日程 id？", current_step="确认目标日程"),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        ask = agent.handle_input("帮我更新日程")
        self.assertEqual(ask, "请确认：你想操作哪个日程 id？")
        cancel = agent.handle_input("取消当前任务")
        self.assertEqual(cancel, "已取消当前任务。")

    def test_plan_contract_requires_tools_field(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                json.dumps(
                    {
                        "status": "planned",
                        "goal": "扩展后的目标",
                        "plan": [{"task": "步骤一", "completed": False}],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm, search_provider=FakeSearchProvider())

        response = agent.handle_input("测试 plan tools 缺失")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertEqual(fake_llm.model_call_count, 4)

    def test_history_tool_supports_list_and_search_actions(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())
        self.db.save_turn(user_content="我要买牛奶", assistant_content="已记录买牛奶日程")

        list_observation = agent._execute_planner_tool(
            action_tool="history",
            action_input='{"action":"list","limit":5}',
        )
        search_observation = agent._execute_planner_tool(
            action_tool="history",
            action_input='{"action":"search","keyword":"牛奶","limit":5}',
        )

        self.assertTrue(list_observation.ok)
        self.assertIn("历史会话(最近 1 轮)", list_observation.result)
        self.assertTrue(search_observation.ok)
        self.assertIn("历史搜索(关键词: 牛奶", search_observation.result)

    def test_history_tool_supports_runtime_typed_payload(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())
        self.db.save_turn(user_content="我要买牛奶", assistant_content="已记录买牛奶日程")

        observation = agent._execute_planner_tool(
            action_tool="history",
            action_input="not-json",
            action_payload=RuntimePlannerActionPayload(
                tool_name="history_search",
                arguments=HistorySearchArgs(keyword="牛奶", limit=5),
            ),
        )

        self.assertTrue(observation.ok)
        self.assertIn("历史搜索(关键词: 牛奶", observation.result)

    def test_history_tool_validation_for_limit_and_keyword(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())

        invalid_limit = agent._execute_planner_tool(
            action_tool="history",
            action_input='{"action":"list","limit":0}',
        )
        missing_keyword = agent._execute_planner_tool(
            action_tool="history",
            action_input='{"action":"search","limit":3}',
        )

        self.assertFalse(invalid_limit.ok)
        self.assertEqual(invalid_limit.result, "history.list limit 必须为正整数。")
        self.assertFalse(missing_keyword.ok)
        self.assertEqual(missing_keyword.result, "history.search keyword 不能为空。")

    def test_history_search_tool_forces_search_action(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())
        self.db.save_turn(user_content="我要买牛奶", assistant_content="已记录买牛奶日程")

        observation = agent._execute_planner_tool(
            action_tool="history_search",
            action_input='{"action":"list","keyword":"牛奶","limit":5}',
        )

        self.assertTrue(observation.ok)
        self.assertIn("历史搜索(关键词: 牛奶", observation.result)

    def test_history_search_tool_supports_legacy_search_command(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())
        self.db.save_turn(user_content="安排体检", assistant_content="已记录体检日程")

        observation = agent._execute_planner_tool(
            action_tool="history_search",
            action_input="/history search 体检 --limit 5",
        )

        self.assertTrue(observation.ok)
        self.assertIn("历史搜索(关键词: 体检", observation.result)

    def test_thoughts_tool_supports_crud_actions(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())

        add_observation = agent._execute_planner_tool(
            action_tool="thoughts",
            action_input='{"action":"add","content":"记得买牛奶"}',
        )
        self.assertTrue(add_observation.ok)
        self.assertIn("已记录想法 #1", add_observation.result)

        list_observation = agent._execute_planner_tool(
            action_tool="thoughts",
            action_input='{"action":"list"}',
        )
        self.assertTrue(list_observation.ok)
        self.assertIn("想法列表(状态: 未完成|完成)", list_observation.result)

        get_observation = agent._execute_planner_tool(
            action_tool="thoughts",
            action_input='{"action":"get","id":1}',
        )
        self.assertTrue(get_observation.ok)
        self.assertIn("想法详情:", get_observation.result)

        update_observation = agent._execute_planner_tool(
            action_tool="thoughts",
            action_input='{"action":"update","id":1,"content":"记得买牛奶和鸡蛋","status":"完成"}',
        )
        self.assertTrue(update_observation.ok)
        self.assertIn("[状态:完成]", update_observation.result)

        delete_observation = agent._execute_planner_tool(
            action_tool="thoughts",
            action_input='{"action":"delete","id":1}',
        )
        self.assertTrue(delete_observation.ok)
        self.assertIn("想法 #1 已删除", delete_observation.result)

        deleted_list = agent._execute_planner_tool(
            action_tool="thoughts",
            action_input='{"action":"list","status":"删除"}',
        )
        self.assertTrue(deleted_list.ok)
        self.assertIn("想法列表(状态: 删除)", deleted_list.result)
        self.assertIn("记得买牛奶和鸡蛋", deleted_list.result)

    def test_thoughts_tool_logs_done_and_failed_events(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())

        with self.assertLogs("assistant_app.app", level="INFO") as captured_done:
            invalid_status = agent._execute_planner_tool(
                action_tool="thoughts",
                action_input='{"action":"list","status":"进行中"}',
            )
        self.assertFalse(invalid_status.ok)
        self.assertIn("status 必须为 未完成|完成|删除", invalid_status.result)
        merged_done = "\n".join(captured_done.output)
        self.assertIn("planner_tool_thoughts_start", merged_done)
        self.assertIn("planner_tool_thoughts_done", merged_done)

        with patch.object(agent.db, "add_thought", side_effect=RuntimeError("boom")):
            with self.assertLogs("assistant_app.app", level="INFO") as captured_failed:
                failed = agent._execute_planner_tool(
                    action_tool="thoughts",
                    action_input='{"action":"add","content":"x"}',
                )
        self.assertFalse(failed.ok)
        self.assertIn("thoughts 工具执行失败", failed.result)
        merged_failed = "\n".join(captured_failed.output)
        self.assertIn("planner_tool_thoughts_start", merged_failed)
        self.assertIn("planner_tool_thoughts_failed", merged_failed)

    def test_thoughts_tool_update_supports_runtime_typed_payload(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())
        thought_id = self.db.add_thought("记得买牛奶")

        with self.assertLogs("assistant_app.app", level="INFO") as captured:
            observation = agent._execute_planner_tool(
                action_tool="thoughts",
                action_input="not-json",
                action_payload=RuntimePlannerActionPayload(
                    tool_name="thoughts_update",
                    arguments=ThoughtsUpdateArgs(id=thought_id, content="记得买牛奶和鸡蛋", status="完成"),
                ),
            )

        self.assertTrue(observation.ok)
        self.assertIn("[状态:完成]", observation.result)
        item = self.db.get_thought(thought_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.content, "记得买牛奶和鸡蛋")
        self.assertEqual(item.status, "完成")
        merged = "\n".join(captured.output)
        self.assertIn("planner_tool_thoughts_start", merged)
        self.assertIn("planner_tool_thoughts_done", merged)

    def test_thoughts_cli_and_json_payload_share_runtime_payload(self) -> None:
        command_payload = parse_tool_command_payload("/thoughts update 1 记得买牛奶和鸡蛋 --status 完成")
        self.assertIsNotNone(command_payload)
        assert command_payload is not None

        compat_payload = coerce_thoughts_action_payload(
            {"action": "update", "id": 1, "content": "记得买牛奶和鸡蛋", "status": "完成"}
        )

        self.assertEqual(command_payload, compat_payload)

    def test_schedule_add_cli_and_json_payload_share_runtime_payload(self) -> None:
        command_payload = parse_tool_command_payload(
            "/schedule add 2026-03-01 09:30 项目同步 --tag Work --duration 45 "
            "--remind 2026-03-01 09:00 --interval 60 --times 3 --remind-start 2026-03-01 08:45"
        )
        self.assertIsNotNone(command_payload)
        assert command_payload is not None

        compat_payload = coerce_schedule_action_payload(
            {
                "action": "add",
                "event_time": "2026-03-01 09:30",
                "title": "项目同步",
                "tag": "Work",
                "duration_minutes": 45,
                "remind_at": "2026-03-01 09:00",
                "interval_minutes": 60,
                "times": 3,
                "remind_start_time": "2026-03-01 08:45",
            }
        )

        self.assertEqual(command_payload, compat_payload)

    def test_schedule_update_cli_and_json_payload_share_runtime_payload(self) -> None:
        command_payload = parse_tool_command_payload(
            "/schedule update 7 2026-03-01 10:00 项目复盘 --tag review --duration 30 --interval 60 --times -1"
        )
        self.assertIsNotNone(command_payload)
        assert command_payload is not None

        compat_payload = coerce_schedule_action_payload(
            {
                "action": "update",
                "id": 7,
                "event_time": "2026-03-01 10:00",
                "title": "项目复盘",
                "tag": "review",
                "duration_minutes": 30,
                "interval_minutes": 60,
                "times": -1,
            }
        )

        self.assertEqual(command_payload, compat_payload)

    def test_history_search_cli_and_json_payload_share_runtime_payload(self) -> None:
        command_payload = parse_tool_command_payload("/history search 周报 --limit 5")
        self.assertIsNotNone(command_payload)
        assert command_payload is not None

        compat_payload = coerce_history_action_payload(
            {"action": "search", "keyword": "周报", "limit": 5}
        )

        self.assertEqual(command_payload, compat_payload)

    def test_thoughts_tool_update_rejects_explicit_null_status(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())
        thought_id = self.db.add_thought("记得买牛奶")

        observation = agent._execute_planner_tool(
            action_tool="thoughts",
            action_input='{"action":"update","id":1,"content":"记得买牛奶和鸡蛋","status":null}',
        )

        self.assertFalse(observation.ok)
        self.assertEqual(observation.result, "thoughts.update status 必须为 未完成|完成|删除。")
        item = self.db.get_thought(thought_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.content, "记得买牛奶")

    def test_json_planner_tool_executor_prefers_typed_payload_over_legacy_command(self) -> None:
        typed_observation = PlannerObservation(tool="history", input_text="typed", ok=True, result="typed")
        route = JsonPlannerToolRoute(
            tool="history",
            invalid_json_result="invalid",
            legacy_command_prefix="/history",
            payload_executor=lambda payload, raw_input: PlannerObservation(
                tool="history",
                input_text=raw_input,
                ok=False,
                result="payload",
            ),
            typed_payload_executor=lambda payload, raw_input: typed_observation,
        )
        executor = build_json_planner_tool_executor(
            route=route,
            command_executor=lambda command: "legacy",
        )

        observation = executor(
            "/history search 牛奶",
            RuntimePlannerActionPayload(
                tool_name="history_search",
                arguments=HistorySearchArgs(keyword="牛奶"),
            ),
        )

        self.assertIs(observation, typed_observation)

    def test_schedule_tool_supports_runtime_typed_payload(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())

        observation = agent._execute_planner_tool(
            action_tool="schedule",
            action_input="not-json",
            action_payload=RuntimePlannerActionPayload(
                tool_name="schedule_add",
                arguments=ScheduleAddArgs(
                    event_time="2026-03-02 09:30",
                    title="项目同步",
                    duration_minutes=45,
                ),
            ),
        )

        self.assertTrue(observation.ok)
        self.assertIn("已添加日程 #1", observation.result)
        item = self.db.get_schedule(1)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.duration_minutes, 45)

    def test_schedule_tool_update_supports_runtime_typed_payload(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())
        schedule_id = self.db.add_schedule("项目同步", "2026-03-01 10:00", duration_minutes=30, tag="work")

        observation = agent._execute_planner_tool(
            action_tool="schedule",
            action_input="not-json",
            action_payload=RuntimePlannerActionPayload(
                tool_name="schedule_update",
                arguments=ScheduleUpdateArgs(
                    id=schedule_id,
                    event_time="2026-03-01 11:00",
                    title="项目复盘",
                    duration_minutes=45,
                    tag="review",
                ),
            ),
        )

        self.assertTrue(observation.ok)
        self.assertIn("[标签:review]", observation.result)
        self.assertIn("(45 分钟)", observation.result)
        item = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.title, "项目复盘")
        self.assertEqual(item.duration_minutes, 45)
        self.assertEqual(item.tag, "review")

    def test_schedule_tool_update_with_null_tag_clears_to_default(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())
        schedule_id = self.db.add_schedule("项目同步", "2026-03-01 10:00", tag="work")

        observation = agent._execute_schedule_system_action(
            payload={
                "action": "update",
                "id": schedule_id,
                "event_time": "2026-03-01 11:00",
                "title": "项目同步",
                "tag": None,
            },
            raw_input='{"action":"update","id":1,"event_time":"2026-03-01 11:00","title":"项目同步","tag":null}',
        )

        self.assertTrue(observation.ok)
        self.assertIn("[标签:default]", observation.result)
        updated = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.tag, "default")

    def test_schedule_tool_add_rejects_explicit_null_duration(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())

        observation = agent._execute_schedule_system_action(
            payload={
                "action": "add",
                "event_time": "2026-03-01 11:00",
                "title": "项目同步",
                "duration_minutes": None,
            },
            raw_input='{"action":"add","event_time":"2026-03-01 11:00","title":"项目同步","duration_minutes":null}',
        )

        self.assertFalse(observation.ok)
        self.assertEqual(observation.result, "schedule.add duration_minutes 需为 >=1 的整数。")

    def test_schedule_tool_update_rejects_explicit_null_times(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())
        schedule_id = self.db.add_schedule("项目同步", "2026-03-01 10:00")

        observation = agent._execute_schedule_system_action(
            payload={
                "action": "update",
                "id": schedule_id,
                "event_time": "2026-03-01 11:00",
                "title": "项目同步",
                "interval_minutes": 60,
                "times": None,
            },
            raw_input=(
                '{"action":"update","id":1,"event_time":"2026-03-01 11:00",'
                '"title":"项目同步","interval_minutes":60,"times":null}'
            ),
        )

        self.assertFalse(observation.ok)
        self.assertEqual(observation.result, "schedule.update times 需为 -1 或 >=2 的整数。")

    def test_schedule_tool_repeat_with_dict_payload_updates_rule_state(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=FakeLLMClient(), search_provider=FakeSearchProvider())
        schedule_id = self.db.add_schedule("项目同步", "2026-03-01 10:00", tag="work")
        self.db.set_schedule_recurrence(
            schedule_id,
            start_time="2026-03-01 10:00",
            repeat_interval_minutes=1440,
            repeat_times=-1,
        )

        observation = agent._execute_schedule_system_action(
            payload={
                "action": "repeat",
                "id": schedule_id,
                "enabled": False,
            },
            raw_input='{"action":"repeat","id":1,"enabled":false}',
        )

        self.assertTrue(observation.ok)
        self.assertIn("已停用日程", observation.result)
        updated = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertFalse(updated.repeat_enabled)

    def test_schedule_delete_missing_id_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                json.dumps({"intent": "schedule_delete"}, ensure_ascii=False, separators=(",", ":")),
            ]
        )
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("删掉这个日程")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertEqual(fake_llm.model_call_count, 4)

    def test_invalid_intent_json_returns_service_unavailable(self) -> None:
        fake_llm = FakeLLMClient(responses=["不是json", "还是不是json", "依然不是json"])
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("今天天气如何")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertIn("/schedule", response)
        self.assertEqual(fake_llm.model_call_count, 4)

    def test_non_json_model_text_is_treated_as_failure(self) -> None:
        fake_llm = FakeLLMClient(responses=["我先快速扫一遍日程并给你汇总清单。"])
        agent = AssistantAgent(db=self.db, llm_client=fake_llm)

        response = agent.handle_input("看一下全部日程")
        self.assertIn("计划执行服务暂时不可用", response)
        self.assertEqual(fake_llm.model_call_count, 4)

    def test_schedule_repeat_invalid_combo_retries_then_unavailable(self) -> None:
        fake_llm = FakeLLMClient(
            responses=[
                _planner_planned(["新增日程"]),
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
                _planner_planned(["新增日程"]),
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
                _planner_planned(["查看月视图"]),
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
        self.assertEqual(fake_llm.model_call_count, 4)

    def test_chat_without_llm(self) -> None:
        agent = AssistantAgent(db=self.db, llm_client=None)

        result = agent.handle_input("今天要做什么")
        self.assertIn("未配置 LLM", result)

    def test_strip_think_blocks(self) -> None:
        text = "<think>abc</think>最终答案"
        self.assertEqual(_strip_think_blocks(text), "最终答案")


if __name__ == "__main__":
    unittest.main()
