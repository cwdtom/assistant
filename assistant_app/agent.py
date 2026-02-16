from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from assistant_app.db import AssistantDB
from assistant_app.llm import LLMClient
from assistant_app.search import BingSearchProvider, SearchProvider, SearchResult

SCHEDULE_EVENT_PREFIX_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s+(.+)$")
SCHEDULE_INTERVAL_OPTION_PATTERN = re.compile(r"(^|\s)--interval\s+(\d+)")
SCHEDULE_TIMES_OPTION_PATTERN = re.compile(r"(^|\s)--times\s+(-?\d+)")
SCHEDULE_DURATION_OPTION_PATTERN = re.compile(r"(^|\s)--duration\s+(\d+)")
SCHEDULE_REMIND_OPTION_PATTERN = re.compile(r"(^|\s)--remind\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})")
SCHEDULE_REMIND_START_OPTION_PATTERN = re.compile(
    r"(^|\s)--remind-start\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})"
)
TODO_TAG_OPTION_PATTERN = re.compile(r"(^|\s)--tag\s+(\S+)")
TODO_VIEW_OPTION_PATTERN = re.compile(r"(^|\s)--view\s+(\S+)")
TODO_PRIORITY_OPTION_PATTERN = re.compile(r"(^|\s)--priority\s+(-?\d+)")
TODO_DUE_OPTION_PATTERN = re.compile(r"(^|\s)--due\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})")
TODO_REMIND_OPTION_PATTERN = re.compile(r"(^|\s)--remind\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})")
TODO_VIEW_NAMES = ("all", "today", "overdue", "upcoming", "inbox")
SCHEDULE_VIEW_NAMES = ("day", "week", "month")
DEFAULT_INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS = 31
DEFAULT_PLAN_REPLAN_MAX_STEPS = 20
DEFAULT_PLAN_REPLAN_RETRY_COUNT = 2
DEFAULT_PLAN_OBSERVATION_CHAR_LIMIT = 10000
DEFAULT_PLAN_OBSERVATION_HISTORY_LIMIT = 100
DEFAULT_PLAN_CONTINUOUS_FAILURE_LIMIT = 2
DEFAULT_TASK_CANCEL_COMMAND = "取消当前任务"
DEFAULT_INTERNET_SEARCH_TOP_K = 3
DEFAULT_SCHEDULE_MAX_WINDOW_DAYS = 31

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
""".strip()

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
  "status": "continue|step_done|ask_user|done",
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
- status=step_done: 标记当前计划项完成，next_action/question 为空
- status=ask_user: question 必填，next_action/response 为空
- status=done: response 必填，next_action/question 为空
- todo/schedule 的 next_action.input 必须是可直接执行的合法命令
""".strip()

REPLAN_PROMPT = """
你是 CLI 助手的 replan 模块，只在用户澄清后更新计划。
你每次必须只输出一个 JSON 对象，禁止输出额外文本。

输出 JSON 格式：
{
  "status": "replanned",
  "plan": ["更新后的步骤1", "更新后的步骤2"]
}

规则：
- 只输出 replanned，不要输出 done
- 新计划要融合用户澄清信息
- 若信息仍不足，可保留待澄清步骤，但不要直接提问
""".strip()

PLAN_TOOL_CONTRACT: dict[str, list[str]] = {
    "todo": [
        "/todo add <内容> [--tag <标签>] [--priority <>=0>] [--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]",
        "/todo list [--tag <标签>] [--view <all|today|overdue|upcoming|inbox>]",
        "/todo get <id>",
        "/todo update <id> <内容> [--tag <标签>] [--priority <>=0>] "
        "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]",
        "/todo done <id>",
        "/todo delete <id>",
        "/todo search <关键词> [--tag <标签>]",
        "/view <all|today|overdue|upcoming|inbox> [--tag <标签>]",
    ],
    "schedule": [
        "/schedule add <YYYY-MM-DD HH:MM> <标题> [--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
        "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]",
        "/schedule list",
        "/schedule get <id>",
        "/schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM]",
        "/schedule update <id> <YYYY-MM-DD HH:MM> <标题> [--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
        "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]",
        "/schedule repeat <id> <on|off>",
        "/schedule delete <id>",
    ],
    "internet_search": ["<关键词>"],
    "ask_user": ["<单个澄清问题>"],
}


@dataclass
class PlannerObservation:
    tool: str
    input_text: str
    ok: bool
    result: str


@dataclass
class PendingPlanTask:
    goal: str
    clarification_history: list[str] = field(default_factory=list)
    observations: list[PlannerObservation] = field(default_factory=list)
    step_count: int = 0
    plan_initialized: bool = False
    awaiting_clarification: bool = False
    needs_replan_after_clarification: bool = False
    latest_plan: list[str] = field(default_factory=list)
    current_plan_index: int = 0
    last_thought_snapshot: str | None = None
    planner_failure_rounds: int = 0
    last_ask_user_question: str | None = None
    last_ask_user_clarification_len: int = 0
    ask_user_repeat_count: int = 0
    successful_steps: int = 0
    failed_steps: int = 0
    last_reported_plan_signature: tuple[str, ...] | None = None


class AssistantAgent:
    def __init__(
        self,
        db: AssistantDB,
        llm_client: LLMClient | None = None,
        search_provider: SearchProvider | None = None,
        progress_callback: Callable[[str], None] | None = None,
        plan_replan_max_steps: int = DEFAULT_PLAN_REPLAN_MAX_STEPS,
        plan_replan_retry_count: int = DEFAULT_PLAN_REPLAN_RETRY_COUNT,
        plan_observation_char_limit: int = DEFAULT_PLAN_OBSERVATION_CHAR_LIMIT,
        plan_observation_history_limit: int = DEFAULT_PLAN_OBSERVATION_HISTORY_LIMIT,
        plan_continuous_failure_limit: int = DEFAULT_PLAN_CONTINUOUS_FAILURE_LIMIT,
        task_cancel_command: str = DEFAULT_TASK_CANCEL_COMMAND,
        internet_search_top_k: int = DEFAULT_INTERNET_SEARCH_TOP_K,
        schedule_max_window_days: int = DEFAULT_SCHEDULE_MAX_WINDOW_DAYS,
        infinite_repeat_conflict_preview_days: int = DEFAULT_INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS,
    ) -> None:
        self.db = db
        self.llm_client = llm_client
        self.search_provider = search_provider or BingSearchProvider()
        self._pending_plan_task: PendingPlanTask | None = None
        self._progress_callback = progress_callback
        self._plan_replan_max_steps = max(plan_replan_max_steps, 1)
        self._plan_replan_retry_count = max(plan_replan_retry_count, 0)
        self._plan_observation_char_limit = max(plan_observation_char_limit, 1)
        self._plan_observation_history_limit = max(plan_observation_history_limit, 1)
        self._plan_continuous_failure_limit = max(plan_continuous_failure_limit, 1)
        self._task_cancel_command = task_cancel_command.strip() or DEFAULT_TASK_CANCEL_COMMAND
        self._internet_search_top_k = max(internet_search_top_k, 1)
        self._schedule_max_window_days = max(schedule_max_window_days, 1)
        self._infinite_repeat_conflict_preview_days = max(infinite_repeat_conflict_preview_days, 1)

    def set_progress_callback(self, callback: Callable[[str], None] | None) -> None:
        self._progress_callback = callback

    def handle_input(self, user_input: str) -> str:
        text = user_input.strip()
        if not text:
            return "请输入内容。输入 /help 查看可用命令。"

        if text == self._task_cancel_command:
            if self._pending_plan_task is None:
                return "当前没有进行中的任务。"
            self._pending_plan_task = None
            return "已取消当前任务。"

        if text.startswith("/"):
            return self._handle_command(text)

        if not self.llm_client:
            return "当前未配置 LLM。请设置 DEEPSEEK_API_KEY 后重试。"

        pending_task = self._pending_plan_task
        if pending_task is not None:
            self._pending_plan_task = None
            pending_task.clarification_history.append(f"用户补充: {text}")
            pending_task.awaiting_clarification = False
            pending_task.needs_replan_after_clarification = True
            return self._run_plan_thought_loop(task=pending_task, current_user_text=text)

        task = PendingPlanTask(goal=text)
        return self._run_plan_thought_loop(task=task, current_user_text=text)

    def _run_plan_thought_loop(self, task: PendingPlanTask, current_user_text: str) -> str:
        while True:
            if task.step_count >= self._plan_replan_max_steps:
                return self._finalize_planner_task(task, self._format_step_limit_response(task))

            planned_total_text = self._progress_total_text(task)
            current_plan_total = self._current_plan_total_text(task)
            plan_suffix = f"（当前计划 {current_plan_total} 步）" if current_plan_total is not None else ""
            progress_text = (
                f"步骤进度：已执行 {task.step_count}/{planned_total_text}，"
                f"开始第 {task.step_count + 1} 步决策。{plan_suffix}"
            )
            self._emit_progress(progress_text)

            if not task.plan_initialized:
                plan_payload = self._request_plan_payload(task)
                if plan_payload is None:
                    return self._finalize_planner_task(task, self._planner_unavailable_text())
                plan_decision = plan_payload.get("decision")
                if not isinstance(plan_decision, dict):
                    return self._finalize_planner_task(task, self._planner_unavailable_text())
                task.latest_plan = [str(item).strip() for item in plan_decision.get("plan", []) if str(item).strip()]
                task.plan_initialized = True
                task.current_plan_index = 0
                self._emit_progress(f"规划完成：共 {len(task.latest_plan)} 步。")
                self._emit_plan_progress(task)

            if task.awaiting_clarification:
                self._pending_plan_task = task
                return "请确认：请补充必要信息。"

            if task.needs_replan_after_clarification:
                task.step_count += 1
                replan_payload = self._request_replan_payload(task)
                if replan_payload is None:
                    task.planner_failure_rounds += 1
                    self._append_observation(
                        task,
                        PlannerObservation(
                            tool="replan",
                            input_text="plan",
                            ok=False,
                            result="replan 输出不符合 JSON 契约。",
                        ),
                    )
                    if task.planner_failure_rounds >= self._plan_continuous_failure_limit:
                        return self._finalize_planner_task(task, self._planner_unavailable_text())
                    self._emit_progress("重规划失败：模型输出不符合契约，准备重试。")
                    continue

                task.planner_failure_rounds = 0
                replan_decision = replan_payload.get("decision")
                if not isinstance(replan_decision, dict):
                    return self._finalize_planner_task(task, self._planner_unavailable_text())
                task.latest_plan = [str(item).strip() for item in replan_decision.get("plan", []) if str(item).strip()]
                task.current_plan_index = 0
                task.needs_replan_after_clarification = False
                self._emit_progress(f"重规划完成：共 {len(task.latest_plan)} 步。")
                self._emit_plan_progress(task)

            self._emit_current_plan_item_progress(task)
            task.step_count += 1
            thought_payload = self._request_thought_payload(task)
            if thought_payload is None:
                task.planner_failure_rounds += 1
                self._append_observation(
                    task,
                    PlannerObservation(
                        tool="thought",
                        input_text="decision",
                        ok=False,
                        result="thought 输出不符合 JSON 契约。",
                    ),
                )
                if task.planner_failure_rounds >= self._plan_continuous_failure_limit:
                    return self._finalize_planner_task(task, self._planner_unavailable_text())
                self._emit_progress("思考失败：模型输出不符合契约，准备重试。")
                continue

            task.planner_failure_rounds = 0
            thought_decision = thought_payload.get("decision")
            if not isinstance(thought_decision, dict):
                return self._finalize_planner_task(task, self._planner_unavailable_text())

            status = str(thought_decision.get("status") or "").strip().lower()
            current_step = str(thought_decision.get("current_step") or "").strip()
            if current_step:
                task.last_thought_snapshot = current_step
            self._emit_progress(f"思考决策：{status} | {current_step or '（未提供步骤）'}")

            if status == "done":
                response = str(thought_decision.get("response") or "").strip()
                if response:
                    return self._finalize_planner_task(task, response)
                return self._finalize_planner_task(task, self._planner_unavailable_text())

            if status == "step_done":
                if task.latest_plan:
                    task.current_plan_index = min(task.current_plan_index + 1, len(task.latest_plan))
                continue

            if status == "ask_user":
                question = str(thought_decision.get("question") or "").strip()
                if not question:
                    self._append_observation(
                        task,
                        PlannerObservation(
                            tool="ask_user",
                            input_text="",
                            ok=False,
                            result="ask_user 缺少提问内容。",
                        ),
                    )
                    continue
                if (
                    _is_same_question_text(task.last_ask_user_question, question)
                    and len(task.clarification_history) > task.last_ask_user_clarification_len
                ):
                    task.ask_user_repeat_count += 1
                    self._append_observation(
                        task,
                        PlannerObservation(
                            tool="ask_user",
                            input_text=question,
                            ok=False,
                            result="重复提问：用户已补充信息，请基于已知信息执行重规划。",
                        ),
                    )
                    if task.ask_user_repeat_count >= self._plan_continuous_failure_limit:
                        return self._finalize_planner_task(
                            task,
                            "我已经拿到你的补充信息，但仍无法完成重规划。请直接使用 /todo 或 /schedule 命令。",
                        )
                    continue
                ask_turns = sum(1 for line in task.clarification_history if line.startswith("助手提问:"))
                if ask_turns >= 6:
                    return self._finalize_planner_task(
                        task,
                        "澄清次数过多，我仍无法稳定重规划。请直接使用 /todo 或 /schedule 命令。",
                    )
                task.ask_user_repeat_count = 0
                task.last_ask_user_question = question
                task.last_ask_user_clarification_len = len(task.clarification_history)
                task.clarification_history.append(f"助手提问: {question}")
                task.awaiting_clarification = True
                self._emit_progress(f"步骤动作：ask_user -> {question}")
                self._pending_plan_task = task
                return f"请确认：{question}"

            next_action = thought_decision.get("next_action")
            if not isinstance(next_action, dict):
                self._append_observation(
                    task,
                    PlannerObservation(
                        tool="thought",
                        input_text="next_action",
                        ok=False,
                        result="status=continue 但 next_action 为空。",
                    ),
                )
                continue
            action_tool = str(next_action.get("tool") or "").strip().lower()
            action_input = str(next_action.get("input") or "").strip()
            self._emit_progress(f"步骤动作：{action_tool} -> {action_input}")
            task.step_count += 1
            observation = self._execute_planner_tool(action_tool=action_tool, action_input=action_input)
            self._append_observation(task, observation)
            status_text = "成功" if observation.ok else "失败"
            if observation.ok:
                task.successful_steps += 1
            else:
                task.failed_steps += 1
            preview = _truncate_text(observation.result.replace("\n", " "), 220)
            self._emit_progress(f"步骤结果：{status_text} | {preview}")
            planned_total_text = self._progress_total_text(task)
            current_plan_total = self._current_plan_total_text(task)
            plan_suffix = f"，当前计划 {current_plan_total} 步" if current_plan_total is not None else ""
            self._emit_progress(
                "完成情况："
                f"成功 {task.successful_steps} 步，失败 {task.failed_steps} 步，"
                f"已执行 {task.step_count}/{planned_total_text} 步（上限 {self._plan_replan_max_steps}{plan_suffix}）。"
            )

    def _request_plan_payload(self, task: PendingPlanTask) -> dict[str, Any] | None:
        if not self.llm_client:
            return None

        planner_messages = self._build_plan_messages(task)
        return self._request_payload_with_retry(planner_messages, _normalize_plan_decision)

    def _request_thought_payload(self, task: PendingPlanTask) -> dict[str, Any] | None:
        if not self.llm_client:
            return None

        planner_messages = self._build_thought_messages(task)
        return self._request_payload_with_retry(planner_messages, _normalize_thought_decision)

    def _request_replan_payload(self, task: PendingPlanTask) -> dict[str, Any] | None:
        if not self.llm_client:
            return None

        planner_messages = self._build_replan_messages(task)
        return self._request_payload_with_retry(planner_messages, _normalize_replan_decision)

    def _request_payload_with_retry(
        self,
        messages: list[dict[str, str]],
        normalizer: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        max_attempts = 1 + self._plan_replan_retry_count
        for _ in range(max_attempts):
            try:
                raw = self._llm_reply_for_planner(messages)
            except Exception:
                continue
            payload = _try_parse_json(_strip_think_blocks(raw).strip())
            if not isinstance(payload, dict):
                continue

            decision = normalizer(payload)
            if decision is not None:
                return {"decision": decision}
        return None

    def _build_plan_messages(self, task: PendingPlanTask) -> list[dict[str, str]]:
        context_payload = self._build_planner_context(task)
        context_payload["phase"] = "plan"
        return [
            {"role": "system", "content": PLAN_ONCE_PROMPT},
            {"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)},
        ]

    def _build_thought_messages(self, task: PendingPlanTask) -> list[dict[str, str]]:
        context_payload = self._build_planner_context(task)
        context_payload["phase"] = "thought"
        context_payload["current_plan_item"] = self._current_plan_item_text(task)
        return [
            {"role": "system", "content": THOUGHT_PROMPT},
            {"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)},
        ]

    def _build_replan_messages(self, task: PendingPlanTask) -> list[dict[str, str]]:
        context_payload = self._build_planner_context(task)
        context_payload["phase"] = "replan"
        context_payload["current_plan_item"] = self._current_plan_item_text(task)
        return [
            {"role": "system", "content": REPLAN_PROMPT},
            {"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)},
        ]

    def _build_planner_context(self, task: PendingPlanTask) -> dict[str, Any]:
        observations = [
            {
                "tool": item.tool,
                "input": item.input_text,
                "ok": item.ok,
                "result": item.result,
            }
            for item in task.observations
        ]
        context_payload = {
            "goal": task.goal,
            "clarification_history": task.clarification_history,
            "step_count": task.step_count,
            "max_steps": self._plan_replan_max_steps,
            "latest_plan": task.latest_plan,
            "current_plan_index": task.current_plan_index,
            "observations": observations,
            "tool_contract": PLAN_TOOL_CONTRACT,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        return context_payload

    @staticmethod
    def _current_plan_item_text(task: PendingPlanTask) -> str:
        if not task.latest_plan:
            return ""
        if task.current_plan_index < 0:
            return ""
        if task.current_plan_index >= len(task.latest_plan):
            return ""
        return task.latest_plan[task.current_plan_index]

    def _llm_reply_for_planner(self, messages: list[dict[str, str]]) -> str:
        if self.llm_client is None:
            return ""

        reply_json = getattr(self.llm_client, "reply_json", None)
        if callable(reply_json):
            try:
                return str(reply_json(messages))
            except Exception:
                pass
        return self.llm_client.reply(messages)

    def _execute_planner_tool(self, *, action_tool: str, action_input: str) -> PlannerObservation:
        if action_tool == "todo":
            normalized_command = action_input.strip()
            if not normalized_command.startswith("/todo") and not normalized_command.startswith("/view"):
                return PlannerObservation(
                    tool="todo",
                    input_text=action_input,
                    ok=False,
                    result="todo 工具仅支持 /todo 或 /view 命令。",
                )
            command_result = self._handle_command(normalized_command)
            ok = not command_result.startswith("用法:") and not command_result.startswith("未知命令")
            return PlannerObservation(tool="todo", input_text=normalized_command, ok=ok, result=command_result)

        if action_tool == "schedule":
            normalized_command = action_input.strip()
            if not normalized_command.startswith("/schedule"):
                return PlannerObservation(
                    tool="schedule",
                    input_text=action_input,
                    ok=False,
                    result="schedule 工具仅支持 /schedule 命令。",
                )
            command_result = self._handle_command(normalized_command)
            ok = not command_result.startswith("用法:") and not command_result.startswith("未知命令")
            return PlannerObservation(tool="schedule", input_text=normalized_command, ok=ok, result=command_result)

        if action_tool == "internet_search":
            query = action_input.strip()
            if not query:
                return PlannerObservation(
                    tool="internet_search",
                    input_text=action_input,
                    ok=False,
                    result="internet_search 缺少查询词。",
                )
            try:
                search_results = self.search_provider.search(query, top_k=self._internet_search_top_k)
            except Exception as exc:  # noqa: BLE001
                return PlannerObservation(
                    tool="internet_search",
                    input_text=query,
                    ok=False,
                    result=f"搜索失败: {exc}",
                )
            if not search_results:
                return PlannerObservation(
                    tool="internet_search",
                    input_text=query,
                    ok=False,
                    result=f"未搜索到与“{query}”相关的结果。",
                )
            formatted = _format_search_results(search_results)
            return PlannerObservation(tool="internet_search", input_text=query, ok=True, result=formatted)

        return PlannerObservation(
            tool=action_tool or "unknown",
            input_text=action_input,
            ok=False,
            result=f"未知工具: {action_tool}",
        )

    def _append_observation(self, task: PendingPlanTask, observation: PlannerObservation) -> None:
        truncated = _truncate_text(observation.result, self._plan_observation_char_limit)
        task.observations.append(
            PlannerObservation(
                tool=observation.tool,
                input_text=observation.input_text,
                ok=observation.ok,
                result=truncated,
            )
        )
        if len(task.observations) > self._plan_observation_history_limit:
            task.observations = task.observations[-self._plan_observation_history_limit :]

    def _format_step_limit_response(self, task: PendingPlanTask) -> str:
        completed = [obs for obs in task.observations if obs.ok and obs.tool != "planner"]
        failed = [obs for obs in task.observations if not obs.ok]
        completed_lines = [f"- {item.tool}: {item.input_text}" for item in completed[-3:]] or ["- 暂无已完成动作。"]
        failed_reason = failed[-1].result if failed else "需要更多信息才能继续。"
        return (
            f"已达到最大执行步数（{self._plan_replan_max_steps}）。\n"
            "已完成部分:\n"
            f"{chr(10).join(completed_lines)}\n"
            "未完成原因:\n"
            f"- {failed_reason}\n"
            "下一步建议:\n"
            "- 你可以补充更具体的时间、编号或关键词；\n"
            "- 或直接使用 /todo、/schedule 命令完成关键操作。"
        )

    def _finalize_planner_task(self, task: PendingPlanTask, response: str) -> str:
        if self._pending_plan_task is task:
            self._pending_plan_task = None
        self._emit_progress("任务状态：已完成。")
        return response

    @staticmethod
    def _planner_unavailable_text() -> str:
        return "抱歉，当前计划执行服务暂时不可用。你可以稍后重试，或先使用 /todo、/schedule 命令继续操作。"

    def _emit_progress(self, message: str) -> None:
        callback = self._progress_callback
        if callback is None:
            return
        callback(message)

    def _emit_plan_progress(self, task: PendingPlanTask) -> None:
        if not task.latest_plan:
            return
        signature = tuple(task.latest_plan)
        if task.last_reported_plan_signature == signature:
            return
        task.last_reported_plan_signature = signature
        done_count = min(task.current_plan_index, len(task.latest_plan))
        lines = ["计划列表："]
        for idx, item in enumerate(task.latest_plan, start=1):
            status = "完成" if idx <= done_count else "待办"
            lines.append(f"{idx}. [{status}] {item}")
        self._emit_progress("\n".join(lines))

    def _emit_current_plan_item_progress(self, task: PendingPlanTask) -> None:
        if not task.latest_plan:
            return
        if task.current_plan_index < 0 or task.current_plan_index >= len(task.latest_plan):
            return
        index = task.current_plan_index + 1
        total = len(task.latest_plan)
        self._emit_progress(f"当前计划项：{index}/{total} - {task.latest_plan[task.current_plan_index]}")

    @staticmethod
    def _progress_total_text(task: PendingPlanTask) -> str:
        if not task.latest_plan:
            return "未定"
        # Replan may shorten current plan. Keep denominator >= executed steps to avoid "20/1" style confusion.
        return str(max(task.step_count, len(task.latest_plan)))

    @staticmethod
    def _current_plan_total_text(task: PendingPlanTask) -> str | None:
        if not task.latest_plan:
            return None
        return str(len(task.latest_plan))

    def _handle_command(self, command: str) -> str:
        if command == "/help":
            return self._help_text()

        if command == "/view list":
            return self._todo_view_list_text()

        if command.startswith("/view "):
            view_parsed = _parse_view_command_input(command.removeprefix("/view ").strip())
            if view_parsed is None:
                return "用法: /view <all|today|overdue|upcoming|inbox> [--tag <标签>]"
            view_name, view_tag = view_parsed
            list_cmd = f"/todo list --view {view_name}"
            if view_tag is not None:
                list_cmd += f" --tag {view_tag}"
            return self._handle_command(list_cmd)

        if command.startswith("/todo add "):
            add_parsed = _parse_todo_add_input(command.removeprefix("/todo add ").strip())
            if add_parsed is None:
                return (
                    "用法: /todo add <内容> [--tag <标签>] [--priority <>=0>] "
                    "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]"
                )
            content, add_tag, add_priority, add_due_at, add_remind_at = add_parsed
            if not content:
                return (
                    "用法: /todo add <内容> [--tag <标签>] [--priority <>=0>] "
                    "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]"
                )
            try:
                added_todo_id = self.db.add_todo(
                    content,
                    tag=add_tag,
                    priority=add_priority,
                    due_at=add_due_at,
                    remind_at=add_remind_at,
                )
            except ValueError:
                return "提醒时间需要和截止时间一起设置，且优先级必须为大于等于 0 的整数。"
            return (
                f"已添加待办 #{added_todo_id} [标签:{add_tag}]: {content}"
                f"{_format_todo_meta_inline(add_due_at, add_remind_at, priority=add_priority)}"
            )

        if command == "/todo list" or command.startswith("/todo list "):
            list_parsed = _parse_todo_list_options(command)
            if list_parsed is None:
                return "用法: /todo list [--tag <标签>] [--view <all|today|overdue|upcoming|inbox>]"
            list_tag, list_view = list_parsed
            todos = self.db.list_todos(tag=list_tag)
            todos = _filter_todos_by_view(todos, view_name=list_view)
            if not todos:
                if list_tag is None and list_view == "all":
                    return "暂无待办。"
                if list_tag is None:
                    return f"视图 {list_view} 下暂无待办。"
                if list_view == "all":
                    return f"标签 {list_tag} 下暂无待办。"
                return f"标签 {list_tag} 的 {list_view} 视图下暂无待办。"

            header_parts: list[str] = []
            if list_tag is not None:
                header_parts.append(f"标签: {list_tag}")
            if list_view != "all":
                header_parts.append(f"视图: {list_view}")
            if header_parts:
                header = f"待办列表({', '.join(header_parts)}):"
            else:
                header = "待办列表:"
            rows = [
                [
                    str(item.id),
                    "完成" if item.done else "待办",
                    item.tag,
                    str(item.priority),
                    item.content,
                    item.created_at,
                    item.completed_at or "-",
                    item.due_at or "-",
                    item.remind_at or "-",
                ]
                for item in todos
            ]
            table = _render_table(
                headers=["ID", "状态", "标签", "优先级", "内容", "创建时间", "完成时间", "截止时间", "提醒时间"],
                rows=rows,
            )
            return f"{header}\n{table}"

        if command.startswith("/todo search "):
            search_parsed = _parse_todo_search_input(command.removeprefix("/todo search ").strip())
            if search_parsed is None:
                return "用法: /todo search <关键词> [--tag <标签>]"
            keyword, search_tag = search_parsed
            todos = self.db.search_todos(keyword, tag=search_tag)
            if not todos:
                if search_tag is None:
                    return f"未找到包含“{keyword}”的待办。"
                return f"未在标签 {search_tag} 下找到包含“{keyword}”的待办。"

            rows = [
                [
                    str(item.id),
                    "完成" if item.done else "待办",
                    item.tag,
                    str(item.priority),
                    item.content,
                    item.created_at,
                    item.completed_at or "-",
                    item.due_at or "-",
                    item.remind_at or "-",
                ]
                for item in todos
            ]
            table = _render_table(
                headers=["ID", "状态", "标签", "优先级", "内容", "创建时间", "完成时间", "截止时间", "提醒时间"],
                rows=rows,
            )
            if search_tag is None:
                header = f"搜索结果(关键词: {keyword}):"
            else:
                header = f"搜索结果(关键词: {keyword}, 标签: {search_tag}):"
            return f"{header}\n{table}"

        if command.startswith("/todo get "):
            get_todo_id = _parse_positive_int(command.removeprefix("/todo get ").strip())
            if get_todo_id is None:
                return "用法: /todo get <id>"
            todo = self.db.get_todo(get_todo_id)
            if todo is None:
                return f"未找到待办 #{get_todo_id}"
            status = "x" if todo.done else " "
            completed_at = todo.completed_at or "-"
            due_at = todo.due_at or "-"
            remind_at = todo.remind_at or "-"
            table = _render_table(
                headers=["ID", "状态", "标签", "优先级", "内容", "创建时间", "完成时间", "截止时间", "提醒时间"],
                rows=[
                    [
                        str(todo.id),
                        "完成" if status == "x" else "待办",
                        todo.tag,
                        str(todo.priority),
                        todo.content,
                        todo.created_at,
                        completed_at,
                        due_at,
                        remind_at,
                    ]
                ],
            )
            return f"待办详情:\n{table}"

        if command.startswith("/todo update "):
            update_parsed = _parse_todo_update_input(command.removeprefix("/todo update ").strip())
            if update_parsed is None:
                return (
                    "用法: /todo update <id> <内容> [--tag <标签>] "
                    "[--priority <>=0>] [--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]"
                )
            (
                update_todo_id,
                content,
                update_tag,
                update_priority,
                update_due_at,
                update_remind_at,
                has_priority,
                has_due,
                has_remind,
            ) = update_parsed
            current = self.db.get_todo(update_todo_id)
            if current is None:
                return f"未找到待办 #{update_todo_id}"

            if has_remind and update_remind_at and not ((has_due and update_due_at) or current.due_at):
                return "提醒时间需要和截止时间一起设置。"

            update_kwargs: dict[str, Any] = {"content": content}
            if update_tag is not None:
                update_kwargs["tag"] = update_tag
            if has_priority:
                update_kwargs["priority"] = update_priority
            if has_due:
                update_kwargs["due_at"] = update_due_at
            if has_remind:
                update_kwargs["remind_at"] = update_remind_at

            updated = self.db.update_todo(update_todo_id, **update_kwargs)
            if not updated:
                return f"未找到待办 #{update_todo_id}"
            todo = self.db.get_todo(update_todo_id)
            if todo is None:
                return f"已更新待办 #{update_todo_id}: {content}"
            return (
                f"已更新待办 #{update_todo_id} [标签:{todo.tag}]: {content}"
                f"{_format_todo_meta_inline(todo.due_at, todo.remind_at, priority=todo.priority)}"
            )

        if command.startswith("/todo delete "):
            delete_todo_id = _parse_positive_int(command.removeprefix("/todo delete ").strip())
            if delete_todo_id is None:
                return "用法: /todo delete <id>"
            deleted = self.db.delete_todo(delete_todo_id)
            if not deleted:
                return f"未找到待办 #{delete_todo_id}"
            return f"待办 #{delete_todo_id} 已删除。"

        if command.startswith("/todo done "):
            id_text = command.removeprefix("/todo done ").strip()
            if not id_text.isdigit():
                return "用法: /todo done <id>"
            done = self.db.mark_todo_done(int(id_text))
            if not done:
                return f"未找到待办 #{id_text}"
            todo = self.db.get_todo(int(id_text))
            done_completed_at = todo.completed_at if todo is not None else _now_time_text()
            return f"待办 #{id_text} 已完成。完成时间: {done_completed_at}"

        if command == "/schedule list":
            window_start, window_end = _default_schedule_list_window(
                window_days=self._schedule_max_window_days
            )
            items = self.db.list_schedules(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._schedule_max_window_days,
            )
            if not items:
                return f"前天起未来 {self._schedule_max_window_days} 天内暂无日程。"
            table = _render_table(
                headers=[
                    "ID",
                    "时间",
                    "时长(分钟)",
                    "标题",
                    "提醒时间",
                    "重复提醒开始",
                    "重复间隔(分钟)",
                    "重复次数",
                    "重复启用",
                    "创建时间",
                ],
                rows=[
                    [
                        str(item.id),
                        item.event_time,
                        str(item.duration_minutes),
                        item.title,
                        item.remind_at or "-",
                        item.repeat_remind_start_time or "-",
                        str(item.repeat_interval_minutes) if item.repeat_interval_minutes is not None else "-",
                        str(item.repeat_times) if item.repeat_times is not None else "-",
                        _repeat_enabled_text(item.repeat_enabled),
                        item.created_at,
                    ]
                    for item in items
                ],
            )
            return f"日程列表(前天起未来 {self._schedule_max_window_days} 天):\n{table}"

        if command.startswith("/schedule view "):
            view_parsed = _parse_schedule_view_input(command.removeprefix("/schedule view ").strip())
            if view_parsed is None:
                return "用法: /schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM]"
            view_name, anchor = view_parsed
            window_start, window_end = _resolve_schedule_view_window(view_name=view_name, anchor=anchor)
            items = self.db.list_schedules(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._schedule_max_window_days,
            )
            items = _filter_schedules_by_calendar_view(items, view_name=view_name, anchor=anchor)
            if not items:
                return f"{view_name} 视图下暂无日程。"
            table = _render_table(
                headers=[
                    "ID",
                    "时间",
                    "时长(分钟)",
                    "标题",
                    "提醒时间",
                    "重复提醒开始",
                    "重复间隔(分钟)",
                    "重复次数",
                    "重复启用",
                    "创建时间",
                ],
                rows=[
                    [
                        str(item.id),
                        item.event_time,
                        str(item.duration_minutes),
                        item.title,
                        item.remind_at or "-",
                        item.repeat_remind_start_time or "-",
                        str(item.repeat_interval_minutes) if item.repeat_interval_minutes is not None else "-",
                        str(item.repeat_times) if item.repeat_times is not None else "-",
                        _repeat_enabled_text(item.repeat_enabled),
                        item.created_at,
                    ]
                    for item in items
                ],
            )
            if anchor:
                return f"日历视图({view_name}, {anchor}):\n{table}"
            return f"日历视图({view_name}):\n{table}"

        if command.startswith("/schedule get "):
            schedule_id = _parse_positive_int(command.removeprefix("/schedule get ").strip())
            if schedule_id is None:
                return "用法: /schedule get <id>"
            item = self.db.get_schedule(schedule_id)
            if item is None:
                return f"未找到日程 #{schedule_id}"
            table = _render_table(
                headers=[
                    "ID",
                    "时间",
                    "时长(分钟)",
                    "标题",
                    "提醒时间",
                    "重复提醒开始",
                    "重复间隔(分钟)",
                    "重复次数",
                    "重复启用",
                    "创建时间",
                ],
                rows=[
                    [
                        str(item.id),
                        item.event_time,
                        str(item.duration_minutes),
                        item.title,
                        item.remind_at or "-",
                        item.repeat_remind_start_time or "-",
                        str(item.repeat_interval_minutes) if item.repeat_interval_minutes is not None else "-",
                        str(item.repeat_times) if item.repeat_times is not None else "-",
                        _repeat_enabled_text(item.repeat_enabled),
                        item.created_at,
                    ]
                ],
            )
            return f"日程详情:\n{table}"

        if command.startswith("/schedule add"):
            add_schedule_parsed = _parse_schedule_add_input(command.removeprefix("/schedule add").strip())
            if add_schedule_parsed is None:
                return (
                    "用法: /schedule add <YYYY-MM-DD HH:MM> <标题> "
                    "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
                    "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
                )
            (
                event_time,
                title,
                duration_minutes,
                remind_at,
                repeat_interval_minutes,
                repeat_times,
                repeat_remind_start_time,
            ) = add_schedule_parsed
            event_times = _build_schedule_event_times(
                event_time=event_time,
                repeat_interval_minutes=repeat_interval_minutes,
                repeat_times=repeat_times,
                infinite_repeat_conflict_preview_days=self._infinite_repeat_conflict_preview_days,
            )
            conflicts = self.db.find_schedule_conflicts(
                event_times,
                duration_minutes=duration_minutes,
            )
            if conflicts:
                return _format_schedule_conflicts(conflicts)
            schedule_id = self.db.add_schedule(
                title=title,
                event_time=event_time,
                duration_minutes=duration_minutes,
                remind_at=remind_at,
            )
            if repeat_interval_minutes is not None and repeat_times != 1:
                self.db.set_schedule_recurrence(
                    schedule_id,
                    start_time=event_time,
                    repeat_interval_minutes=repeat_interval_minutes,
                    repeat_times=repeat_times,
                    remind_start_time=repeat_remind_start_time,
                )
            remind_meta = _format_schedule_remind_meta_inline(
                remind_at=remind_at,
                repeat_remind_start_time=repeat_remind_start_time,
            )
            if repeat_times == 1:
                return f"已添加日程 #{schedule_id}: {event_time} {title} ({duration_minutes} 分钟){remind_meta}"
            if repeat_times == -1:
                return (
                    f"已添加无限重复日程 #{schedule_id}: {event_time} {title} "
                    f"(duration={duration_minutes}m, interval={repeat_interval_minutes}m{remind_meta})"
                )
            return (
                f"已添加重复日程 {repeat_times} 条: {event_time} {title} "
                f"(duration={duration_minutes}m, interval={repeat_interval_minutes}m, "
                f"times={repeat_times}{remind_meta})"
            )

        if command.startswith("/schedule update "):
            update_schedule_parsed = _parse_schedule_update_input(command.removeprefix("/schedule update ").strip())
            if update_schedule_parsed is None:
                return (
                    "用法: /schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
                    "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
                    "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
                )
            (
                schedule_id,
                event_time,
                title,
                parsed_duration_minutes,
                parsed_remind_at,
                has_remind,
                repeat_interval_minutes,
                repeat_times,
                repeat_remind_start_time,
                has_repeat_remind_start_time,
            ) = update_schedule_parsed
            current_item = self.db.get_schedule(schedule_id)
            if current_item is None:
                return f"未找到日程 #{schedule_id}"
            if parsed_duration_minutes is not None:
                applied_duration_minutes = parsed_duration_minutes
            else:
                applied_duration_minutes = current_item.duration_minutes
            event_times = _build_schedule_event_times(
                event_time=event_time,
                repeat_interval_minutes=repeat_interval_minutes,
                repeat_times=repeat_times,
                infinite_repeat_conflict_preview_days=self._infinite_repeat_conflict_preview_days,
            )
            conflicts = self.db.find_schedule_conflicts(
                event_times,
                duration_minutes=applied_duration_minutes,
                exclude_schedule_id=schedule_id,
            )
            if conflicts:
                return _format_schedule_conflicts(conflicts)
            update_kwargs: dict[str, Any] = {
                "title": title,
                "event_time": event_time,
                "duration_minutes": applied_duration_minutes,
            }
            if has_remind:
                update_kwargs["remind_at"] = parsed_remind_at
            if has_repeat_remind_start_time:
                update_kwargs["repeat_remind_start_time"] = repeat_remind_start_time
            updated = self.db.update_schedule(schedule_id, **update_kwargs)
            if not updated:
                return f"未找到日程 #{schedule_id}"
            if repeat_times == 1:
                self.db.clear_schedule_recurrence(schedule_id)
                item = self.db.get_schedule(schedule_id)
                remind_meta = _format_schedule_remind_meta_inline(
                    remind_at=item.remind_at if item else None,
                    repeat_remind_start_time=item.repeat_remind_start_time if item else None,
                )
                return f"已更新日程 #{schedule_id}: {event_time} {title} ({applied_duration_minutes} 分钟){remind_meta}"
            if repeat_interval_minutes is not None:
                remind_start_for_rule = (
                    repeat_remind_start_time
                    if has_repeat_remind_start_time
                    else current_item.repeat_remind_start_time
                )
                self.db.set_schedule_recurrence(
                    schedule_id,
                    start_time=event_time,
                    repeat_interval_minutes=repeat_interval_minutes,
                    repeat_times=repeat_times,
                    remind_start_time=remind_start_for_rule,
                )
            item = self.db.get_schedule(schedule_id)
            remind_meta = _format_schedule_remind_meta_inline(
                remind_at=item.remind_at if item else None,
                repeat_remind_start_time=item.repeat_remind_start_time if item else None,
            )
            if repeat_times == -1:
                return (
                    f"已更新为无限重复日程 #{schedule_id}: {event_time} {title} "
                    f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m{remind_meta})"
                )
            return (
                f"已更新日程 #{schedule_id}: {event_time} {title} "
                f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m, "
                f"times={repeat_times}{remind_meta})"
            )

        if command.startswith("/schedule delete "):
            schedule_id = _parse_positive_int(command.removeprefix("/schedule delete ").strip())
            if schedule_id is None:
                return "用法: /schedule delete <id>"
            deleted = self.db.delete_schedule(schedule_id)
            if not deleted:
                return f"未找到日程 #{schedule_id}"
            return f"日程 #{schedule_id} 已删除。"

        if command.startswith("/schedule repeat "):
            repeat_toggle_parsed = _parse_schedule_repeat_toggle_input(
                command.removeprefix("/schedule repeat ").strip()
            )
            if repeat_toggle_parsed is None:
                return "用法: /schedule repeat <id> <on|off>"
            schedule_id, enabled = repeat_toggle_parsed
            changed = self.db.set_schedule_recurrence_enabled(schedule_id, enabled)
            if not changed:
                return f"日程 #{schedule_id} 没有可切换的重复规则。"
            status = "启用" if enabled else "停用"
            return f"已{status}日程 #{schedule_id} 的重复规则。"

        return "未知命令。输入 /help 查看可用命令。"

    @staticmethod
    def _todo_view_list_text() -> str:
        return (
            "可用视图:\n"
            "- all: 全部待办（含已完成）\n"
            "- today: 今天到期且未完成\n"
            "- overdue: 已逾期且未完成\n"
            "- upcoming: 未来 7 天到期且未完成\n"
            "- inbox: 未设置截止时间且未完成"
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "可用命令:\n"
            "/help\n"
            "/view list\n"
            "/view <all|today|overdue|upcoming|inbox> [--tag <标签>]\n"
            "/todo add <内容> [--tag <标签>] [--priority <>=0>] "
            "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]\n"
            "/todo list [--tag <标签>] [--view <all|today|overdue|upcoming|inbox>]\n"
            "/todo search <关键词> [--tag <标签>]\n"
            "/todo get <id>\n"
            "/todo update <id> <内容> [--tag <标签>] [--priority <>=0>] "
            "[--due <YYYY-MM-DD HH:MM>] [--remind <YYYY-MM-DD HH:MM>]\n"
            "/todo delete <id>\n"
            "/todo done <id>\n"
            "/schedule add <YYYY-MM-DD HH:MM> <标题> "
            "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
            "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]\n"
            "/schedule get <id>\n"
            "/schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM]\n"
            "/schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
            "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
            "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]\n"
            "/schedule repeat <id> <on|off>\n"
            "/schedule delete <id>\n"
            "/schedule list\n"
            "你也可以直接说自然语言（会走 plan -> act -> observe -> replan 循环）。\n"
            "当前版本仅支持计划链路，不再走 chat 直聊分支。"
        )


def _parse_positive_int(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    value = int(raw)
    if value <= 0:
        return None
    return value


def _parse_todo_add_input(raw: str) -> tuple[str, str, int, str | None, str | None] | None:
    parsed = _parse_todo_text_with_options(raw, default_tag="default", default_priority=0)
    if parsed is None:
        return None
    content, tag, priority, due_at, remind_at, _, _, _ = parsed
    if remind_at and not due_at:
        return None
    if tag is None:
        tag = "default"
    if priority is None:
        priority = 0
    return content, tag, priority, due_at, remind_at


def _parse_todo_update_input(
    raw: str,
) -> tuple[int, str, str | None, int | None, str | None, str | None, bool, bool, bool] | None:
    parts = raw.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None

    todo_id = _parse_positive_int(parts[0])
    if todo_id is None:
        return None

    parsed = _parse_todo_text_with_options(parts[1], default_tag=None, default_priority=None)
    if parsed is None:
        return None
    content, tag, priority, due_at, remind_at, has_priority, has_due, has_remind = parsed
    return todo_id, content, tag, priority, due_at, remind_at, has_priority, has_due, has_remind


def _parse_todo_text_with_options(
    raw: str,
    *,
    default_tag: str | None,
    default_priority: int | None,
) -> tuple[str, str | None, int | None, str | None, str | None, bool, bool, bool] | None:
    text = raw.strip()
    if not text:
        return None

    working = text
    tag: str | None = default_tag
    priority: int | None = default_priority
    due_at: str | None = None
    remind_at: str | None = None
    has_priority = False
    has_due = False
    has_remind = False

    tag_match = TODO_TAG_OPTION_PATTERN.search(working)
    if tag_match:
        provided_tag = _sanitize_tag(tag_match.group(2))
        if not provided_tag:
            return None
        tag = provided_tag
        working = _remove_option_span(working, tag_match.span())

    priority_match = TODO_PRIORITY_OPTION_PATTERN.search(working)
    if priority_match:
        parsed_priority = _normalize_todo_priority_value(priority_match.group(2))
        if parsed_priority is None:
            return None
        priority = parsed_priority
        has_priority = True
        working = _remove_option_span(working, priority_match.span())

    due_match = TODO_DUE_OPTION_PATTERN.search(working)
    if due_match:
        parsed_due = _normalize_datetime_text(due_match.group(2))
        if not parsed_due:
            return None
        due_at = parsed_due
        has_due = True
        working = _remove_option_span(working, due_match.span())

    remind_match = TODO_REMIND_OPTION_PATTERN.search(working)
    if remind_match:
        parsed_remind = _normalize_datetime_text(remind_match.group(2))
        if not parsed_remind:
            return None
        remind_at = parsed_remind
        has_remind = True
        working = _remove_option_span(working, remind_match.span())

    content = re.sub(r"\s+", " ", working).strip()
    if not content:
        return None

    return content, tag, priority, due_at, remind_at, has_priority, has_due, has_remind


def _parse_todo_list_options(command: str) -> tuple[str | None, str] | None:
    if command == "/todo list":
        return None, "all"

    suffix = command.removeprefix("/todo list").strip()
    if not suffix:
        return None, "all"

    working = suffix
    tag: str | None = None
    view_name = "all"

    tag_match = TODO_TAG_OPTION_PATTERN.search(working)
    if tag_match:
        parsed_tag = _sanitize_tag(tag_match.group(2))
        if parsed_tag is None:
            return None
        tag = parsed_tag
        working = _remove_option_span(working, tag_match.span())

    view_match = TODO_VIEW_OPTION_PATTERN.search(working)
    if view_match:
        parsed_view = _normalize_todo_view_value(view_match.group(2))
        if parsed_view is None:
            return None
        view_name = parsed_view
        working = _remove_option_span(working, view_match.span())

    leftover = re.sub(r"\s+", " ", working).strip()
    if leftover:
        if " " in leftover:
            return None
        if leftover.startswith("--"):
            return None
        parsed_tag = _sanitize_tag(leftover)
        if parsed_tag is None:
            return None
        if tag is not None:
            return None
        tag = parsed_tag

    return tag, view_name


def _parse_view_command_input(raw: str) -> tuple[str, str | None] | None:
    text = raw.strip()
    if not text:
        return None

    parts = text.split(maxsplit=1)
    view_name = _normalize_todo_view_value(parts[0])
    if view_name is None:
        return None

    if len(parts) == 1:
        return view_name, None

    suffix = parts[1].strip()
    if not suffix:
        return view_name, None

    option_match = re.match(r"^--tag\s+(\S+)$", suffix)
    if option_match is None:
        return None
    tag = _sanitize_tag(option_match.group(1))
    if tag is None:
        return None
    return view_name, tag


def _parse_todo_search_input(raw: str) -> tuple[str, str | None] | None:
    text = raw.strip()
    if not text:
        return None

    working = text
    tag: str | None = None

    tag_match = TODO_TAG_OPTION_PATTERN.search(working)
    if tag_match:
        provided_tag = _sanitize_tag(tag_match.group(2))
        if not provided_tag:
            return None
        tag = provided_tag
        working = _remove_option_span(working, tag_match.span())

    keyword = re.sub(r"\s+", " ", working).strip()
    if not keyword:
        return None
    return keyword, tag


def _parse_schedule_add_input(
    raw: str,
) -> tuple[str, str, int, str | None, int | None, int, str | None] | None:
    parsed = _parse_schedule_input(raw, default_duration_minutes=60)
    if parsed is None:
        return None
    (
        event_time,
        title,
        duration_minutes,
        remind_at,
        _has_remind,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
        has_repeat_remind_start_time,
    ) = parsed
    if duration_minutes is None:
        return None
    if repeat_interval_minutes is None and repeat_times != 1:
        return None
    if repeat_interval_minutes is not None and repeat_times == 1:
        return None
    if has_repeat_remind_start_time and repeat_interval_minutes is None:
        return None
    return (
        event_time,
        title,
        duration_minutes,
        remind_at,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
    )


def _parse_schedule_update_input(
    raw: str,
) -> tuple[int, str, str, int | None, str | None, bool, int | None, int, str | None, bool] | None:
    parts = raw.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    schedule_id = _parse_positive_int(parts[0])
    if schedule_id is None:
        return None
    parsed = _parse_schedule_input(parts[1], default_duration_minutes=None)
    if parsed is None:
        return None
    (
        event_time,
        title,
        duration_minutes,
        remind_at,
        has_remind,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
        has_repeat_remind_start_time,
    ) = parsed
    if repeat_interval_minutes is None and repeat_times != 1:
        return None
    if repeat_interval_minutes is not None and repeat_times == 1:
        return None
    if has_repeat_remind_start_time and repeat_interval_minutes is None:
        return None
    return (
        schedule_id,
        event_time,
        title,
        duration_minutes,
        remind_at,
        has_remind,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
        has_repeat_remind_start_time,
    )


def _parse_schedule_input(
    raw: str,
    *,
    default_duration_minutes: int | None,
) -> tuple[str, str, int | None, str | None, bool, int | None, int, str | None, bool] | None:
    text = raw.strip()
    if not text:
        return None
    matched = SCHEDULE_EVENT_PREFIX_PATTERN.match(text)
    if matched is None:
        return None
    event_time = matched.group(1)
    if not _is_valid_datetime_text(event_time):
        return None

    working = matched.group(2).strip()
    if not working:
        return None

    duration_minutes: int | None = default_duration_minutes
    remind_at: str | None = None
    has_remind = False
    repeat_interval_minutes: int | None = None
    repeat_times = 1
    has_repeat_times = False
    repeat_remind_start_time: str | None = None
    has_repeat_remind_start_time = False

    interval_match = SCHEDULE_INTERVAL_OPTION_PATTERN.search(working)
    if interval_match:
        parsed_interval = _normalize_schedule_interval_minutes_value(interval_match.group(2))
        if parsed_interval is None:
            return None
        repeat_interval_minutes = parsed_interval
        working = _remove_option_span(working, interval_match.span())

    duration_match = SCHEDULE_DURATION_OPTION_PATTERN.search(working)
    if duration_match:
        parsed_duration = _normalize_schedule_duration_minutes_value(duration_match.group(2))
        if parsed_duration is None:
            return None
        duration_minutes = parsed_duration
        working = _remove_option_span(working, duration_match.span())

    remind_match = SCHEDULE_REMIND_OPTION_PATTERN.search(working)
    if remind_match:
        parsed_remind = _normalize_datetime_text(remind_match.group(2))
        if not parsed_remind:
            return None
        remind_at = parsed_remind
        has_remind = True
        working = _remove_option_span(working, remind_match.span())

    times_match = SCHEDULE_TIMES_OPTION_PATTERN.search(working)
    if times_match:
        parsed_times = _normalize_schedule_repeat_times_value(times_match.group(2))
        if parsed_times is None:
            return None
        repeat_times = parsed_times
        has_repeat_times = True
        working = _remove_option_span(working, times_match.span())

    remind_start_match = SCHEDULE_REMIND_START_OPTION_PATTERN.search(working)
    if remind_start_match:
        parsed_remind_start = _normalize_datetime_text(remind_start_match.group(2))
        if not parsed_remind_start:
            return None
        repeat_remind_start_time = parsed_remind_start
        has_repeat_remind_start_time = True
        working = _remove_option_span(working, remind_start_match.span())

    if repeat_interval_minutes is not None and not has_repeat_times:
        repeat_times = -1

    title = re.sub(r"\s+", " ", working).strip()
    if not title:
        return None
    if re.search(r"(^|\s)--(duration|interval|times|remind|remind-start)\b", title):
        return None
    return (
        event_time,
        title,
        duration_minutes,
        remind_at,
        has_remind,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
        has_repeat_remind_start_time,
    )


def _parse_schedule_view_input(raw: str) -> tuple[str, str | None] | None:
    text = raw.strip()
    if not text:
        return None
    parts = text.split(maxsplit=1)
    view_name = _normalize_schedule_view_value(parts[0])
    if view_name is None:
        return None
    if len(parts) == 1:
        return view_name, None
    anchor = _normalize_schedule_view_anchor(view_name=view_name, value=parts[1].strip())
    if anchor is None:
        return None
    return view_name, anchor


def _parse_schedule_repeat_toggle_input(raw: str) -> tuple[int, bool] | None:
    parts = raw.strip().split()
    if len(parts) != 2:
        return None
    schedule_id = _parse_positive_int(parts[0])
    if schedule_id is None:
        return None
    status = parts[1].strip().lower()
    if status == "on":
        return schedule_id, True
    if status == "off":
        return schedule_id, False
    return None


def _sanitize_tag(tag: str | None) -> str | None:
    if tag is None:
        return None
    normalized = tag.strip().lower()
    if not normalized:
        return None
    normalized = normalized.lstrip("#")
    if not normalized:
        return None
    return re.sub(r"\s+", "-", normalized)


def _normalize_todo_tag_value(value: Any) -> str | None:
    if value is None:
        return None
    return _sanitize_tag(str(value))


def _normalize_todo_view_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in TODO_VIEW_NAMES:
        return text
    return None


def _normalize_schedule_interval_minutes_value(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        return parsed if parsed >= 1 else None
    text = str(value).strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    if parsed < 1:
        return None
    return parsed


def _normalize_schedule_view_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in SCHEDULE_VIEW_NAMES:
        return text
    return None


def _normalize_schedule_repeat_times_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if value == -1:
            return -1
        return value if value >= 1 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        if parsed == -1:
            return -1
        return parsed if parsed >= 1 else None
    text = str(value).strip()
    if not re.fullmatch(r"-?\d+", text):
        return None
    parsed = int(text)
    if parsed == -1:
        return -1
    if parsed < 1:
        return None
    return parsed


def _normalize_schedule_duration_minutes_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 1 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        return parsed if parsed >= 1 else None
    text = str(value).strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    if parsed < 1:
        return None
    return parsed


def _normalize_schedule_view_anchor(*, view_name: str, value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if view_name in {"day", "week"}:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return parsed.strftime("%Y-%m-%d")
    if view_name == "month":
        try:
            parsed = datetime.strptime(text, "%Y-%m")
        except ValueError:
            return None
        return parsed.strftime("%Y-%m")
    return None


def _normalize_todo_priority_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
        return value if value >= 0 else None

    text = str(value).strip()
    if not re.fullmatch(r"-?\d+", text):
        return None
    parsed = int(text)
    if parsed < 0:
        return None
    return parsed


def _normalize_datetime_text(value: str) -> str | None:
    text = re.sub(r"\s+", " ", value).strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%d %H:%M")


def _filter_todos_by_view(todos: list[Any], *, view_name: str, now: datetime | None = None) -> list[Any]:
    if view_name == "all":
        return todos

    current = now or datetime.now()
    today = current.date()
    today_end = datetime.combine(today, datetime.max.time())
    upcoming_end = current + timedelta(days=7)

    filtered: list[Any] = []
    for item in todos:
        if item.done:
            continue
        due_at = _parse_due_datetime(item.due_at)

        if view_name == "today":
            if due_at is not None and due_at.date() == today:
                filtered.append(item)
            continue

        if view_name == "overdue":
            if due_at is not None and due_at < current:
                filtered.append(item)
            continue

        if view_name == "upcoming":
            if due_at is not None and today_end < due_at <= upcoming_end:
                filtered.append(item)
            continue

        if view_name == "inbox":
            if due_at is None:
                filtered.append(item)
            continue

    return filtered


def _filter_schedules_by_calendar_view(
    schedules: list[Any],
    *,
    view_name: str,
    anchor: str | None,
    now: datetime | None = None,
) -> list[Any]:
    if view_name not in SCHEDULE_VIEW_NAMES:
        return schedules

    start, end = _resolve_schedule_view_window(view_name=view_name, anchor=anchor, now=now)

    filtered: list[Any] = []
    for item in schedules:
        event_time = _parse_due_datetime(item.event_time)
        if event_time is None:
            continue
        if start <= event_time < end:
            filtered.append(item)
    return filtered


def _parse_due_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _default_schedule_list_window(
    now: datetime | None = None,
    *,
    window_days: int = DEFAULT_SCHEDULE_MAX_WINDOW_DAYS,
) -> tuple[datetime, datetime]:
    current = now or datetime.now()
    start = datetime.combine(current.date() - timedelta(days=2), datetime.min.time())
    end = start + timedelta(days=max(window_days, 1))
    return start, end


def _resolve_schedule_view_window(
    *,
    view_name: str,
    anchor: str | None,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    current = now or datetime.now()
    if anchor:
        if view_name == "month":
            anchor_time = datetime.strptime(anchor, "%Y-%m")
        else:
            anchor_time = datetime.strptime(anchor, "%Y-%m-%d")
    else:
        anchor_time = current

    if view_name == "day":
        start = datetime.combine(anchor_time.date(), datetime.min.time())
        end = start + timedelta(days=1)
        return start, end
    if view_name == "week":
        week_start_date = anchor_time.date() - timedelta(days=anchor_time.weekday())
        start = datetime.combine(week_start_date, datetime.min.time())
        end = start + timedelta(days=7)
        return start, end
    month_start = datetime(anchor_time.year, anchor_time.month, 1)
    if anchor_time.month == 12:
        month_end = datetime(anchor_time.year + 1, 1, 1)
    else:
        month_end = datetime(anchor_time.year, anchor_time.month + 1, 1)
    return month_start, month_end


def _build_schedule_event_times(
    *,
    event_time: str,
    repeat_interval_minutes: int | None,
    repeat_times: int,
    infinite_repeat_conflict_preview_days: int = DEFAULT_INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS,
) -> list[str]:
    base = datetime.strptime(event_time, "%Y-%m-%d %H:%M")
    if repeat_interval_minutes is None:
        return [base.strftime("%Y-%m-%d %H:%M")]

    if repeat_times == -1:
        # Keep conflict checks deterministic with a time-bounded preview window.
        preview_minutes = max(infinite_repeat_conflict_preview_days, 1) * 24 * 60
        preview_times = preview_minutes // repeat_interval_minutes + 1
    else:
        preview_times = max(repeat_times, 1)

    result: list[str] = []
    current = base
    for _ in range(preview_times):
        result.append(current.strftime("%Y-%m-%d %H:%M"))
        current += timedelta(minutes=repeat_interval_minutes)
    return result


def _now_time_text() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _is_valid_datetime_text(value: str) -> bool:
    return _normalize_datetime_text(value) is not None


def _remove_option_span(text: str, span: tuple[int, int]) -> str:
    start, end = span
    return (text[:start] + " " + text[end:]).strip()


def _format_todo_meta_inline(due_at: str | None, remind_at: str | None, *, priority: int | None = None) -> str:
    meta_parts: list[str] = []
    if priority is not None:
        meta_parts.append(f"优先级:{priority}")
    if due_at:
        meta_parts.append(f"截止:{due_at}")
    if remind_at:
        meta_parts.append(f"提醒:{remind_at}")
    if not meta_parts:
        return ""
    return " | " + " ".join(meta_parts)


def _format_schedule_remind_meta_inline(
    *,
    remind_at: str | None,
    repeat_remind_start_time: str | None,
) -> str:
    meta_parts: list[str] = []
    if remind_at:
        meta_parts.append(f"提醒:{remind_at}")
    if repeat_remind_start_time:
        meta_parts.append(f"重复提醒开始:{repeat_remind_start_time}")
    if not meta_parts:
        return ""
    return " | " + " ".join(meta_parts)


def _repeat_enabled_text(value: bool | None) -> str:
    if value is None:
        return "-"
    return "on" if value else "off"


def _format_schedule_conflicts(conflicts: list[Any]) -> str:
    lines = ["日程冲突：以下时间段与现有日程重叠，请调整时间后重试。"]
    for item in conflicts[:5]:
        lines.append(f"- #{item.id} {item.event_time} {item.title}")
    if len(conflicts) > 5:
        lines.append(f"- ... 还有 {len(conflicts) - 5} 条冲突")
    return "\n".join(lines)


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    header_line = "| " + " | ".join(_table_cell_text(item) for item in headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(_table_cell_text(item) for item in row) + " |" for row in rows]
    return "\n".join([header_line, separator, *body])


def _table_cell_text(value: str) -> str:
    # Keep table layout stable even if content contains separators or line breaks.
    return value.replace("|", "｜").replace("\n", " ").strip()


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)


def _try_parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None

    cleaned = text.strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return None


def _normalize_plan_items(payload: dict[str, Any]) -> list[str]:
    raw_plan = payload.get("plan")
    plan_items: list[str] = []
    if isinstance(raw_plan, list):
        for item in raw_plan:
            text = str(item).strip()
            if text:
                plan_items.append(text)
    return plan_items


def _normalize_plan_decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = str(payload.get("status") or "").strip().lower()
    plan_items = _normalize_plan_items(payload)
    if status == "planned":
        if not plan_items:
            return None
        return {"status": "planned", "plan": plan_items}
    return None


def _normalize_thought_decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = str(payload.get("status") or "").strip().lower()
    current_step = str(payload.get("current_step") or "").strip()
    if not current_step:
        plan_items = _normalize_plan_items(payload)
        if plan_items:
            current_step = plan_items[0]

    if status == "continue":
        next_action = payload.get("next_action")
        if not isinstance(next_action, dict):
            return None
        tool = str(next_action.get("tool") or "").strip().lower()
        input_text = str(next_action.get("input") or "").strip()
        if tool not in {"todo", "schedule", "internet_search", "ask_user"}:
            return None
        if not input_text:
            return None
        response_text = str(payload.get("response") or "").strip()
        if response_text:
            return None
        if tool == "ask_user":
            return {
                "status": "ask_user",
                "current_step": current_step,
                "next_action": None,
                "question": input_text,
                "response": None,
            }
        return {
            "status": "continue",
            "current_step": current_step,
            "next_action": {"tool": tool, "input": input_text},
            "question": None,
            "response": None,
        }

    if status == "step_done":
        return {
            "status": "step_done",
            "current_step": current_step,
            "next_action": None,
            "question": None,
            "response": str(payload.get("response") or "").strip() or None,
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
        response_text = str(payload.get("response") or "").strip()
        if not response_text:
            return None
        return {
            "status": "done",
            "current_step": current_step,
            "next_action": None,
            "question": None,
            "response": response_text,
        }
    return None


def _normalize_replan_decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    status = str(payload.get("status") or "").strip().lower()
    plan_items = _normalize_plan_items(payload)
    if status == "replanned":
        if not plan_items:
            return None
        return {"status": "replanned", "plan": plan_items}
    return None


def _truncate_text(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)] + "..."


def _is_same_question_text(previous: str | None, current: str) -> bool:
    if previous is None:
        return False
    a = _normalize_question_text(previous)
    b = _normalize_question_text(current)
    if not a or not b:
        return False
    if a == b:
        return True
    return a in b or b in a


def _normalize_question_text(text: str) -> str:
    normalized = text.strip().lower()
    normalized = re.sub(r"[\s，。！？；：、,.!?;:]+", "", normalized)
    return normalized


def _format_search_results(results: list[SearchResult]) -> str:
    lines = ["互联网搜索结果（Top 3）:"]
    for index, item in enumerate(results[:3], start=1):
        snippet = item.snippet or "-"
        lines.append(f"{index}. {item.title}")
        lines.append(f"   摘要: {snippet}")
        lines.append(f"   链接: {item.url}")
    return "\n".join(lines)
