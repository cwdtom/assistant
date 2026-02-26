from __future__ import annotations

import json
from typing import Any

from assistant_app.planner_common import normalize_plan_items

THOUGHT_PROMPT = """
你是 CLI 助手的 thought 模块，需要基于当前计划项做一步决策。
你在 thought 阶段必须优先使用 tool calling。
工具与参数定义以 API 请求里的 tools schema 为准（不以 prompt 中的示例字段为准）。
可用工具名：todo、schedule、internet_search、history_search、ask_user、done。

规则：
- 每轮最多调用 1 个工具
- 如果信息不足，调用 ask_user
- 当前子任务完成时，调用 done（由 replan 决定外层继续或收口）
- 输入上下文里的 current_subtask 是当前唯一可执行子任务；不得基于未来步骤提前执行动作
- completed_subtasks / current_subtask_observations 仅用于参考已完成结果与当前子任务进度
- 历史对话消息（messages 中的 user/assistant 轮次）/ user_profile
  可用于补全默认信息与保持输出风格一致；不得覆盖用户当前明确指令
- 你会在 messages 中看到上一轮 assistant tool_calls 与 role=tool 的执行结果，请结合多轮上下文继续决策
- 禁止在 tool 参数里传命令字符串；必须传结构化字段
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
                        "description": "待办动作类型：add新增、list列表、get详情、update更新、delete删除、done完成、search搜索、view视图。",
                    },
                    "id": {"type": "integer", "description": "待办 ID，正整数；用于 get/update/delete/done。"},
                    "content": {"type": "string", "description": "待办内容文本；用于 add/update。"},
                    "tag": {"type": ["string", "null"], "description": "待办标签；null 表示不设置或清空。"},
                    "priority": {"type": "integer", "description": "优先级整数，>=0，数值越小优先级越高。"},
                    "due_at": {
                        "type": ["string", "null"],
                        "description": "截止时间，格式 YYYY-MM-DD HH:MM（本地时间）；null 表示不设置/清空。",
                    },
                    "remind_at": {
                        "type": ["string", "null"],
                        "description": "提醒时间，格式 YYYY-MM-DD HH:MM（本地时间）；null 表示不设置/清空。",
                    },
                    "view": {
                        "type": "string",
                        "enum": ["all", "today", "overdue", "upcoming", "inbox"],
                        "description": "待办视图，仅用于 view/list：all|today|overdue|upcoming|inbox。",
                    },
                    "keyword": {"type": "string", "description": "搜索关键词；用于 search。"},
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
                        "description": "日程动作类型：add新增、list列表、get详情、view视图、update更新、delete删除、repeat切换重复规则。",
                    },
                    "id": {"type": "integer", "description": "日程 ID，正整数；用于 get/update/delete/repeat。"},
                    "event_time": {
                        "type": "string",
                        "description": "日程开始时间，格式 YYYY-MM-DD HH:MM（本地时间）；用于 add/update。",
                    },
                    "title": {"type": "string", "description": "日程标题文本；用于 add/update。"},
                    "tag": {"type": ["string", "null"], "description": "日程标签；null 表示不设置或清空。"},
                    "duration_minutes": {"type": "integer", "description": "日程时长，单位分钟，>=1。"},
                    "remind_at": {
                        "type": ["string", "null"],
                        "description": "单次提醒时间，格式 YYYY-MM-DD HH:MM（本地时间）；null 表示不设置/清空。",
                    },
                    "interval_minutes": {"type": "integer", "description": "重复间隔，单位分钟，>=1。"},
                    "times": {"type": "integer", "description": "重复次数：-1 表示无限重复，或 >=2 的有限重复次数。"},
                    "remind_start_time": {
                        "type": ["string", "null"],
                        "description": "重复提醒起始时间，格式 YYYY-MM-DD HH:MM（本地时间）；null 表示不设置/清空。",
                    },
                    "view": {
                        "type": "string",
                        "enum": ["day", "week", "month"],
                        "description": "日历视图类型，仅用于 view：day|week|month。",
                    },
                    "anchor": {"type": ["string", "null"], "description": "视图锚点；day/week 用 YYYY-MM-DD，month 用 YYYY-MM。"},
                    "enabled": {"type": "boolean", "description": "重复规则开关，仅用于 repeat：true=开启，false=关闭。"},
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
            "description": "搜索互联网信息，返回结构化搜索结果摘要。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词文本；用于互联网检索。",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "history_search",
            "description": "检索历史会话中的用户输入与最终回答。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "历史检索关键词文本；用于匹配历史会话。",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "返回结果上限，正整数；不填时使用系统默认值。",
                    },
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
                    "question": {
                        "type": "string",
                        "description": "单个澄清问题文本；用于向用户补齐缺失信息。",
                    },
                    "current_step": {
                        "type": "string",
                        "description": "当前步骤说明文本（可选）；用于标注该问题对应的子任务步骤。",
                    },
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
                    "response": {
                        "type": "string",
                        "description": "当前子任务结论文本；用于交给 replan 判断是否继续。",
                    },
                    "current_step": {
                        "type": "string",
                        "description": "当前步骤说明文本（可选）；用于标注已完成的子任务步骤。",
                    },
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
