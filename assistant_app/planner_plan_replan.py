from __future__ import annotations

from typing import Any

from assistant_app.planner_common import normalize_tool_names

PLANNER_CAPABILITIES_TEXT = """
可用执行能力（用于规划步骤，不要求你输出工具命令）：
- schedule：日程管理（新增、查询、更新、删除、日历视图、重复规则）
  - 常用动作：add/list/get/view/update/repeat/delete
  - 关键字段：title（标题）、tag（标签）、event_time（开始时间，YYYY-MM-DD HH:MM）、
    duration_minutes（分钟）、remind_at（提醒时间）、interval_minutes/times/remind_start_time（重复规则）、
    view（day|week|month）与 anchor（锚点日期）
- internet_search：互联网检索网页信息并返回摘要（支持 query 关键词检索与已知 URL 正文抓取）
- history：历史会话检索（最近列表与关键词搜索）
  - 常用动作：list/search
  - 关键字段：keyword（搜索关键词，可用于 search）、limit（返回条数上限，>=1）
- ask_user：当信息不足时向用户发起澄清（question 文本，由 thought 阶段触发）
""".strip()

PLAN_INTENT_EXPANSION_RULE = (
    "先将用户口语化表达扩展成可执行且信息完整的目标再写计划步骤"
    "（如“看一下/看看/查一下”通常表示“查询并列出来给用户查看”；"
    "若关键信息缺失，优先结合历史对话 messages 与 user_profile 补全默认信息。"
    "例如“看一下明天的天气”可扩展为“查询用户默认城市的明天天气，并输出天气结果与衣着建议”）"
)
PLANNER_HISTORY_RULE = (
    "请求 messages 中会追加历史对话（近 24 小时，最多 50 轮）"
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
  "goal": "扩展后的目标描述",
  "plan": [
    {{"task": "步骤1", "completed": false, "tools": ["schedule"]}},
    {{"task": "步骤2", "completed": false, "tools": ["history"]}}
  ]
}}

规则：
- 只输出 planned，不要输出 done
- goal 必须是对用户原始 goal 的扩展版本，语义不变但信息更完整、可执行
- plan 默认为执行步骤数组；若判定用户输入只是对上一轮最终回答的确认/致谢（例如“谢谢”“好的”“明白了”），
  可输出空数组 [] 表示 ack-only（无需后续执行）
- 若用户输入里包含新的明确任务意图（例如“好的，顺便帮我查明天天气”），不得输出空数组
- 非空 plan 应按执行顺序排列
- plan 每项都必须包含 task/completed/tools
- plan 中每项的 completed 必须为 false
- tools 仅填写该子任务所需工具，工具名可用：schedule|internet_search|history
- {PLAN_INTENT_EXPANSION_RULE}
- {PLANNER_HISTORY_RULE}
- {PLANNER_USER_PROFILE_RULE}
- 不要输出工具参数示例或命令字符串；tools 只填工具名列表
""".strip()

REPLAN_PROMPT = f"""
你是 CLI 助手的 replan 模块，需要在一个子任务的 thought->act->observe 循环完成后更新计划进度。
你每次必须只输出一个 JSON 对象，禁止输出额外文本。

{PLANNER_CAPABILITIES_TEXT}

输出 JSON 格式：
{{
  "status": "replanned|done",
  "plan": [
    {{"task": "步骤1", "completed": true, "tools": ["history"]}},
    {{"task": "步骤2", "completed": false, "tools": ["schedule"]}}
  ],
  "response": "string|null"
}}

规则：
- status=replanned: 必须输出计划数组（至少 1 项）
- status=replanned: plan 每项都必须包含 task/completed/tools
- status=replanned: 至少要有 1 项 completed=false，表示仍有后续可执行任务
- status=replanned: tools 仅填写该子任务可执行工具名，工具名可用：schedule|internet_search|history
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
    goal = str(payload.get("goal") or "").strip()
    if status == "planned":
        raw_plan = payload.get("plan", [])
        if raw_plan is None:
            raw_plan = []
        if not goal or not isinstance(raw_plan, list):
            return None
        plan_items: list[dict[str, Any]] = []
        for item in raw_plan:
            if not isinstance(item, dict):
                return None
            task = str(item.get("task") or "").strip()
            completed = item.get("completed")
            tools = normalize_tool_names(item.get("tools"))
            if not task or completed is not False or tools is None:
                return None
            plan_items.append({"task": task, "completed": False, "tools": tools})
        return {"status": "planned", "goal": goal, "plan": plan_items}
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
            tools = normalize_tool_names(item.get("tools"))
            if not task or not isinstance(completed, bool) or tools is None:
                return None
            if not completed:
                has_pending = True
            plan_items.append({"task": task, "completed": completed, "tools": tools})
        if not plan_items or not has_pending:
            return None
        return {"status": "replanned", "plan": plan_items}
    if status == "done":
        response = str(payload.get("response") or "").strip()
        if not response:
            return None
        return {"status": "done", "response": response}
    return None
