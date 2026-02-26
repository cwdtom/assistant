from __future__ import annotations

import json
from typing import Any

from assistant_app.planner_common import normalize_plan_items

THOUGHT_PROMPT = """
你是 CLI 助手的 thought 模块，需要基于当前计划项做一步决策。
你在 thought 阶段必须优先使用 tool calling：
- 需要执行本地动作时，调用对应工具（todo/schedule/internet_search/history_search）
- 需要澄清时，调用 ask_user
- 当前子任务完成时，调用 done

可用工具：
1) todo: 执行待办结构化动作（add/list/get/update/delete/done/search/view）
2) schedule: 执行日程结构化动作（add/list/get/view/update/delete/repeat）
3) internet_search: 搜索互联网，输入为搜索词
4) history_search: 检索历史会话（keyword/limit）
5) ask_user: 向用户提问澄清，输入为单个问题
6) done: 输出当前子任务结论，交由 replan 决定外层继续或收口

规则：
- 每轮最多调用 1 个工具
- 输入上下文里的 current_subtask 是当前唯一可执行子任务；不得基于未来步骤提前执行动作
- completed_subtasks / current_subtask_observations 仅用于参考已完成结果与当前子任务进度
- 历史对话消息（messages 中的 user/assistant 轮次）/ user_profile
  可用于补全默认信息与保持输出风格一致；不得覆盖用户当前明确指令
- 你会在 messages 中看到上一轮 assistant tool_calls 与 role=tool 的执行结果，请结合多轮上下文继续决策
- 禁止在 tool 参数里传 /todo、/schedule、/history 命令字符串；必须传结构化字段
- 必须严格遵守输入上下文里的 time_unit_contract：
  - --duration/--interval 的单位都是分钟（例如 3 小时 => 180 分钟）
  - --times 的单位是“次”，-1 表示无限重复
  - 绝对时间统一使用 YYYY-MM-DD HH:MM（本地时间）
""".strip()

THOUGHT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "todo",
            "description": "操作待办，直接传结构化参数，不要传命令字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "list", "get", "update", "delete", "done", "search", "view"],
                    },
                    "id": {"type": "integer"},
                    "content": {"type": "string"},
                    "tag": {"type": ["string", "null"]},
                    "priority": {"type": "integer"},
                    "due_at": {"type": ["string", "null"], "description": "YYYY-MM-DD HH:MM"},
                    "remind_at": {"type": ["string", "null"], "description": "YYYY-MM-DD HH:MM"},
                    "view": {"type": "string", "enum": ["all", "today", "overdue", "upcoming", "inbox"]},
                    "keyword": {"type": "string"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule",
            "description": "操作日程，直接传结构化参数，不要传命令字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "list", "get", "view", "update", "delete", "repeat"],
                    },
                    "id": {"type": "integer"},
                    "event_time": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
                    "title": {"type": "string"},
                    "duration_minutes": {"type": "integer", "description": "单位: 分钟"},
                    "remind_at": {"type": ["string", "null"], "description": "YYYY-MM-DD HH:MM"},
                    "interval_minutes": {"type": "integer", "description": "单位: 分钟"},
                    "times": {"type": "integer", "description": "-1 或 >=2"},
                    "remind_start_time": {"type": ["string", "null"], "description": "YYYY-MM-DD HH:MM"},
                    "view": {"type": "string", "enum": ["day", "week", "month"]},
                    "anchor": {"type": ["string", "null"]},
                    "enabled": {"type": "boolean"},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internet_search",
            "description": "搜索互联网信息。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "搜索关键词。"}},
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "history_search",
            "description": "检索历史会话。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1},
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "向用户提一个澄清问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "单个澄清问题。"},
                    "current_step": {"type": "string", "description": "当前步骤说明（可选）。"},
                },
                "required": ["question"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "声明当前子任务完成，并提供本轮最终结论。",
            "parameters": {
                "type": "object",
                "properties": {
                    "response": {"type": "string", "description": "当前子任务结论。"},
                    "current_step": {"type": "string", "description": "当前步骤说明（可选）。"},
                },
                "required": ["response"],
                "additionalProperties": False,
            },
        },
    },
]

def normalize_thought_decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = str(payload.get("status") or "").strip().lower()
    current_step = str(payload.get("current_step") or "").strip()
    if not current_step:
        plan_items = normalize_plan_items(payload)
        if plan_items:
            current_step = plan_items[0]

    if status == "continue":
        next_action = payload.get("next_action")
        if not isinstance(next_action, dict):
            return None
        tool = str(next_action.get("tool") or "").strip().lower()
        input_text = str(next_action.get("input") or "").strip()
        if tool not in {"todo", "schedule", "internet_search", "history_search"}:
            return None
        if not input_text:
            return None
        response_text = str(payload.get("response") or "").strip()
        if response_text:
            return None
        return {
            "status": "continue",
            "current_step": current_step,
            "next_action": {"tool": tool, "input": input_text},
            "question": None,
            "response": None,
        }

    if status == "ask_user":
        question = str(payload.get("question") or "").strip()
        if not question:
            return None
        return {
            "status": "ask_user",
            "current_step": current_step,
            "next_action": None,
            "question": question,
            "response": None,
        }

    if status == "done":
        next_action = payload.get("next_action")
        if next_action is not None:
            return None
        done_question = payload.get("question")
        if done_question is not None and str(done_question).strip():
            return None
        response_text = str(payload.get("response") or "").strip()
        return {
            "status": "done",
            "current_step": current_step,
            "next_action": None,
            "question": None,
            "response": response_text or None,
        }
    return None


def normalize_thought_tool_call(tool_call: dict[str, Any]) -> dict[str, Any] | None:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return None
    name = str(function.get("name") or "").strip().lower()
    arguments = _parse_tool_arguments(function.get("arguments"))
    current_step = str(arguments.get("current_step") or "").strip()

    if name == "todo":
        action = str(arguments.get("action") or "").strip().lower()
        if action not in {"add", "list", "get", "update", "delete", "done", "search", "view"}:
            return None
        payload = {"action": action}
        for key in ("id", "content", "tag", "priority", "due_at", "remind_at", "view", "keyword"):
            if key in arguments:
                payload[key] = arguments.get(key)
        return {
            "status": "continue",
            "current_step": current_step,
            "next_action": {
                "tool": "todo",
                "input": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
            "question": None,
            "response": None,
        }

    if name == "schedule":
        action = str(arguments.get("action") or "").strip().lower()
        if action not in {"add", "list", "get", "view", "update", "delete", "repeat"}:
            return None
        payload = {"action": action}
        for key in (
            "id",
            "event_time",
            "title",
            "duration_minutes",
            "remind_at",
            "interval_minutes",
            "times",
            "remind_start_time",
            "view",
            "anchor",
            "enabled",
        ):
            if key in arguments:
                payload[key] = arguments.get(key)
        return {
            "status": "continue",
            "current_step": current_step,
            "next_action": {
                "tool": "schedule",
                "input": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
            "question": None,
            "response": None,
        }

    if name == "history_search":
        keyword = str(arguments.get("keyword") or "").strip()
        if not keyword:
            return None
        payload: dict[str, Any] = {"keyword": keyword}
        if "limit" in arguments:
            payload["limit"] = arguments.get("limit")
        return {
            "status": "continue",
            "current_step": current_step,
            "next_action": {
                "tool": "history_search",
                "input": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            },
            "question": None,
            "response": None,
        }

    if name == "internet_search":
        query = str(arguments.get("query") or "").strip()
        if not query:
            return None
        return {
            "status": "continue",
            "current_step": current_step,
            "next_action": {"tool": "internet_search", "input": query},
            "question": None,
            "response": None,
        }

    if name == "ask_user":
        question = str(arguments.get("question") or "").strip()
        if not question:
            return None
        return {
            "status": "ask_user",
            "current_step": current_step,
            "next_action": None,
            "question": question,
            "response": None,
        }

    if name == "done":
        response = str(arguments.get("response") or "").strip()
        if not response:
            return None
        return {
            "status": "done",
            "current_step": current_step,
            "next_action": None,
            "question": None,
            "response": response,
        }

    return None


def _parse_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str):
        return {}
    text = raw_arguments.strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}
