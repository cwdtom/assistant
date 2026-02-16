from __future__ import annotations

from typing import Any

from assistant_app.planner_common import normalize_plan_items

PLAN_ONCE_PROMPT = """
你是 CLI 助手的 plan 模块，只负责在任务开始时生成执行计划。
你每次必须只输出一个 JSON 对象，禁止输出额外文本。

输出 JSON 格式：
{
  "status": "planned",
  "plan": ["步骤1", "步骤2"]
}

规则：
- 只输出 planned，不要输出 done
- plan 至少包含 1 项，且应按执行顺序排列
- 不要输出工具动作，只给步骤描述
- 对涉及时间/时长/重复间隔的步骤描述，需与 time_unit_contract 保持一致（尤其分钟/次数单位）
""".strip()

REPLAN_PROMPT = """
你是 CLI 助手的 replan 模块，需要在一个子任务的 thought->act->observe 循环完成后更新计划进度。
你每次必须只输出一个 JSON 对象，禁止输出额外文本。

输出 JSON 格式：
{
  "status": "replanned|done",
  "plan": ["更新后的步骤1", "更新后的步骤2"],
  "response": "string|null"
}

规则：
- status=replanned: 必须输出后续计划（至少 1 项）
- status=done: 必须输出最终结论 response，不要再给后续计划
- 新计划要融合该子任务的最新 observation 和用户澄清信息（如有）
- 可以输出“剩余步骤计划”或“重排后的全量计划”，但必须可继续执行
- 若信息仍不足，可保留待澄清步骤，但不要直接提问
- 涉及时间相关步骤时，必须沿用 time_unit_contract 的单位口径（分钟/次数/时间格式）
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
    plan_items = normalize_plan_items(payload)
    if status == "replanned":
        if not plan_items:
            return None
        return {"status": "replanned", "plan": plan_items}
    if status == "done":
        response = str(payload.get("response") or "").strip()
        if not response:
            return None
        return {"status": "done", "response": response}
    return None
