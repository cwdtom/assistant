from __future__ import annotations

from typing import Any

from assistant_app.planner_common import normalize_plan_items

PLANNER_CAPABILITIES_TEXT = """
可用执行能力（用于规划步骤，不要求你输出工具命令）：
- todo：待办管理（新增、查询、更新、完成、删除、视图筛选）
- schedule：日程管理（新增、查询、更新、删除、日历视图、重复规则）
- internet_search：互联网检索网页信息并返回摘要
- history_search：检索历史会话（用户输入与最终回答）
- ask_user：当信息不足时向用户发起澄清（由 thought 阶段触发）
""".strip()

PLAN_INTENT_EXPANSION_RULE = (
    "先将用户口语化表达扩展成可执行且信息完整的目标再写计划步骤"
    "（如“看一下/看看/查一下”通常表示“查询并列出来给用户查看”；"
    "若关键信息缺失，优先结合 recent_chat_turns 与 user_profile 补全默认信息。"
    "例如“看一下明天的天气”可扩展为“查询用户默认城市的明天天气，并输出天气结果与衣着建议”）"
)
PLANNER_HISTORY_RULE = (
    "输入上下文会提供 recent_chat_turns（近 24 小时，最多 50 轮）"
    "，可用于补全上下文与引用历史约束。"
)
PLANNER_USER_PROFILE_RULE = (
    "输入上下文可能提供 user_profile（用户画像）。若存在，只能用于理解用户偏好和背景；"
    "不得覆盖用户当前明确指令，也不得臆造画像中不存在的信息。"
)

PLAN_ONCE_PROMPT = f"""
你是 CLI 助手的 plan 模块，只负责在任务开始时生成执行计划。
你每次必须只输出一个 JSON 对象，禁止输出额外文本。

{PLANNER_CAPABILITIES_TEXT}

输出 JSON 格式：
{{
  "status": "planned",
  "plan": ["步骤1", "步骤2"]
}}

规则：
- 只输出 planned，不要输出 done
- plan 至少包含 1 项，且应按执行顺序排列
- {PLAN_INTENT_EXPANSION_RULE}
- {PLANNER_HISTORY_RULE}
- {PLANNER_USER_PROFILE_RULE}
- 不要输出工具动作，只给步骤描述
""".strip()

REPLAN_PROMPT = f"""
你是 CLI 助手的 replan 模块，需要在一个子任务的 thought->act->observe 循环完成后更新计划进度。
你每次必须只输出一个 JSON 对象，禁止输出额外文本。

{PLANNER_CAPABILITIES_TEXT}

输出 JSON 格式：
{{
  "status": "replanned|done",
  "plan": [
    {{"task": "步骤1", "completed": true}},
    {{"task": "步骤2", "completed": false}}
  ],
  "response": "string|null"
}}

规则：
- status=replanned: 必须输出计划数组（至少 1 项）
- status=replanned: plan 每项都必须包含 task(任务文本) 和 completed(是否已完成，布尔值)
- status=replanned: 至少要有 1 项 completed=false，表示仍有后续可执行任务
- 若基于当前 latest_plan/completed_subtasks/clarification_history 已能直接回答 goal，
  必须输出 status=done，并在 response 给出问题答案；不要继续扩写计划
- status=done: 必须输出最终结论 response，不要再给后续计划
- 新计划要融合 completed_subtasks 中的已完成子任务结果与用户澄清信息（如有）
- {PLANNER_HISTORY_RULE}
- {PLANNER_USER_PROFILE_RULE}
- 可以输出“剩余步骤计划”或“重排后的全量计划”，但必须可继续执行
- 若信息仍不足，可保留待澄清步骤，但不要直接提问
""".strip()


def normalize_plan_decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = str(payload.get("status") or "").strip().lower()
    plan_items = normalize_plan_items(payload)
    if status == "planned":
        if not plan_items:
            return None
        return {"status": "planned", "plan": plan_items}
    return None


def normalize_replan_decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = str(payload.get("status") or "").strip().lower()
    if status == "replanned":
        raw_plan = payload.get("plan")
        if not isinstance(raw_plan, list):
            return None
        plan_items: list[dict[str, Any]] = []
        has_pending = False
        for item in raw_plan:
            if not isinstance(item, dict):
                return None
            task = str(item.get("task") or "").strip()
            completed = item.get("completed")
            if not task or not isinstance(completed, bool):
                return None
            if not completed:
                has_pending = True
            plan_items.append({"task": task, "completed": completed})
        if not plan_items or not has_pending:
            return None
        return {"status": "replanned", "plan": plan_items}
    if status == "done":
        response = str(payload.get("response") or "").strip()
        if not response:
            return None
        return {"status": "done", "response": response}
    return None
