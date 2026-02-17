from __future__ import annotations

from typing import Any

from assistant_app.planner_common import normalize_plan_items

THOUGHT_PROMPT = """
你是 CLI 助手的 thought 模块，需要基于当前计划项做一步决策。
你每次必须只输出一个 JSON 对象，禁止输出额外文本。

可用工具：
1) todo: 执行 /todo 或 /view 命令
2) schedule: 执行 /schedule 命令
3) internet_search: 搜索互联网，输入为搜索词
4) ask_user: 向用户提问澄清，输入为单个问题

输出 JSON 格式：
{
  "status": "continue|ask_user|done",
  "current_step": "string",
  "next_action": {
    "tool": "todo|schedule|internet_search",
    "input": "string"
  } | null,
  "question": "string|null",
  "response": "string|null"
}

规则：
- status=continue: next_action 必须存在，question/response 为空
- status=ask_user: question 必填，next_action/response 为空
- status=done: 表示“当前子任务已完成”，将退出内层循环并交由 replan 决定外层继续或收口
- 输入上下文里的 current_subtask 是当前唯一可执行子任务；不得基于未来步骤提前执行动作
- completed_subtasks / current_subtask_observations 仅用于参考已完成结果与当前子任务进度
- todo/schedule 的 next_action.input 必须是可直接执行的合法命令
- 必须严格遵守输入上下文里的 time_unit_contract：
  - --duration/--interval 的单位都是分钟（例如 3 小时 => 180 分钟）
  - --times 的单位是“次”，-1 表示无限重复
  - 绝对时间统一使用 YYYY-MM-DD HH:MM（本地时间）
""".strip()

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
        if tool not in {"todo", "schedule", "internet_search"}:
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
        question = payload.get("question")
        if question is not None and str(question).strip():
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
