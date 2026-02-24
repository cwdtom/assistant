from __future__ import annotations

from typing import Any

from assistant_app.planner_common import normalize_plan_items

PLAN_ONCE_PROMPT = """
你是 CLI 助手的 plan 模块，只负责在任务开始时生成执行计划。
你每次必须只输出一个 JSON 对象，禁止输出额外文本。

可用执行能力（用于规划步骤，不要求你输出工具命令）：
- todo：待办管理（新增、查询、更新、完成、删除、视图筛选）
- schedule：日程管理（新增、查询、更新、删除、日历视图、重复规则）
- internet_search：互联网检索网页信息并返回摘要
- ask_user：当信息不足时向用户发起澄清（由 thought 阶段触发）

输出 JSON 格式：
{
  "status": "planned",
  "plan": ["步骤1", "步骤2"]
}

规则：
- 只输出 planned，不要输出 done
- plan 至少包含 1 项，且应按执行顺序排列
- 不要输出工具动作，只给步骤描述
""".strip()

REPLAN_PROMPT = """
你是 CLI 助手的 replan 模块，需要在一个子任务的 thought->act->observe 循环完成后更新计划进度。
你每次必须只输出一个 JSON 对象，禁止输出额外文本。

可用执行能力（用于判断后续是否可继续推进）：
- todo：待办管理（新增、查询、更新、完成、删除、视图筛选）
- schedule：日程管理（新增、查询、更新、删除、日历视图、重复规则）
- internet_search：互联网检索网页信息并返回摘要
- ask_user：当信息不足时向用户发起澄清（由 thought 阶段触发）

输出 JSON 格式：
{
  "status": "replanned|done",
  "plan": [
    {"task": "步骤1", "completed": true},
    {"task": "步骤2", "completed": false}
  ],
  "response": "string|null"
}

规则：
- status=replanned: 必须输出计划数组（至少 1 项）
- status=replanned: plan 每项都必须包含 task(任务文本) 和 completed(是否已完成，布尔值)
- status=replanned: 至少要有 1 项 completed=false，表示仍有后续可执行任务
- 若基于当前 latest_plan/completed_subtasks/clarification_history 已能直接回答 goal，
  必须输出 status=done，并在 response 给出问题答案；不要继续扩写计划
- status=done: 必须输出最终结论 response，不要再给后续计划
- 新计划要融合 completed_subtasks 中的已完成子任务结果与用户澄清信息（如有）
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
