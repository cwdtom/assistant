from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from assistant_app.planner_common import (
    THOUGHT_RUNTIME_TOOL_NAMES,
    expand_tool_groups,
    normalize_tool_names,
)
from assistant_app.runtime_actions import runtime_action_tool_for_payload
from assistant_app.schemas.planner import (
    ThoughtAskUserDecision,
    ThoughtContinueDecision,
    ThoughtDecision,
    ThoughtDoneDecision,
    normalize_tool_call_payload,
    parse_thought_decision,
)
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import (
    THOUGHT_TOOL_ARGS_MODELS,
    build_function_tool_schema,
    parse_json_object,
    validate_thought_tool_arguments,
)

THOUGHT_PROMPT = """
你是 CLI 助手的 thought 模块，需要基于当前计划项做一步决策。
你在 thought 阶段必须优先使用 tool calling。
工具与参数定义以 API 请求里的 tools schema 为准（不以 prompt 中的示例字段为准）。
可用工具名：
- schedule_add、schedule_list、schedule_view、schedule_get、schedule_update、schedule_delete、schedule_repeat
- internet_search_tool、internet_search_fetch_url、history_list、history_search
- thoughts_add、thoughts_list、thoughts_get、thoughts_update、thoughts_delete、system_date
- done
- ask_user 仅在本轮 tools schema 明确提供时可用

规则：
- 每轮最多调用 1 个工具
- 如果信息不足且本轮 tools schema 提供了 ask_user，调用 ask_user
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

_THOUGHT_TOOL_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("schedule_add", "新增日程，直接传结构化参数，不要传命令字符串。", ("current_step",)),
    ("schedule_list", "列出日程（不带视图参数），直接传结构化参数，不要传命令字符串。", ("current_step",)),
    ("schedule_view", "按日历视图列出日程，直接传结构化参数，不要传命令字符串。", ("current_step",)),
    ("schedule_get", "获取日程详情，直接传结构化参数，不要传命令字符串。", ("current_step",)),
    ("schedule_update", "更新日程，直接传结构化参数，不要传命令字符串。", ("current_step",)),
    ("schedule_delete", "删除日程，直接传结构化参数，不要传命令字符串。", ("current_step",)),
    ("schedule_repeat", "切换重复规则开关，直接传结构化参数，不要传命令字符串。", ("current_step",)),
    ("internet_search_tool", "搜索互联网信息，返回结构化搜索结果摘要。", ("current_step",)),
    ("internet_search_fetch_url", "按 URL 抓取网页正文信息，返回主文本内容。", ("current_step",)),
    ("history_list", "列出最近历史会话，直接传结构化参数，不要传命令字符串。", ("current_step",)),
    ("history_search", "检索历史会话中的用户输入与最终回答。", ("current_step",)),
    ("thoughts_add", "记录碎片想法：新增一条想法内容。", ("current_step",)),
    ("thoughts_list", "记录碎片想法：按状态列出想法。", ("current_step",)),
    ("thoughts_get", "记录碎片想法：查看单条想法详情。", ("current_step",)),
    ("thoughts_update", "记录碎片想法：更新内容，并可选更新状态。", ("current_step",)),
    ("thoughts_delete", "记录碎片想法：软删除（状态置为删除）。", ("current_step",)),
    ("system_date", "读取当前本地时间，返回 YYYY-MM-DD HH:MM:SS。", ("current_step",)),
    ("ask_user", "向用户提一个澄清问题。", ()),
    ("done", "声明当前子任务完成，并提供本轮最终结论。", ()),
)

THOUGHT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    build_function_tool_schema(
        name=name,
        description=description,
        arguments_model=THOUGHT_TOOL_ARGS_MODELS[name],
        exclude_fields=exclude_fields,
    )
    for name, description, exclude_fields in _THOUGHT_TOOL_SPECS
]

_THOUGHT_SCHEMA_BY_NAME: dict[str, dict[str, Any]] = {
    str(item.get("function", {}).get("name") or "").strip().lower(): item for item in THOUGHT_TOOL_SCHEMAS
}


def resolve_current_subtask_tool_names(raw_tools: Any, *, allow_ask_user: bool = True) -> list[str]:
    base_tools = normalize_tool_names(raw_tools)
    if base_tools is None:
        base_tools = []
    runtime_tool_names = [name for name in THOUGHT_RUNTIME_TOOL_NAMES if allow_ask_user or name != "ask_user"]
    for name in runtime_tool_names:
        if name not in base_tools:
            base_tools.append(name)
    return base_tools


def build_thought_tool_schemas(raw_tools: Any, *, allow_ask_user: bool = True) -> list[dict[str, Any]]:
    tool_names = resolve_current_subtask_tool_names(raw_tools, allow_ask_user=allow_ask_user)
    schema_tool_names = expand_tool_groups(tool_names)
    schemas: list[dict[str, Any]] = []
    for name in schema_tool_names:
        schema = _THOUGHT_SCHEMA_BY_NAME.get(name)
        if schema is not None:
            schemas.append(deepcopy(schema))
    return schemas


def normalize_thought_decision(payload: dict[str, Any]) -> ThoughtDecision | None:
    return parse_thought_decision(payload)


def normalize_thought_tool_call(tool_call: dict[str, Any]) -> ThoughtDecision | None:
    tool_call_model = normalize_tool_call_payload(tool_call)
    if tool_call_model is None:
        return None
    name = tool_call_model.function.name.strip().lower()
    parsed_arguments = parse_json_object(tool_call_model.function.arguments)
    if parsed_arguments is None:
        return None
    validated_arguments = validate_thought_tool_arguments(name, parsed_arguments)
    if validated_arguments is None:
        return None
    current_step = validated_arguments.current_step
    runtime_payload = RuntimePlannerActionPayload(tool_name=name, arguments=validated_arguments)
    action_tool = runtime_action_tool_for_payload(runtime_payload)
    if action_tool is not None:
        return _build_continue_decision(
            current_step=current_step,
            action_tool=action_tool,
            runtime_payload=runtime_payload,
        )

    if name == "ask_user":
        question = str(validated_arguments.model_dump().get("question") or "").strip()
        if not question:
            return None
        try:
            return ThoughtAskUserDecision.model_validate(
                {
                    "status": "ask_user",
                    "current_step": current_step,
                    "next_action": None,
                    "question": question,
                    "response": None,
                }
            )
        except ValidationError:
            return None

    if name == "done":
        response = str(validated_arguments.model_dump().get("response") or "").strip()
        if not response:
            return None
        try:
            return ThoughtDoneDecision.model_validate(
                {
                    "status": "done",
                    "current_step": current_step,
                    "next_action": None,
                    "question": None,
                    "response": response,
                }
            )
        except ValidationError:
            return None

    return None


def _build_continue_decision(
    *,
    current_step: str,
    action_tool: str,
    runtime_payload: RuntimePlannerActionPayload,
) -> ThoughtDecision | None:
    try:
        return ThoughtContinueDecision.model_validate(
            {
                "status": "continue",
                "current_step": current_step,
                "next_action": {
                    "tool": action_tool,
                    "payload": runtime_payload,
                },
                "question": None,
                "response": None,
            }
        )
    except ValidationError:
        return None
