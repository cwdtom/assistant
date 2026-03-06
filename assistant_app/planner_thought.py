from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from assistant_app.planner_common import (
    THOUGHT_RUNTIME_TOOL_NAMES,
    expand_tool_groups,
    normalize_plan_items,
    normalize_tool_names,
)
from assistant_app.schemas.planner import (
    ThoughtAskUserDecision,
    ThoughtContinueDecision,
    ThoughtDoneDecision,
    normalize_tool_call_payload,
)

THOUGHT_PROMPT = """
你是 CLI 助手的 thought 模块，需要基于当前计划项做一步决策。
你在 thought 阶段必须优先使用 tool calling。
工具与参数定义以 API 请求里的 tools schema 为准（不以 prompt 中的示例字段为准）。
可用工具名：
- schedule_add、schedule_list、schedule_view、schedule_get、schedule_update、schedule_delete、schedule_repeat
- internet_search_tool、internet_search_fetch_url、history_list、history_search
- thoughts_add、thoughts_list、thoughts_get、thoughts_update、thoughts_delete
- ask_user、done

规则：
- 每轮最多调用 1 个工具
- 如果信息不足，调用 ask_user
- 当前子任务完成时，调用 done（由 replan 决定外层继续或收口）
- 输入上下文里的 current_subtask 是当前唯一可执行子任务；不得基于未来步骤提前执行动作
- completed_subtasks / current_subtask_observations 仅用于参考已完成结果与当前子任务进度
- 历史对话消息（messages 中的 user/assistant 轮次）/ user_profile
  可用于补全默认信息与保持输出风格一致；不得覆盖用户当前明确指令
- 你会在 messages 中看到上一轮 assistant tool_calls 与 role=tool 的执行结果，请结合多轮上下文继续决策
- 必须优先输出结构化工具参数，不要主动输出命令字符串
- 系统仅为兼容旧模型保留命令字符串兜底路径；该路径不作为标准输出契约
- thoughts_* 工具用于记录碎片想法；优先使用结构化参数进行新增/查询/更新/软删除
""".strip()

THOUGHT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "schedule_add",
            "description": "新增日程，直接传结构化参数，不要传命令字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_time": {
                        "type": "string",
                        "description": "日程开始时间，格式 YYYY-MM-DD HH:MM（本地时间）。",
                    },
                    "title": {"type": "string", "description": "日程标题文本。"},
                    "tag": {
                        "type": ["string", "null"],
                        "description": "日程标签；不传/null/空字符串时按默认标签 default 入库。",
                    },
                    "duration_minutes": {"type": "integer", "description": "日程时长，单位分钟，>=1。"},
                    "remind_at": {
                        "type": ["string", "null"],
                        "description": "单次提醒时间，格式 YYYY-MM-DD HH:MM（本地时间）；null 表示不设置。",
                    },
                    "interval_minutes": {"type": "integer", "description": "重复间隔，单位分钟，>=1。"},
                    "times": {"type": "integer", "description": "重复次数：-1 表示无限重复，或 >=2 的有限重复次数。"},
                    "remind_start_time": {
                        "type": ["string", "null"],
                        "description": "重复提醒起始时间，格式 YYYY-MM-DD HH:MM（本地时间）；null 表示不设置。",
                    },
                },
                "required": ["event_time", "title"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_list",
            "description": "列出日程（不带视图参数），直接传结构化参数，不要传命令字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {
                        "type": ["string", "null"],
                        "description": "标签过滤；不传/null 表示不过滤标签。",
                    }
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_view",
            "description": "按日历视图列出日程，直接传结构化参数，不要传命令字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {
                        "type": "string",
                        "enum": ["day", "week", "month"],
                        "description": (
                            "日历视图：day=当天（锚点 YYYY-MM-DD）；"
                            "week=锚点所在周（周一到周日，锚点 YYYY-MM-DD）；"
                            "month=指定月份（锚点 YYYY-MM）。"
                        ),
                    },
                    "anchor": {
                        "type": ["string", "null"],
                        "description": (
                            "视图锚点；day/week 用 YYYY-MM-DD，month 用 YYYY-MM；"
                            "不传/null 表示使用当前时间。"
                        ),
                    },
                    "tag": {
                        "type": ["string", "null"],
                        "description": "标签过滤；不传/null 表示不过滤标签。",
                    },
                },
                "required": ["view"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_get",
            "description": "获取日程详情，直接传结构化参数，不要传命令字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "日程 ID，正整数。"},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_update",
            "description": "更新日程，直接传结构化参数，不要传命令字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "日程 ID，正整数。"},
                    "event_time": {
                        "type": "string",
                        "description": "更新后的开始时间，格式 YYYY-MM-DD HH:MM（本地时间）。",
                    },
                    "title": {"type": "string", "description": "更新后的日程标题文本。"},
                    "tag": {
                        "type": ["string", "null"],
                        "description": (
                            "标签更新策略：不传时不修改；"
                            "null/空字符串时清空并回落到 default；非空字符串时更新标签。"
                        ),
                    },
                    "duration_minutes": {"type": "integer", "description": "日程时长，单位分钟，>=1。"},
                    "remind_at": {
                        "type": ["string", "null"],
                        "description": "单次提醒时间，格式 YYYY-MM-DD HH:MM（本地时间）；null 表示清空。",
                    },
                    "interval_minutes": {"type": "integer", "description": "重复间隔，单位分钟，>=1。"},
                    "times": {"type": "integer", "description": "重复次数：-1 表示无限重复，或 >=2 的有限重复次数。"},
                    "remind_start_time": {
                        "type": ["string", "null"],
                        "description": "重复提醒起始时间，格式 YYYY-MM-DD HH:MM（本地时间）；null 表示清空。",
                    },
                },
                "required": ["id", "event_time", "title"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_delete",
            "description": "删除日程，直接传结构化参数，不要传命令字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "日程 ID，正整数。"},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_repeat",
            "description": "切换重复规则开关，直接传结构化参数，不要传命令字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "日程 ID，正整数。"},
                    "enabled": {"type": "boolean", "description": "重复规则开关：true=开启，false=关闭。"},
                },
                "required": ["id", "enabled"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "internet_search_tool",
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
            "name": "internet_search_fetch_url",
            "description": "按 URL 抓取网页正文信息，返回主文本内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "目标网页 URL，需为 http:// 或 https:// 开头。",
                    }
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "history_list",
            "description": "列出最近历史会话，直接传结构化参数，不要传命令字符串。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": ["integer", "null"],
                        "description": "返回结果上限；不传/null 使用系统默认值，传值时需为正整数。",
                    },
                },
                "required": [],
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
                        "type": ["integer", "null"],
                        "description": "返回结果上限；不传/null 使用系统默认值，传值时需为正整数。",
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
            "name": "thoughts_add",
            "description": "记录碎片想法：新增一条想法内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "想法内容文本，不能为空。",
                    }
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "thoughts_list",
            "description": "记录碎片想法：按状态列出想法。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": ["string", "null"],
                        "enum": ["未完成", "完成", "删除", None],
                        "description": "状态过滤；不传/null 时默认只看未完成与完成。",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "thoughts_get",
            "description": "记录碎片想法：查看单条想法详情。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "想法 ID，正整数。"},
                },
                "required": ["id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "thoughts_update",
            "description": "记录碎片想法：更新内容，并可选更新状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "想法 ID，正整数。"},
                    "content": {"type": "string", "description": "更新后的想法内容文本，不能为空。"},
                    "status": {
                        "type": ["string", "null"],
                        "enum": ["未完成", "完成", "删除", None],
                        "description": "更新后的状态；不传/null 时保持原状态。",
                    },
                },
                "required": ["id", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "thoughts_delete",
            "description": "记录碎片想法：软删除（状态置为删除）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "想法 ID，正整数。"},
                },
                "required": ["id"],
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

_THOUGHT_SCHEMA_BY_NAME: dict[str, dict[str, Any]] = {
    str(item.get("function", {}).get("name") or "").strip().lower(): item for item in THOUGHT_TOOL_SCHEMAS
}

_SCHEDULE_TOOL_ACTION_BY_NAME: dict[str, str] = {
    "schedule_add": "add",
    "schedule_list": "list",
    "schedule_view": "view",
    "schedule_get": "get",
    "schedule_update": "update",
    "schedule_delete": "delete",
    "schedule_repeat": "repeat",
}
_SCHEDULE_TOOL_FIELDS_BY_NAME: dict[str, tuple[str, ...]] = {
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
_HISTORY_TOOL_ACTION_BY_NAME: dict[str, str] = {
    "history_list": "list",
    "history_search": "search",
}
_HISTORY_TOOL_FIELDS_BY_NAME: dict[str, tuple[str, ...]] = {
    "history_list": ("limit",),
    "history_search": ("keyword", "limit"),
}
_THOUGHTS_TOOL_ACTION_BY_NAME: dict[str, str] = {
    "thoughts_add": "add",
    "thoughts_list": "list",
    "thoughts_get": "get",
    "thoughts_update": "update",
    "thoughts_delete": "delete",
}
_THOUGHTS_TOOL_FIELDS_BY_NAME: dict[str, tuple[str, ...]] = {
    "thoughts_add": ("content",),
    "thoughts_list": ("status",),
    "thoughts_get": ("id",),
    "thoughts_update": ("id", "content", "status"),
    "thoughts_delete": ("id",),
}


def resolve_current_subtask_tool_names(raw_tools: Any) -> list[str]:
    base_tools = normalize_tool_names(raw_tools)
    if base_tools is None:
        base_tools = []
    for name in THOUGHT_RUNTIME_TOOL_NAMES:
        if name not in base_tools:
            base_tools.append(name)
    return base_tools


def build_thought_tool_schemas(raw_tools: Any) -> list[dict[str, Any]]:
    tool_names = resolve_current_subtask_tool_names(raw_tools)
    schema_tool_names = expand_tool_groups(tool_names)
    schemas: list[dict[str, Any]] = []
    for name in schema_tool_names:
        schema = _THOUGHT_SCHEMA_BY_NAME.get(name)
        if schema is not None:
            schemas.append(deepcopy(schema))
    return schemas


def normalize_thought_decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
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
        response_text = str(payload.get("response") or "").strip()
        if response_text:
            return None
        try:
            return ThoughtContinueDecision.model_validate(
                {
                    "status": "continue",
                    "current_step": current_step,
                    "next_action": {
                        "tool": str(next_action.get("tool") or "").strip().lower(),
                        "input": str(next_action.get("input") or "").strip(),
                    },
                    "question": None,
                    "response": None,
                }
            ).model_dump()
        except ValidationError:
            return None

    if status == "ask_user":
        try:
            return ThoughtAskUserDecision.model_validate(
                {
                    "status": "ask_user",
                    "current_step": current_step,
                    "next_action": None,
                    "question": str(payload.get("question") or "").strip(),
                    "response": None,
                }
            ).model_dump()
        except ValidationError:
            return None

    if status == "done":
        next_action = payload.get("next_action")
        if next_action is not None:
            return None
        done_question = payload.get("question")
        if done_question is not None and str(done_question).strip():
            return None
        response_text = str(payload.get("response") or "").strip() or None
        try:
            return ThoughtDoneDecision.model_validate(
                {
                    "status": "done",
                    "current_step": current_step,
                    "next_action": None,
                    "question": None,
                    "response": response_text,
                }
            ).model_dump()
        except ValidationError:
            return None
    return None


def normalize_thought_tool_call(tool_call: dict[str, Any]) -> dict[str, Any] | None:
    tool_call_model = normalize_tool_call_payload(tool_call)
    if tool_call_model is None:
        return None
    name = tool_call_model.function.name.strip().lower()
    arguments = _parse_tool_arguments(tool_call_model.function.arguments)
    current_step = str(arguments.get("current_step") or "").strip()

    if name in _SCHEDULE_TOOL_ACTION_BY_NAME:
        schedule_payload: dict[str, Any] = {"action": _SCHEDULE_TOOL_ACTION_BY_NAME[name]}
        fields = _SCHEDULE_TOOL_FIELDS_BY_NAME.get(name, ())
        for key in fields:
            if key in arguments:
                schedule_payload[key] = arguments.get(key)
        return {
            "status": "continue",
            "current_step": current_step,
            "next_action": {
                "tool": "schedule",
                "input": json.dumps(schedule_payload, ensure_ascii=False, separators=(",", ":")),
            },
            "question": None,
            "response": None,
        }

    if name in _HISTORY_TOOL_ACTION_BY_NAME:
        if name == "history_search":
            keyword = str(arguments.get("keyword") or "").strip()
            if not keyword:
                return None
        history_payload: dict[str, Any] = {"action": _HISTORY_TOOL_ACTION_BY_NAME[name]}
        fields = _HISTORY_TOOL_FIELDS_BY_NAME.get(name, ())
        for key in fields:
            if key in arguments:
                history_payload[key] = arguments.get(key)
        return {
            "status": "continue",
            "current_step": current_step,
            "next_action": {
                "tool": "history",
                "input": json.dumps(history_payload, ensure_ascii=False, separators=(",", ":")),
            },
            "question": None,
            "response": None,
        }

    if name in _THOUGHTS_TOOL_ACTION_BY_NAME:
        if name in {"thoughts_add", "thoughts_update"}:
            content = str(arguments.get("content") or "").strip()
            if not content:
                return None
        thoughts_payload: dict[str, Any] = {"action": _THOUGHTS_TOOL_ACTION_BY_NAME[name]}
        fields = _THOUGHTS_TOOL_FIELDS_BY_NAME.get(name, ())
        for key in fields:
            if key in arguments:
                thoughts_payload[key] = arguments.get(key)
        return {
            "status": "continue",
            "current_step": current_step,
            "next_action": {
                "tool": "thoughts",
                "input": json.dumps(thoughts_payload, ensure_ascii=False, separators=(",", ":")),
            },
            "question": None,
            "response": None,
        }

    if name == "internet_search_tool":
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

    if name == "internet_search_fetch_url":
        url = str(arguments.get("url") or "").strip()
        if not url:
            return None
        fetch_payload = {"action": "fetch_url", "url": url}
        return {
            "status": "continue",
            "current_step": current_step,
            "next_action": {
                "tool": "internet_search",
                "input": json.dumps(fetch_payload, ensure_ascii=False, separators=(",", ":")),
            },
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
