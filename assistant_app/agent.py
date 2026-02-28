from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from assistant_app.db import AssistantDB, ChatTurn
from assistant_app.llm import LLMClient
from assistant_app.planner_plan_replan import (
    PLAN_ONCE_PROMPT,
    REPLAN_PROMPT,
    normalize_plan_decision,
    normalize_replan_decision,
)
from assistant_app.planner_thought import (
    THOUGHT_PROMPT,
    THOUGHT_TOOL_SCHEMAS,
    normalize_thought_decision,
    normalize_thought_tool_call,
)
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
HISTORY_LIMIT_OPTION_PATTERN = re.compile(r"(^|\s)--limit\s+(\d+)")
TODO_VIEW_NAMES = ("all", "today", "overdue", "upcoming", "inbox")
SCHEDULE_VIEW_NAMES = ("day", "week", "month")
TODO_TABLE_HEADERS = ["ID", "状态", "标签", "优先级", "内容", "创建时间", "完成时间", "截止时间", "提醒时间"]
DEFAULT_PLAN_REPLAN_MAX_STEPS = 20
DEFAULT_PLAN_REPLAN_RETRY_COUNT = 2
DEFAULT_PLAN_OBSERVATION_CHAR_LIMIT = 10000
DEFAULT_PLAN_OBSERVATION_HISTORY_LIMIT = 100
DEFAULT_PLAN_CONTINUOUS_FAILURE_LIMIT = 2
DEFAULT_TASK_CANCEL_COMMAND = "取消当前任务"
DEFAULT_INTERNET_SEARCH_TOP_K = 3
DEFAULT_SCHEDULE_MAX_WINDOW_DAYS = 31
UNKNOWN_APP_VERSION = "unknown"
DEFAULT_HISTORY_LIST_LIMIT = 20
MAX_HISTORY_LIST_LIMIT = 200
PLAN_HISTORY_LOOKBACK_HOURS = 24
PLAN_HISTORY_MAX_TURNS = 50
DEFAULT_USER_PROFILE_MAX_CHARS = 6000
PROJECT_ROOT = Path(__file__).resolve().parent.parent

class TaskInterruptedError(RuntimeError):
    """Raised when an in-flight planner task is interrupted by a newer input."""


class ThoughtToolCallingError(RuntimeError):
    """Raised when thought tool-calling cannot proceed."""


@dataclass
class PlannerObservation:
    tool: str
    input_text: str
    ok: bool
    result: str


@dataclass
class CompletedSubtask:
    item: str
    result: str


@dataclass
class ClarificationTurn:
    role: str
    content: str


@dataclass
class PlanStep:
    item: str
    completed: bool = False


@dataclass
class OuterPlanContext:
    goal: str
    clarification_history: list[ClarificationTurn] = field(default_factory=list)
    latest_plan: list[PlanStep] = field(default_factory=list)
    current_plan_index: int = 0
    completed_subtasks: list[CompletedSubtask] = field(default_factory=list)
    outer_messages: list[dict[str, str]] | None = None


@dataclass
class InnerReActContext:
    current_subtask: str = ""
    completed_subtasks: list[CompletedSubtask] = field(default_factory=list)
    observations: list[PlannerObservation] = field(default_factory=list)
    thought_messages: list[dict[str, Any]] = field(default_factory=list)
    response: str | None = None


@dataclass
class PendingPlanTask:
    goal: str
    outer_context: OuterPlanContext | None = None
    inner_context: InnerReActContext | None = None
    observations: list[PlannerObservation] = field(default_factory=list)
    step_count: int = 0
    plan_initialized: bool = False
    awaiting_clarification: bool = False
    # True means outer loop should run replan before next thought cycle.
    needs_replan: bool = False
    planner_failure_rounds: int = 0
    last_ask_user_question: str | None = None
    last_ask_user_clarification_len: int = 0
    ask_user_repeat_count: int = 0
    post_plan_done_count: int = 0
    successful_steps: int = 0
    failed_steps: int = 0
    plan_goal_notified: bool = False
    last_reported_plan_signature: tuple[tuple[str, bool], ...] | None = None
    last_notified_completed_subtask_count: int = 0


class AssistantAgent:
    def __init__(
        self,
        db: AssistantDB,
        llm_client: LLMClient | None = None,
        search_provider: SearchProvider | None = None,
        llm_trace_logger: logging.Logger | None = None,
        app_logger: logging.Logger | None = None,
        progress_callback: Callable[[str], None] | None = None,
        plan_replan_max_steps: int = DEFAULT_PLAN_REPLAN_MAX_STEPS,
        plan_replan_retry_count: int = DEFAULT_PLAN_REPLAN_RETRY_COUNT,
        plan_observation_char_limit: int = DEFAULT_PLAN_OBSERVATION_CHAR_LIMIT,
        plan_observation_history_limit: int = DEFAULT_PLAN_OBSERVATION_HISTORY_LIMIT,
        plan_continuous_failure_limit: int = DEFAULT_PLAN_CONTINUOUS_FAILURE_LIMIT,
        task_cancel_command: str = DEFAULT_TASK_CANCEL_COMMAND,
        internet_search_top_k: int = DEFAULT_INTERNET_SEARCH_TOP_K,
        schedule_max_window_days: int = DEFAULT_SCHEDULE_MAX_WINDOW_DAYS,
        user_profile_path: str = "",
        user_profile_max_chars: int = DEFAULT_USER_PROFILE_MAX_CHARS,
        user_profile_refresh_runner: Callable[[], str] | None = None,
        final_response_rewriter: Callable[[str], str] | None = None,
        app_version: str = UNKNOWN_APP_VERSION,
    ) -> None:
        self.db = db
        self.llm_client = llm_client
        self.search_provider = search_provider or BingSearchProvider()
        self._llm_trace_logger = llm_trace_logger or logging.getLogger("assistant_app.llm_trace")
        self._llm_trace_logger.propagate = False
        if not self._llm_trace_logger.handlers:
            self._llm_trace_logger.addHandler(logging.NullHandler())
        self._app_logger = app_logger or logging.getLogger("assistant_app.app")
        self._app_logger.propagate = False
        if not self._app_logger.handlers:
            self._app_logger.addHandler(logging.NullHandler())
        self._llm_trace_call_seq = 0
        self._pending_plan_task: PendingPlanTask | None = None
        self._progress_callback = progress_callback
        self._last_task_completed = False
        self._interrupt_lock = threading.Lock()
        self._interrupt_requested = False
        self._plan_replan_max_steps = max(plan_replan_max_steps, 1)
        self._plan_replan_retry_count = max(plan_replan_retry_count, 0)
        self._plan_observation_char_limit = max(plan_observation_char_limit, 1)
        self._plan_observation_history_limit = max(plan_observation_history_limit, 1)
        self._plan_continuous_failure_limit = max(plan_continuous_failure_limit, 1)
        self._task_cancel_command = task_cancel_command.strip() or DEFAULT_TASK_CANCEL_COMMAND
        self._internet_search_top_k = max(internet_search_top_k, 1)
        self._schedule_max_window_days = max(schedule_max_window_days, 1)
        self._user_profile_max_chars = max(user_profile_max_chars, 1)
        self._user_profile_path, self._user_profile_content = self._load_user_profile(user_profile_path)
        self._user_profile_refresh_runner = user_profile_refresh_runner
        self._final_response_rewriter = final_response_rewriter
        self._app_version = app_version.strip() or UNKNOWN_APP_VERSION
        self._subtask_result_callback: Callable[[str], None] | None = None

    def set_progress_callback(self, callback: Callable[[str], None] | None) -> None:
        self._progress_callback = callback

    def set_user_profile_refresh_runner(self, runner: Callable[[], str] | None) -> None:
        self._user_profile_refresh_runner = runner

    def set_subtask_result_callback(self, callback: Callable[[str], None] | None) -> None:
        self._subtask_result_callback = callback

    @staticmethod
    def _outer_context(task: PendingPlanTask) -> OuterPlanContext:
        if task.outer_context is None:
            task.outer_context = OuterPlanContext(goal=task.goal)
        return task.outer_context

    def _new_inner_context(self, task: PendingPlanTask) -> InnerReActContext:
        outer = self._outer_context(task)
        outer_messages = self._ensure_outer_messages(task)
        return InnerReActContext(
            current_subtask=self._current_plan_item_text(task),
            completed_subtasks=[
                CompletedSubtask(item=item.item, result=item.result) for item in outer.completed_subtasks
            ],
            observations=[],
            thought_messages=[{"role": "system", "content": THOUGHT_PROMPT}, *deepcopy(outer_messages)],
            response=None,
        )

    def _ensure_inner_context(self, task: PendingPlanTask) -> InnerReActContext:
        if task.inner_context is None:
            task.inner_context = self._new_inner_context(task)
        assert task.inner_context is not None
        return task.inner_context

    def handle_input(self, user_input: str) -> str:
        text = user_input.strip()
        if not text:
            return "请输入内容。输入 /help 查看可用命令。"
        self._last_task_completed = False
        self._clear_interrupt_request()
        response = self._handle_input_text(text)
        if not text.startswith("/"):
            self._save_turn_history(user_text=text, assistant_text=response)
        return response

    def handle_input_with_task_status(self, user_input: str) -> tuple[str, bool]:
        response = self.handle_input(user_input)
        return response, self._last_task_completed

    def interrupt_current_task(self) -> None:
        with self._interrupt_lock:
            self._interrupt_requested = True
        self._pending_plan_task = None

    def _handle_input_text(self, text: str) -> str:
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
            outer = self._outer_context(pending_task)
            outer.clarification_history.append(ClarificationTurn(role="user_answer", content=text))
            pending_task.awaiting_clarification = False
            pending_task.needs_replan = True
            return self._run_outer_plan_loop(task=pending_task)

        task = PendingPlanTask(goal=text)
        return self._run_outer_plan_loop(task=task)

    def _save_turn_history(self, *, user_text: str, assistant_text: str) -> None:
        try:
            self.db.save_turn(user_content=user_text, assistant_content=assistant_text)
        except Exception:
            self._app_logger.warning(
                "failed to save chat history",
                extra={"event": "chat_history_save_failed"},
                exc_info=True,
            )

    def _run_outer_plan_loop(self, task: PendingPlanTask) -> str:
        try:
            while True:
                self._raise_if_task_interrupted()
                if task.step_count >= self._plan_replan_max_steps:
                    return self._finalize_planner_task(task, self._format_step_limit_response(task))

                self._emit_decision_progress(task)

                if not task.plan_initialized:
                    if not self._initialize_plan_once(task):
                        return self._finalize_planner_task(task, self._planner_unavailable_text())

                if task.awaiting_clarification:
                    self._pending_plan_task = task
                    return "请确认：请补充必要信息。"

                replan_outcome, replan_response = self._run_replan_gate(task)
                if replan_outcome == "retry":
                    continue
                if replan_outcome == "unavailable":
                    return self._finalize_planner_task(task, self._planner_unavailable_text())
                if replan_outcome == "done":
                    final_response = replan_response or self._planner_unavailable_text()
                    final_response = self._rewrite_final_response(final_response)
                    return self._finalize_planner_task(task, final_response)

                task.inner_context = self._new_inner_context(task)
                loop_outcome, payload = self._run_inner_react_loop(task)
                if loop_outcome == "replan":
                    continue
                if loop_outcome == "ask_user":
                    self._pending_plan_task = task
                    return payload or "请确认：请补充必要信息。"
                if loop_outcome == "done_candidate":
                    task.needs_replan = True
                    continue
                if loop_outcome == "step_limit":
                    return self._finalize_planner_task(task, self._format_step_limit_response(task))
                return self._finalize_planner_task(task, self._planner_unavailable_text())
        except TaskInterruptedError:
            return self._finalize_interrupted_task(task)
        except ThoughtToolCallingError as exc:
            return self._finalize_planner_task(task, str(exc))

    def _emit_decision_progress(self, task: PendingPlanTask) -> None:
        planned_total_text = self._progress_total_text(task)
        current_plan_total = self._current_plan_total_text(task)
        plan_suffix = f"（当前计划 {current_plan_total} 步）" if current_plan_total is not None else ""
        progress_text = (
            f"步骤进度：已执行 {task.step_count}/{planned_total_text}，"
            f"开始第 {task.step_count + 1} 步决策。{plan_suffix}"
        )
        self._emit_progress(progress_text)

    def _initialize_plan_once(self, task: PendingPlanTask) -> bool:
        outer = self._outer_context(task)
        plan_payload = self._request_plan_payload(task)
        if plan_payload is None:
            return False
        plan_decision = plan_payload.get("decision")
        if not isinstance(plan_decision, dict):
            return False
        expanded_goal = str(plan_decision.get("goal") or "").strip()
        if expanded_goal:
            outer.goal = expanded_goal
            task.goal = expanded_goal
            self._notify_plan_goal_result(task, expanded_goal)
        self._append_planner_decision_observation(task, phase="plan", decision=plan_decision)
        outer.latest_plan = [
            PlanStep(item=plan_item, completed=False)
            for plan_item in [str(item).strip() for item in plan_decision.get("plan", []) if str(item).strip()]
        ]
        task.plan_initialized = True
        outer.current_plan_index = 0
        self._emit_progress(f"规划完成：共 {len(outer.latest_plan)} 步。")
        self._emit_plan_progress(task)
        return True

    def _run_replan_gate(self, task: PendingPlanTask) -> tuple[str, str | None]:
        outer = self._outer_context(task)
        if not task.needs_replan:
            return "skipped", None

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
                return "unavailable", None
            self._emit_progress("重规划失败：模型输出不符合契约，准备重试。")
            return "retry", None

        task.planner_failure_rounds = 0
        replan_decision = replan_payload.get("decision")
        if not isinstance(replan_decision, dict):
            return "unavailable", None
        self._append_planner_decision_observation(task, phase="replan", decision=replan_decision)
        status = str(replan_decision.get("status") or "").strip().lower()
        if status == "done":
            response = str(replan_decision.get("response") or "").strip()
            task.needs_replan = False
            return "done", response or None
        raw_plan = replan_decision.get("plan")
        if not isinstance(raw_plan, list):
            return "unavailable", None
        updated_plan: list[PlanStep] = []
        for step in raw_plan:
            if not isinstance(step, dict):
                return "unavailable", None
            item = str(step.get("task") or "").strip()
            completed = step.get("completed")
            if not item or not isinstance(completed, bool):
                return "unavailable", None
            updated_plan.append(PlanStep(item=item, completed=completed))
        outer.latest_plan = updated_plan
        if not outer.latest_plan:
            return "unavailable", None
        outer.current_plan_index = 0
        self._sync_current_plan_index(outer)
        task.needs_replan = False
        self._emit_progress(f"重规划完成：共 {len(outer.latest_plan)} 步。")
        self._emit_plan_progress(task)
        self._notify_replan_continue_subtask_result(task)
        return "ok", None

    def _run_inner_react_loop(self, task: PendingPlanTask) -> tuple[str, str | None]:
        outer = self._outer_context(task)
        emit_progress = False
        while True:
            self._raise_if_task_interrupted()
            if task.step_count >= self._plan_replan_max_steps:
                return "step_limit", None

            if emit_progress:
                self._emit_decision_progress(task)
            emit_progress = True

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
                    return "unavailable", None
                self._emit_progress("思考失败：模型输出不符合契约，准备重试。")
                continue

            thought_decision = thought_payload.get("decision")
            if not isinstance(thought_decision, dict):
                return "unavailable", None

            status = str(thought_decision.get("status") or "").strip().lower()
            current_step = str(thought_decision.get("current_step") or "").strip()
            if status == "done":
                response_text = str(thought_decision.get("response") or "").strip()
                if not response_text:
                    task.planner_failure_rounds += 1
                    self._append_observation(
                        task,
                        PlannerObservation(
                            tool="thought",
                            input_text=current_step or "done",
                            ok=False,
                            result="status=done 但 response 为空，准备重试。",
                        ),
                    )
                    if task.planner_failure_rounds >= self._plan_continuous_failure_limit:
                        return "unavailable", None
                    self._emit_progress("思考失败：done 缺少 response，准备重试。")
                    continue
            task.planner_failure_rounds = 0
            self._append_planner_decision_observation(task, phase="thought", decision=thought_decision)
            self._emit_progress(f"思考决策：{status} | {current_step or '（未提供步骤）'}")

            if status == "done":
                response = str(thought_decision.get("response") or "").strip()
                inner_context = self._ensure_inner_context(task)
                inner_context.response = response
                completed_item = self._current_plan_item_text(task) or current_step or "当前子任务"
                latest_success_result = self._latest_success_observation_result(task)
                completed_result = self._merge_summary_with_detail(
                    summary=response,
                    detail=latest_success_result,
                )
                if not completed_result:
                    completed_result = "子任务已完成。"
                self._append_completed_subtask(
                    task,
                    item=completed_item,
                    result=completed_result,
                )
                # done means current subtask is completed; advance plan cursor before replan.
                if outer.latest_plan:
                    if 0 <= outer.current_plan_index < len(outer.latest_plan):
                        outer.latest_plan[outer.current_plan_index].completed = True
                    outer.current_plan_index = min(outer.current_plan_index + 1, len(outer.latest_plan))
                    self._sync_current_plan_index(outer)
                task.post_plan_done_count = 0
                task.needs_replan = True
                return "replan", None

            if status == "ask_user":
                task.post_plan_done_count = 0
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
                    and len(outer.clarification_history) > task.last_ask_user_clarification_len
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
                        return (
                            "done_candidate",
                            "我已经拿到你的补充信息，但仍无法完成重规划。请直接使用 /todo 或 /schedule 命令。",
                        )
                    continue
                ask_turns = sum(1 for turn in outer.clarification_history if turn.role == "assistant_question")
                if ask_turns >= 6:
                    return "done_candidate", "澄清次数过多，我仍无法稳定重规划。请直接使用 /todo 或 /schedule 命令。"
                task.ask_user_repeat_count = 0
                task.last_ask_user_question = question
                task.last_ask_user_clarification_len = len(outer.clarification_history)
                outer.clarification_history.append(
                    ClarificationTurn(role="assistant_question", content=question)
                )
                task.awaiting_clarification = True
                self._emit_progress(f"步骤动作：ask_user -> {question}")
                return "ask_user", f"请确认：{question}"

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
            task.post_plan_done_count = 0
            action_tool = str(next_action.get("tool") or "").strip().lower()
            action_input = str(next_action.get("input") or "").strip()
            tool_call_id = str(thought_payload.get("tool_call_id") or "").strip() or None
            self._emit_progress(f"步骤动作：{action_tool} -> {action_input}")
            self._raise_if_task_interrupted()
            task.step_count += 1
            observation = self._execute_planner_tool(action_tool=action_tool, action_input=action_input)
            normalized_observation = self._append_observation(task, observation)
            if tool_call_id:
                self._append_thought_tool_result_message(
                    task,
                    observation=normalized_observation,
                    tool_call_id=tool_call_id,
                )
            else:
                self._append_thought_observation_message(task, normalized_observation)
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
        payload = self._request_payload_with_retry(planner_messages, normalize_plan_decision)
        if payload is None:
            return None
        decision = payload.get("decision")
        raw_response = payload.get("raw_response")
        raw_user_message = planner_messages[-1].get("content")
        if isinstance(decision, dict) and isinstance(raw_user_message, str) and isinstance(raw_response, str):
            self._append_outer_message_turn(
                task=task,
                user_message_content=raw_user_message,
                assistant_response=raw_response,
            )
        return payload

    def _request_thought_payload(self, task: PendingPlanTask) -> dict[str, Any] | None:
        if not self.llm_client:
            return None

        planner_messages = self._ensure_thought_messages(task)
        context_payload = self._build_thought_context(task)
        context_payload["phase"] = "thought"
        context_payload["current_plan_item"] = self._current_plan_item_text(task)
        planner_messages.append({"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)})
        request_messages = deepcopy(planner_messages)
        payload = self._request_thought_payload_with_retry(request_messages)
        if payload is None:
            return None
        decision = payload.get("decision")
        assistant_message = payload.get("assistant_message")
        if isinstance(assistant_message, dict):
            self._append_thought_assistant_message(task, assistant_message)
        elif isinstance(decision, dict):
            # Backward-compatible path for clients without tool-calling support.
            self._append_thought_decision_message(task, decision)
        return payload

    def _request_replan_payload(self, task: PendingPlanTask) -> dict[str, Any] | None:
        if not self.llm_client:
            return None

        planner_messages = self._build_replan_messages(task)
        payload = self._request_payload_with_retry(planner_messages, normalize_replan_decision)
        if payload is None:
            return None
        decision = payload.get("decision")
        raw_response = payload.get("raw_response")
        raw_user_message = planner_messages[-1].get("content")
        if isinstance(decision, dict) and isinstance(raw_user_message, str) and isinstance(raw_response, str):
            self._append_outer_message_turn(
                task=task,
                user_message_content=raw_user_message,
                assistant_response=raw_response,
            )
        return payload

    def _request_payload_with_retry(
        self,
        messages: list[dict[str, Any]],
        normalizer: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any] | None:
        max_attempts = 1 + self._plan_replan_retry_count
        phase = self._llm_trace_phase(messages)
        for attempt in range(1, max_attempts + 1):
            self._raise_if_task_interrupted()
            call_id = self._next_llm_trace_call_id()
            self._log_llm_trace_event(
                {
                    "event": "llm_request",
                    "call_id": call_id,
                    "phase": phase,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "messages": messages,
                }
            )
            try:
                raw = self._llm_reply_for_planner(messages)
            except Exception as exc:
                self._log_llm_trace_event(
                    {
                        "event": "llm_response_error",
                        "call_id": call_id,
                        "phase": phase,
                        "attempt": attempt,
                        "error": repr(exc),
                    }
                )
                continue
            self._raise_if_task_interrupted()
            self._log_llm_trace_event(
                {
                    "event": "llm_response",
                    "call_id": call_id,
                    "phase": phase,
                    "attempt": attempt,
                    "response": raw,
                }
            )
            payload = _try_parse_json(_strip_think_blocks(raw).strip())
            if not isinstance(payload, dict):
                continue

            decision = normalizer(payload)
            if decision is not None:
                return {"decision": decision, "raw_response": raw}
        return None

    def _request_thought_payload_with_retry(self, messages: list[dict[str, Any]]) -> dict[str, Any] | None:
        max_attempts = 1 + self._plan_replan_retry_count
        phase = self._llm_trace_phase(messages)
        for attempt in range(1, max_attempts + 1):
            self._raise_if_task_interrupted()
            call_id = self._next_llm_trace_call_id()
            self._log_llm_trace_event(
                {
                    "event": "llm_request",
                    "call_id": call_id,
                    "phase": phase,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "messages": messages,
                    "tools": THOUGHT_TOOL_SCHEMAS,
                }
            )
            try:
                response = self._llm_reply_for_thought(messages)
            except ThoughtToolCallingError:
                raise
            except Exception as exc:
                self._log_llm_trace_event(
                    {
                        "event": "llm_response_error",
                        "call_id": call_id,
                        "phase": phase,
                        "attempt": attempt,
                        "error": repr(exc),
                    }
                )
                continue

            self._raise_if_task_interrupted()
            self._log_llm_trace_event(
                {
                    "event": "llm_response",
                    "call_id": call_id,
                    "phase": phase,
                    "attempt": attempt,
                    "response": response,
                }
            )

            decision = response.get("decision")
            if not isinstance(decision, dict):
                continue

            payload: dict[str, Any] = {"decision": decision}
            assistant_message = response.get("assistant_message")
            if isinstance(assistant_message, dict):
                payload["assistant_message"] = assistant_message
            tool_call_id = response.get("tool_call_id")
            if isinstance(tool_call_id, str) and tool_call_id.strip():
                payload["tool_call_id"] = tool_call_id.strip()
            return payload
        return None

    def _build_plan_messages(self, task: PendingPlanTask) -> list[dict[str, str]]:
        outer_messages = deepcopy(self._ensure_outer_messages(task))
        context_payload = self._build_planner_context(task)
        context_payload["phase"] = "plan"
        messages: list[dict[str, str]] = [{"role": "system", "content": PLAN_ONCE_PROMPT}]
        messages.extend(outer_messages)
        messages.append({"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)})
        return messages

    def _build_thought_messages(self, task: PendingPlanTask) -> list[dict[str, Any]]:
        outer_messages = deepcopy(self._ensure_outer_messages(task))
        context_payload = self._build_thought_context(task)
        context_payload["phase"] = "thought"
        context_payload["current_plan_item"] = self._current_plan_item_text(task)
        messages: list[dict[str, Any]] = [{"role": "system", "content": THOUGHT_PROMPT}]
        messages.extend(outer_messages)
        messages.append({"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)})
        return messages

    def _ensure_thought_messages(self, task: PendingPlanTask) -> list[dict[str, Any]]:
        inner = self._ensure_inner_context(task)
        if inner.thought_messages:
            return inner.thought_messages
        outer_messages = deepcopy(self._ensure_outer_messages(task))
        inner.thought_messages = [{"role": "system", "content": THOUGHT_PROMPT}, *outer_messages]
        return inner.thought_messages

    def _append_thought_assistant_message(self, task: PendingPlanTask, assistant_message: dict[str, Any]) -> None:
        messages = self._ensure_thought_messages(task)
        payload: dict[str, Any] = {"role": "assistant"}
        content = assistant_message.get("content")
        if content is None or isinstance(content, str):
            payload["content"] = content
        else:
            payload["content"] = str(content)
        tool_calls = assistant_message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            payload["tool_calls"] = deepcopy(tool_calls)
        messages.append(payload)

    def _append_thought_decision_message(self, task: PendingPlanTask, decision: dict[str, Any]) -> None:
        messages = self._ensure_thought_messages(task)
        decision_payload = {
            "phase": "thought_decision",
            "decision": decision,
        }
        messages.append({"role": "assistant", "content": json.dumps(decision_payload, ensure_ascii=False)})

    def _append_thought_tool_result_message(
        self,
        task: PendingPlanTask,
        *,
        observation: PlannerObservation,
        tool_call_id: str,
    ) -> None:
        messages = self._ensure_thought_messages(task)
        tool_payload = {
            "tool": observation.tool,
            "input": observation.input_text,
            "ok": observation.ok,
            "result": observation.result,
        }
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": json.dumps(tool_payload, ensure_ascii=False),
            }
        )

    def _append_thought_observation_message(self, task: PendingPlanTask, observation: PlannerObservation) -> None:
        messages = self._ensure_thought_messages(task)
        observation_payload = {
            "phase": "thought_observation",
            "observation": {
                "tool": observation.tool,
                "input": observation.input_text,
                "ok": observation.ok,
                "result": observation.result,
            },
        }
        messages.append({"role": "user", "content": json.dumps(observation_payload, ensure_ascii=False)})

    def _build_replan_messages(self, task: PendingPlanTask) -> list[dict[str, str]]:
        outer_messages = deepcopy(self._ensure_outer_messages(task))
        context_payload = self._build_planner_context(task)
        context_payload["phase"] = "replan"
        context_payload["current_plan_item"] = self._current_plan_item_text(task)
        messages: list[dict[str, str]] = [{"role": "system", "content": REPLAN_PROMPT}]
        messages.extend(outer_messages)
        messages.append({"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)})
        return messages

    def _ensure_outer_messages(self, task: PendingPlanTask) -> list[dict[str, str]]:
        outer = self._outer_context(task)
        if outer.outer_messages is not None:
            return outer.outer_messages
        recent_chat_turns = self.db.recent_turns_for_planner(
            lookback_hours=PLAN_HISTORY_LOOKBACK_HOURS,
            limit=PLAN_HISTORY_MAX_TURNS,
        )
        outer.outer_messages = self._serialize_chat_turns_as_messages(recent_chat_turns)
        return outer.outer_messages

    def _append_outer_message_turn(
        self,
        *,
        task: PendingPlanTask,
        user_message_content: str,
        assistant_response: str,
    ) -> None:
        outer_messages = self._ensure_outer_messages(task)
        outer_messages.append({"role": "user", "content": user_message_content})
        outer_messages.append({"role": "assistant", "content": assistant_response})

    def _build_planner_context(self, task: PendingPlanTask) -> dict[str, Any]:
        outer = self._outer_context(task)
        completed_subtasks = self._serialize_completed_subtasks(outer.completed_subtasks)
        context_payload = {
            "goal": outer.goal,
            "clarification_history": self._serialize_clarification_history(outer.clarification_history),
            "step_count": task.step_count,
            "max_steps": self._plan_replan_max_steps,
            "latest_plan": self._serialize_latest_plan(outer.latest_plan),
            "current_plan_index": outer.current_plan_index,
            "completed_subtasks": completed_subtasks,
            "user_profile": self._serialize_user_profile(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        return context_payload

    def _build_thought_context(self, task: PendingPlanTask) -> dict[str, Any]:
        outer = self._outer_context(task)
        inner = self._ensure_inner_context(task)
        current_subtask_observations = self._serialize_observations(
            inner.observations[-self._plan_observation_history_limit :]
        )
        completed_subtasks = self._serialize_completed_subtasks(inner.completed_subtasks)
        current_subtask: dict[str, Any] = {
            "item": inner.current_subtask,
            "index": outer.current_plan_index + 1,
            "total": len(outer.latest_plan),
        }
        if not inner.current_subtask:
            current_subtask["index"] = None
            current_subtask["total"] = None
        return {
            "clarification_history": self._serialize_clarification_history(outer.clarification_history),
            "step_count": task.step_count,
            "max_steps": self._plan_replan_max_steps,
            "current_subtask": current_subtask,
            "completed_subtasks": completed_subtasks,
            "current_subtask_observations": current_subtask_observations,
            "user_profile": self._serialize_user_profile(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }

    @staticmethod
    def _serialize_observations(observations: list[PlannerObservation]) -> list[dict[str, Any]]:
        return [
            {
                "tool": item.tool,
                "input": item.input_text,
                "ok": item.ok,
                "result": item.result,
            }
            for item in observations
        ]

    @staticmethod
    def _serialize_completed_subtasks(completed_subtasks: list[CompletedSubtask]) -> list[dict[str, Any]]:
        return [
            {
                "item": item.item,
                "result": item.result,
            }
            for item in completed_subtasks
        ]

    @staticmethod
    def _serialize_latest_plan(latest_plan: list[PlanStep]) -> list[dict[str, Any]]:
        return [
            {
                "task": item.item,
                "completed": item.completed,
            }
            for item in latest_plan
        ]

    def _serialize_user_profile(self) -> str | None:
        if not self._user_profile_content:
            return None
        return self._user_profile_content

    def reload_user_profile(self) -> bool:
        loaded_path, loaded_content = self._load_user_profile(self._user_profile_path)
        self._user_profile_path = loaded_path
        self._user_profile_content = loaded_content
        return loaded_content is not None

    def _load_user_profile(self, user_profile_path: str) -> tuple[str, str | None]:
        raw_path = user_profile_path.strip()
        if not raw_path:
            return "", None
        resolved_path = self._resolve_user_profile_path(raw_path)
        try:
            content = resolved_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            self._app_logger.warning(
                "user profile file not found",
                extra={
                    "event": "user_profile_not_found",
                    "context": {"path": str(resolved_path)},
                },
            )
            return str(resolved_path), None
        except (OSError, UnicodeError):
            self._app_logger.warning(
                "failed to read user profile file",
                extra={
                    "event": "user_profile_read_failed",
                    "context": {"path": str(resolved_path)},
                },
                exc_info=True,
            )
            return str(resolved_path), None
        if not content:
            return str(resolved_path), None
        if len(content) > self._user_profile_max_chars:
            raise ValueError(
                "USER_PROFILE_PATH 对应文件内容超长："
                f"{len(content)} 字符，最大允许 {self._user_profile_max_chars} 字符。"
            )
        return str(resolved_path), content

    @staticmethod
    def _resolve_user_profile_path(user_profile_path: str) -> Path:
        path = Path(user_profile_path).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (PROJECT_ROOT / path).resolve()

    @staticmethod
    def _serialize_clarification_history(
        clarification_history: list[ClarificationTurn],
    ) -> list[dict[str, str]]:
        return [
            {
                "role": item.role,
                "content": item.content,
            }
            for item in clarification_history
        ]

    @staticmethod
    def _serialize_chat_turns_as_messages(chat_turns: list[ChatTurn]) -> list[dict[str, str]]:
        history_messages: list[dict[str, str]] = []
        for item in chat_turns:
            if item.user_content.strip():
                history_messages.append({"role": "user", "content": item.user_content})
            if item.assistant_content.strip():
                history_messages.append({"role": "assistant", "content": item.assistant_content})
        return history_messages

    @staticmethod
    def _current_plan_item_text(task: PendingPlanTask) -> str:
        if task.outer_context is None:
            return ""
        if not task.outer_context.latest_plan:
            return ""
        if task.outer_context.current_plan_index < 0:
            return ""
        if task.outer_context.current_plan_index >= len(task.outer_context.latest_plan):
            return ""
        return task.outer_context.latest_plan[task.outer_context.current_plan_index].item

    @staticmethod
    def _sync_current_plan_index(outer: OuterPlanContext) -> None:
        if not outer.latest_plan:
            outer.current_plan_index = 0
            return
        start = min(max(outer.current_plan_index, 0), len(outer.latest_plan))
        for idx in range(start, len(outer.latest_plan)):
            if not outer.latest_plan[idx].completed:
                outer.current_plan_index = idx
                return
        for idx, step in enumerate(outer.latest_plan):
            if not step.completed:
                outer.current_plan_index = idx
                return
        outer.current_plan_index = len(outer.latest_plan)

    def _llm_reply_for_planner(self, messages: list[dict[str, Any]]) -> str:
        if self.llm_client is None:
            return ""

        reply_json = getattr(self.llm_client, "reply_json", None)
        if callable(reply_json):
            try:
                return str(reply_json(messages))
            except Exception:
                pass
        return self.llm_client.reply(messages)

    def _llm_reply_for_thought(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        if self.llm_client is None:
            return {}

        reply_with_tools = getattr(self.llm_client, "reply_with_tools", None)
        if not callable(reply_with_tools):
            raw = self._llm_reply_for_planner(messages)
            payload = _try_parse_json(_strip_think_blocks(raw).strip())
            if not isinstance(payload, dict):
                return {}
            decision = normalize_thought_decision(payload)
            if decision is None:
                return {}
            return {"decision": decision}

        try:
            tool_response = reply_with_tools(messages, tools=THOUGHT_TOOL_SCHEMAS, tool_choice="auto")
        except RuntimeError as exc:
            message = str(exc)
            lowered = message.lower()
            if "thinking" in lowered or "reasoning_content" in lowered or "reasoner" in lowered:
                raise ThoughtToolCallingError(message) from exc
            raise

        reasoning_content = str(tool_response.get("reasoning_content") or "").strip()
        if reasoning_content:
            raise ThoughtToolCallingError(
                "当前版本 thought 阶段暂不支持 thinking 模式（检测到 reasoning_content），"
                "请切换到非 thinking 模式后重试。"
            )

        assistant_message = tool_response.get("assistant_message")
        if not isinstance(assistant_message, dict):
            return {}

        payload: dict[str, Any] = {"assistant_message": assistant_message}
        tool_calls = assistant_message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            if len(tool_calls) > 1:
                raise ThoughtToolCallingError(
                    f"thought 阶段每轮最多调用 1 个工具（本轮收到 {len(tool_calls)} 个），请重试。"
                )
            first_tool_call = tool_calls[0]
            if not isinstance(first_tool_call, dict):
                return {}
            decision = normalize_thought_tool_call(first_tool_call)
            if decision is None:
                return {}
            payload["decision"] = decision
            call_id = str(first_tool_call.get("id") or "").strip()
            if call_id:
                payload["tool_call_id"] = call_id
            return payload

        raw_content = assistant_message.get("content")
        content = str(raw_content or "").strip()
        parsed_content = _try_parse_json(_strip_think_blocks(content))
        if not isinstance(parsed_content, dict):
            return {}
        decision = normalize_thought_decision(parsed_content)
        if decision is None:
            return {}
        payload["decision"] = decision
        return payload

    def _next_llm_trace_call_id(self) -> int:
        self._llm_trace_call_seq += 1
        return self._llm_trace_call_seq

    def _llm_trace_phase(self, messages: list[dict[str, Any]]) -> str:
        if not messages:
            return "unknown"
        payload = _try_parse_json(str(messages[-1].get("content", "")))
        if not isinstance(payload, dict):
            return "unknown"
        phase = str(payload.get("phase") or "").strip().lower()
        return phase or "unknown"

    def _log_llm_trace_event(self, payload: dict[str, Any]) -> None:
        try:
            self._llm_trace_logger.info(json.dumps(payload, ensure_ascii=False))
        except Exception:
            return

    def _execute_planner_tool(self, *, action_tool: str, action_input: str) -> PlannerObservation:
        if action_tool == "todo":
            normalized_input = action_input.strip()
            # Backward-compatible fallback for non-tool-calling thought outputs.
            if normalized_input.startswith("/todo"):
                command_result = self._handle_command(normalized_input)
                ok = _is_planner_command_success(command_result, tool="todo")
                return PlannerObservation(tool="todo", input_text=normalized_input, ok=ok, result=command_result)

            payload = _try_parse_json(normalized_input)
            if not isinstance(payload, dict):
                return PlannerObservation(
                    tool="todo",
                    input_text=action_input,
                    ok=False,
                    result="todo 工具参数无效：需要 JSON 对象。",
                )
            return self._execute_todo_system_action(payload, raw_input=normalized_input)

        if action_tool == "schedule":
            normalized_input = action_input.strip()
            # Backward-compatible fallback for non-tool-calling thought outputs.
            if normalized_input.startswith("/schedule"):
                command_result = self._handle_command(normalized_input)
                ok = _is_planner_command_success(command_result, tool="schedule")
                return PlannerObservation(tool="schedule", input_text=normalized_input, ok=ok, result=command_result)

            payload = _try_parse_json(normalized_input)
            if not isinstance(payload, dict):
                return PlannerObservation(
                    tool="schedule",
                    input_text=action_input,
                    ok=False,
                    result="schedule 工具参数无效：需要 JSON 对象。",
                )
            return self._execute_schedule_system_action(payload, raw_input=normalized_input)

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
            formatted = _format_search_results(search_results, top_k=self._internet_search_top_k)
            return PlannerObservation(tool="internet_search", input_text=query, ok=True, result=formatted)

        if action_tool == "history_search":
            normalized_input = action_input.strip()
            # Backward-compatible fallback for non-tool-calling thought outputs.
            if normalized_input.startswith("/history search"):
                command_result = self._handle_command(normalized_input)
                ok = _is_planner_command_success(command_result, tool="history_search")
                return PlannerObservation(
                    tool="history_search",
                    input_text=normalized_input,
                    ok=ok,
                    result=command_result,
                )

            payload = _try_parse_json(normalized_input)
            if not isinstance(payload, dict):
                return PlannerObservation(
                    tool="history_search",
                    input_text=action_input,
                    ok=False,
                    result="history_search 工具参数无效：需要 JSON 对象。",
                )
            return self._execute_history_search_system_action(payload, raw_input=normalized_input)

        return PlannerObservation(
            tool=action_tool or "unknown",
            input_text=action_input,
            ok=False,
            result=f"未知工具: {action_tool}",
        )

    def _execute_todo_system_action(self, payload: dict[str, Any], *, raw_input: str) -> PlannerObservation:
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"add", "list", "get", "update", "delete", "done", "search", "view"}:
            return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result="todo.action 非法。")

        if action == "add":
            content = str(payload.get("content") or "").strip()
            if not content:
                return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result="todo.add 缺少 content。")
            parsed_tag = _normalize_todo_tag_value(payload.get("tag"))
            add_tag = parsed_tag or "default"
            if "priority" in payload:
                add_priority = _normalize_todo_priority_value(payload.get("priority"))
                if add_priority is None:
                    return PlannerObservation(
                        tool="todo",
                        input_text=raw_input,
                        ok=False,
                        result="todo.add priority 需为 >=0 的整数。",
                    )
            else:
                add_priority = 0
            add_due_at = _normalize_optional_datetime_value(payload.get("due_at"), key_present="due_at" in payload)
            if add_due_at is _INVALID_OPTION_VALUE:
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="todo.add due_at 格式非法，需为 YYYY-MM-DD HH:MM。",
                )
            add_remind_at = _normalize_optional_datetime_value(
                payload.get("remind_at"),
                key_present="remind_at" in payload,
            )
            if add_remind_at is _INVALID_OPTION_VALUE:
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="todo.add remind_at 格式非法，需为 YYYY-MM-DD HH:MM。",
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
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="提醒时间需要和截止时间一起设置，且优先级必须为大于等于 0 的整数。",
                )
            result = (
                f"已添加待办 #{added_todo_id} [标签:{add_tag}]: {content}"
                f"{_format_todo_meta_inline(add_due_at, add_remind_at, priority=add_priority)}"
            )
            return PlannerObservation(tool="todo", input_text=raw_input, ok=True, result=result)

        if action in {"list", "view"}:
            if action == "view":
                list_view = _normalize_todo_view_value(payload.get("view"))
                if list_view is None:
                    return PlannerObservation(
                        tool="todo",
                        input_text=raw_input,
                        ok=False,
                        result="todo.view 需要合法 view(all|today|overdue|upcoming|inbox)。",
                    )
            else:
                if "view" in payload:
                    list_view = _normalize_todo_view_value(payload.get("view"))
                    if list_view is None:
                        return PlannerObservation(
                            tool="todo",
                            input_text=raw_input,
                            ok=False,
                            result="todo.list 的 view 参数非法。",
                        )
                else:
                    list_view = "all"
            list_tag = _normalize_todo_tag_value(payload.get("tag"))
            todos = self.db.list_todos(tag=list_tag)
            todos = _filter_todos_by_view(todos, view_name=list_view)
            if not todos:
                result = _todo_list_empty_text(tag=list_tag, view_name=list_view)
                return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result=result)

            header = _todo_list_header(tag=list_tag, view_name=list_view)
            table = _render_todo_table(todos)
            return PlannerObservation(tool="todo", input_text=raw_input, ok=True, result=f"{header}\n{table}")

        if action == "search":
            keyword = str(payload.get("keyword") or "").strip()
            if not keyword:
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="todo.search 缺少 keyword。",
                )
            search_tag = _normalize_todo_tag_value(payload.get("tag"))
            todos = self.db.search_todos(keyword, tag=search_tag)
            if not todos:
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result=_todo_search_empty_text(keyword=keyword, tag=search_tag),
                )
            table = _render_todo_table(todos)
            header = _todo_search_header(keyword=keyword, tag=search_tag)
            return PlannerObservation(tool="todo", input_text=raw_input, ok=True, result=f"{header}\n{table}")

        todo_id = _normalize_positive_int_value(payload.get("id"))
        if todo_id is None:
            return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result="todo.id 必须为正整数。")

        if action == "get":
            todo = self.db.get_todo(todo_id)
            if todo is None:
                return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result=f"未找到待办 #{todo_id}")
            table = _render_todo_table([todo])
            return PlannerObservation(tool="todo", input_text=raw_input, ok=True, result=f"待办详情:\n{table}")

        if action == "update":
            content = str(payload.get("content") or "").strip()
            if not content:
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="todo.update 缺少 content。",
                )
            current = self.db.get_todo(todo_id)
            if current is None:
                return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result=f"未找到待办 #{todo_id}")
            has_priority = "priority" in payload
            if has_priority:
                update_priority = _normalize_todo_priority_value(payload.get("priority"))
                if update_priority is None:
                    return PlannerObservation(
                        tool="todo",
                        input_text=raw_input,
                        ok=False,
                        result="todo.update priority 需为 >=0 的整数。",
                    )
            else:
                update_priority = None
            has_due = "due_at" in payload
            update_due_at = _normalize_optional_datetime_value(payload.get("due_at"), key_present=has_due)
            if update_due_at is _INVALID_OPTION_VALUE:
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="todo.update due_at 格式非法，需为 YYYY-MM-DD HH:MM。",
                )
            has_remind = "remind_at" in payload
            update_remind_at = _normalize_optional_datetime_value(payload.get("remind_at"), key_present=has_remind)
            if update_remind_at is _INVALID_OPTION_VALUE:
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="todo.update remind_at 格式非法，需为 YYYY-MM-DD HH:MM。",
                )
            if has_remind and update_remind_at and not ((has_due and update_due_at) or current.due_at):
                return PlannerObservation(
                    tool="todo",
                    input_text=raw_input,
                    ok=False,
                    result="提醒时间需要和截止时间一起设置。",
                )
            update_kwargs: dict[str, Any] = {"content": content}
            update_tag = _normalize_todo_tag_value(payload.get("tag"))
            if "tag" in payload:
                update_kwargs["tag"] = update_tag or "default"
            if has_priority:
                update_kwargs["priority"] = update_priority
            if has_due:
                update_kwargs["due_at"] = update_due_at
            if has_remind:
                update_kwargs["remind_at"] = update_remind_at
            updated = self.db.update_todo(todo_id, **update_kwargs)
            if not updated:
                return PlannerObservation(tool="todo", input_text=raw_input, ok=False, result=f"未找到待办 #{todo_id}")
            todo = self.db.get_todo(todo_id)
            if todo is None:
                result = f"已更新待办 #{todo_id}: {content}"
            else:
                result = (
                    f"已更新待办 #{todo_id} [标签:{todo.tag}]: {content}"
                    f"{_format_todo_meta_inline(todo.due_at, todo.remind_at, priority=todo.priority)}"
                )
            ok = _is_planner_command_success(result, tool="todo")
            return PlannerObservation(tool="todo", input_text=raw_input, ok=ok, result=result)

        if action == "delete":
            deleted = self.db.delete_todo(todo_id)
            if not deleted:
                result = f"未找到待办 #{todo_id}"
            else:
                result = f"待办 #{todo_id} 已删除。"
            ok = _is_planner_command_success(result, tool="todo")
            return PlannerObservation(tool="todo", input_text=raw_input, ok=ok, result=result)

        # done
        done = self.db.mark_todo_done(todo_id)
        if not done:
            result = f"未找到待办 #{todo_id}"
        else:
            todo = self.db.get_todo(todo_id)
            done_completed_at = todo.completed_at if todo is not None else _now_time_text()
            result = f"待办 #{todo_id} 已完成。完成时间: {done_completed_at}"
        ok = _is_planner_command_success(result, tool="todo")
        return PlannerObservation(tool="todo", input_text=raw_input, ok=ok, result=result)

    def _execute_schedule_system_action(self, payload: dict[str, Any], *, raw_input: str) -> PlannerObservation:
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"add", "list", "get", "view", "update", "delete", "repeat"}:
            return PlannerObservation(tool="schedule", input_text=raw_input, ok=False, result="schedule.action 非法。")

        if action == "list":
            list_tag = _normalize_schedule_tag_value(payload.get("tag"))
            window_start, window_end = _default_schedule_list_window(window_days=self._schedule_max_window_days)
            items = self.db.list_schedules(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._schedule_max_window_days,
                tag=list_tag,
            )
            if not items:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result=_schedule_list_empty_text(window_days=self._schedule_max_window_days, tag=list_tag),
                )
            table = _render_table(
                headers=_schedule_table_headers(),
                rows=_schedule_table_rows(items),
            )
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=True,
                result=f"{_schedule_list_title(window_days=self._schedule_max_window_days, tag=list_tag)}:\n{table}",
            )

        if action == "view":
            view_name = _normalize_schedule_view_value(payload.get("view"))
            if view_name is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.view 需要合法 view(day|week|month)。",
                )
            anchor: str | None = None
            if "anchor" in payload and payload.get("anchor") is not None:
                anchor = _normalize_schedule_view_anchor(view_name=view_name, value=str(payload.get("anchor")))
                if anchor is None:
                    return PlannerObservation(
                        tool="schedule",
                        input_text=raw_input,
                        ok=False,
                        result="schedule.view 的 anchor 非法。",
                    )
            window_start, window_end = _resolve_schedule_view_window(view_name=view_name, anchor=anchor)
            view_tag = _normalize_schedule_tag_value(payload.get("tag"))
            items = self.db.list_schedules(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._schedule_max_window_days,
                tag=view_tag,
            )
            items = _filter_schedules_by_calendar_view(items, view_name=view_name, anchor=anchor)
            if not items:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result=f"{view_name} 视图下{f'（标签:{view_tag}）' if view_tag else ''}暂无日程。",
                )
            table = _render_table(
                headers=_schedule_table_headers(),
                rows=_schedule_table_rows(items),
            )
            title = _schedule_view_title(view_name=view_name, anchor=anchor, tag=view_tag)
            return PlannerObservation(tool="schedule", input_text=raw_input, ok=True, result=f"{title}:\n{table}")

        if action == "add":
            add_event_time = _normalize_datetime_text(str(payload.get("event_time") or ""))
            add_title = str(payload.get("title") or "").strip()
            add_tag = _normalize_schedule_tag_value(payload.get("tag")) or "default"
            if not add_event_time or not add_title:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.add 缺少 event_time/title 或格式非法。",
                )
            if "duration_minutes" in payload:
                add_duration_minutes = _normalize_schedule_duration_minutes_value(payload.get("duration_minutes"))
                if add_duration_minutes is None:
                    return PlannerObservation(
                        tool="schedule",
                        input_text=raw_input,
                        ok=False,
                        result="schedule.add duration_minutes 需为 >=1 的整数。",
                    )
            else:
                add_duration_minutes = 60
            add_remind_at = _normalize_optional_datetime_value(payload.get("remind_at"), key_present="remind_at" in payload)
            if add_remind_at is _INVALID_OPTION_VALUE:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.add remind_at 格式非法。",
                )
            if "interval_minutes" in payload:
                add_repeat_interval_minutes = _normalize_schedule_interval_minutes_value(payload.get("interval_minutes"))
                if add_repeat_interval_minutes is None:
                    return PlannerObservation(
                        tool="schedule",
                        input_text=raw_input,
                        ok=False,
                        result="schedule.add interval_minutes 需为 >=1 的整数。",
                    )
            else:
                add_repeat_interval_minutes = None
            if "times" in payload:
                add_repeat_times = _normalize_schedule_repeat_times_value(payload.get("times"))
                if add_repeat_times is None:
                    return PlannerObservation(
                        tool="schedule",
                        input_text=raw_input,
                        ok=False,
                        result="schedule.add times 需为 -1 或 >=2 的整数。",
                    )
            else:
                add_repeat_times = -1 if add_repeat_interval_minutes is not None else 1
            has_repeat_remind_start_time = "remind_start_time" in payload
            add_repeat_remind_start_time = _normalize_optional_datetime_value(
                payload.get("remind_start_time"),
                key_present=has_repeat_remind_start_time,
            )
            if add_repeat_remind_start_time is _INVALID_OPTION_VALUE:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.add remind_start_time 格式非法。",
                )
            if add_repeat_interval_minutes is None and add_repeat_times != 1:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.add 提供 times 时必须同时提供 interval_minutes。",
                )
            if add_repeat_interval_minutes is not None and add_repeat_times == 1:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.add interval_minutes 存在时，times 不能为 1。",
                )
            if has_repeat_remind_start_time and add_repeat_interval_minutes is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.add 提供 remind_start_time 时必须提供 interval_minutes。",
                )
            schedule_id = self.db.add_schedule(
                title=add_title,
                event_time=add_event_time,
                duration_minutes=add_duration_minutes,
                remind_at=add_remind_at,
                tag=add_tag,
            )
            if add_repeat_interval_minutes is not None and add_repeat_times != 1:
                self.db.set_schedule_recurrence(
                    schedule_id,
                    start_time=add_event_time,
                    repeat_interval_minutes=add_repeat_interval_minutes,
                    repeat_times=add_repeat_times,
                    remind_start_time=add_repeat_remind_start_time,
                )
            remind_meta = _format_schedule_remind_meta_inline(
                remind_at=add_remind_at,
                repeat_remind_start_time=add_repeat_remind_start_time,
            )
            if add_repeat_times == 1:
                result = (
                    f"已添加日程 #{schedule_id} [标签:{add_tag}]: {add_event_time} {add_title} "
                    f"({add_duration_minutes} 分钟){remind_meta}"
                )
            elif add_repeat_times == -1:
                result = (
                    f"已添加无限重复日程 #{schedule_id} [标签:{add_tag}]: {add_event_time} {add_title} "
                    f"(duration={add_duration_minutes}m, interval={add_repeat_interval_minutes}m{remind_meta})"
                )
            else:
                result = (
                    f"已添加重复日程 {add_repeat_times} 条 [标签:{add_tag}]: {add_event_time} {add_title} "
                    f"(duration={add_duration_minutes}m, interval={add_repeat_interval_minutes}m, "
                    f"times={add_repeat_times}{remind_meta})"
                )
            return PlannerObservation(tool="schedule", input_text=raw_input, ok=True, result=result)

        schedule_id = _normalize_positive_int_value(payload.get("id"))
        if schedule_id is None:
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.id 必须为正整数。",
            )

        if action == "get":
            item = self.db.get_schedule(schedule_id)
            if item is None:
                return PlannerObservation(tool="schedule", input_text=raw_input, ok=False, result=f"未找到日程 #{schedule_id}")
            table = _render_table(
                headers=_schedule_table_headers(),
                rows=_schedule_table_rows([item]),
            )
            return PlannerObservation(tool="schedule", input_text=raw_input, ok=True, result=f"日程详情:\n{table}")

        if action == "update":
            event_time = _normalize_datetime_text(str(payload.get("event_time") or ""))
            title = str(payload.get("title") or "").strip()
            if not event_time or not title:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.update 缺少 event_time/title 或格式非法。",
                )
            current_item = self.db.get_schedule(schedule_id)
            if current_item is None:
                return PlannerObservation(tool="schedule", input_text=raw_input, ok=False, result=f"未找到日程 #{schedule_id}")
            update_tag = _normalize_schedule_tag_value(payload.get("tag"))
            if "duration_minutes" in payload:
                parsed_duration_minutes = _normalize_schedule_duration_minutes_value(payload.get("duration_minutes"))
                if parsed_duration_minutes is None:
                    return PlannerObservation(
                        tool="schedule",
                        input_text=raw_input,
                        ok=False,
                        result="schedule.update duration_minutes 需为 >=1 的整数。",
                    )
            else:
                parsed_duration_minutes = None
            if parsed_duration_minutes is not None:
                applied_duration_minutes = parsed_duration_minutes
            else:
                applied_duration_minutes = current_item.duration_minutes
            has_remind = "remind_at" in payload
            parsed_remind_at = _normalize_optional_datetime_value(payload.get("remind_at"), key_present=has_remind)
            if parsed_remind_at is _INVALID_OPTION_VALUE:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.update remind_at 格式非法。",
                )
            if "interval_minutes" in payload:
                repeat_interval_minutes = _normalize_schedule_interval_minutes_value(payload.get("interval_minutes"))
                if repeat_interval_minutes is None:
                    return PlannerObservation(
                        tool="schedule",
                        input_text=raw_input,
                        ok=False,
                        result="schedule.update interval_minutes 需为 >=1 的整数。",
                    )
            else:
                repeat_interval_minutes = None
            if "times" in payload:
                repeat_times = _normalize_schedule_repeat_times_value(payload.get("times"))
                if repeat_times is None:
                    return PlannerObservation(
                        tool="schedule",
                        input_text=raw_input,
                        ok=False,
                        result="schedule.update times 需为 -1 或 >=2 的整数。",
                    )
            else:
                repeat_times = -1 if repeat_interval_minutes is not None else 1
            has_repeat_remind_start_time = "remind_start_time" in payload
            repeat_remind_start_time = _normalize_optional_datetime_value(
                payload.get("remind_start_time"),
                key_present=has_repeat_remind_start_time,
            )
            if repeat_remind_start_time is _INVALID_OPTION_VALUE:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.update remind_start_time 格式非法。",
                )
            if repeat_interval_minutes is None and repeat_times != 1:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.update 提供 times 时必须同时提供 interval_minutes。",
                )
            if repeat_interval_minutes is not None and repeat_times == 1:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.update interval_minutes 存在时，times 不能为 1。",
                )
            if has_repeat_remind_start_time and repeat_interval_minutes is None:
                return PlannerObservation(
                    tool="schedule",
                    input_text=raw_input,
                    ok=False,
                    result="schedule.update 提供 remind_start_time 时必须提供 interval_minutes。",
                )
            schedule_update_kwargs: dict[str, Any] = {
                "title": title,
                "event_time": event_time,
                "duration_minutes": applied_duration_minutes,
            }
            if "tag" in payload:
                schedule_update_kwargs["tag"] = update_tag or "default"
            if has_remind:
                schedule_update_kwargs["remind_at"] = parsed_remind_at
            if has_repeat_remind_start_time:
                schedule_update_kwargs["repeat_remind_start_time"] = repeat_remind_start_time
            updated = self.db.update_schedule(schedule_id, **schedule_update_kwargs)
            if not updated:
                return PlannerObservation(tool="schedule", input_text=raw_input, ok=False, result=f"未找到日程 #{schedule_id}")
            if repeat_times == 1:
                self.db.clear_schedule_recurrence(schedule_id)
                item = self.db.get_schedule(schedule_id)
                remind_meta = _format_schedule_remind_meta_inline(
                    remind_at=item.remind_at if item else None,
                    repeat_remind_start_time=item.repeat_remind_start_time if item else None,
                )
                result = f"已更新日程 #{schedule_id}: {event_time} {title} ({applied_duration_minutes} 分钟){remind_meta}"
                if item is not None:
                    result = (
                        f"已更新日程 #{schedule_id} [标签:{item.tag}]: {event_time} {title} "
                        f"({applied_duration_minutes} 分钟){remind_meta}"
                    )
                ok = _is_planner_command_success(result, tool="schedule")
                return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)
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
                result = (
                    f"已更新为无限重复日程 #{schedule_id} [标签:{item.tag if item else current_item.tag}]: "
                    f"{event_time} {title} "
                    f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m{remind_meta})"
                )
            else:
                result = (
                    f"已更新日程 #{schedule_id} [标签:{item.tag if item else current_item.tag}]: {event_time} {title} "
                    f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m, "
                    f"times={repeat_times}{remind_meta})"
                )
            ok = _is_planner_command_success(result, tool="schedule")
            return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)

        if action == "delete":
            deleted = self.db.delete_schedule(schedule_id)
            if not deleted:
                result = f"未找到日程 #{schedule_id}"
            else:
                result = f"日程 #{schedule_id} 已删除。"
            ok = _is_planner_command_success(result, tool="schedule")
            return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)

        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            return PlannerObservation(
                tool="schedule",
                input_text=raw_input,
                ok=False,
                result="schedule.repeat 需要 enabled 布尔值。",
            )
        changed = self.db.set_schedule_recurrence_enabled(schedule_id, enabled)
        if not changed:
            result = f"日程 #{schedule_id} 没有可切换的重复规则。"
        else:
            status = "启用" if enabled else "停用"
            result = f"已{status}日程 #{schedule_id} 的重复规则。"
        ok = _is_planner_command_success(result, tool="schedule")
        return PlannerObservation(tool="schedule", input_text=raw_input, ok=ok, result=result)

    def _execute_history_search_system_action(
        self,
        payload: dict[str, Any],
        *,
        raw_input: str,
    ) -> PlannerObservation:
        keyword = str(payload.get("keyword") or "").strip()
        if not keyword:
            return PlannerObservation(
                tool="history_search",
                input_text=raw_input,
                ok=False,
                result="history_search.keyword 不能为空。",
            )
        if "limit" in payload:
            parsed_limit = _normalize_positive_int_value(payload.get("limit"))
            if parsed_limit is None:
                return PlannerObservation(
                    tool="history_search",
                    input_text=raw_input,
                    ok=False,
                    result="history_search.limit 必须为正整数。",
                )
            history_limit = min(parsed_limit, MAX_HISTORY_LIST_LIMIT)
        else:
            history_limit = DEFAULT_HISTORY_LIST_LIMIT
        turns = self.db.search_turns(keyword, limit=history_limit)
        if not turns:
            result = f"未找到包含“{keyword}”的历史会话。"
            ok = _is_planner_command_success(result, tool="history_search")
            return PlannerObservation(tool="history_search", input_text=raw_input, ok=ok, result=result)
        rows = [
            [
                str(index),
                _truncate_text(item.user_content, 300) or "-",
                _truncate_text(item.assistant_content, 300) or "-",
                item.created_at,
            ]
            for index, item in enumerate(turns, start=1)
        ]
        table = _render_table(headers=["#", "用户输入", "最终回答", "时间"], rows=rows)
        result = f"历史搜索(关键词: {keyword}, 命中 {len(turns)} 轮):\n{table}"
        ok = _is_planner_command_success(result, tool="history_search")
        return PlannerObservation(tool="history_search", input_text=raw_input, ok=ok, result=result)

    def _append_observation(self, task: PendingPlanTask, observation: PlannerObservation) -> PlannerObservation:
        truncated = _truncate_text(observation.result, self._plan_observation_char_limit)
        normalized = PlannerObservation(
            tool=observation.tool,
            input_text=observation.input_text,
            ok=observation.ok,
            result=truncated,
        )
        task.observations.append(normalized)
        inner = self._ensure_inner_context(task)
        inner.observations.append(normalized)
        return normalized

    def _append_planner_decision_observation(
        self,
        task: PendingPlanTask,
        *,
        phase: str,
        decision: dict[str, Any],
    ) -> None:
        normalized_phase = phase.strip().lower()
        if normalized_phase not in {"plan", "thought", "replan"}:
            normalized_phase = "planner"
        result = _truncate_text(
            json.dumps(decision, ensure_ascii=False, separators=(",", ":")),
            self._plan_observation_char_limit,
        )
        status = str(decision.get("status") or normalized_phase).strip() or normalized_phase
        self._append_observation(
            task,
            PlannerObservation(
                tool=normalized_phase,
                input_text=status,
                ok=True,
                result=result,
            ),
        )

    def _append_completed_subtask(self, task: PendingPlanTask, *, item: str, result: str) -> None:
        normalized_item = item.strip() or "当前子任务"
        normalized_result = result.strip() or "子任务已完成。"
        outer = self._outer_context(task)
        outer.completed_subtasks.append(
            CompletedSubtask(
                item=normalized_item,
                result=_truncate_text(normalized_result, self._plan_observation_char_limit),
            )
        )

    def _notify_replan_continue_subtask_result(self, task: PendingPlanTask) -> None:
        callback = self._subtask_result_callback
        if callback is None:
            return
        outer = self._outer_context(task)
        completed_subtasks = outer.completed_subtasks
        total_completed = len(completed_subtasks)
        if total_completed <= 0:
            return
        if task.last_notified_completed_subtask_count >= total_completed:
            return
        task.last_notified_completed_subtask_count = total_completed
        latest_item = completed_subtasks[-1].item.strip()
        if not latest_item:
            return
        latest_result = f"{latest_item}已完成"
        try:
            callback(latest_result)
        except Exception:
            self._app_logger.warning(
                "failed to notify replan continue subtask result",
                extra={"event": "replan_continue_subtask_notify_failed"},
                exc_info=True,
            )

    def _notify_plan_goal_result(self, task: PendingPlanTask, expanded_goal: str) -> None:
        callback = self._subtask_result_callback
        if callback is None or task.plan_goal_notified:
            return
        goal_text = expanded_goal.strip()
        if not goal_text:
            return
        task.plan_goal_notified = True
        try:
            callback(f"任务目标：{goal_text}")
        except Exception:
            self._app_logger.warning(
                "failed to notify plan expanded goal",
                extra={"event": "plan_goal_notify_failed"},
                exc_info=True,
            )

    @staticmethod
    def _latest_success_observation_result(task: PendingPlanTask) -> str:
        llm_tools = {"plan", "thought", "replan"}
        for item in reversed(task.observations):
            if item.ok and item.tool not in llm_tools:
                return item.result
        return ""

    @staticmethod
    def _merge_summary_with_detail(*, summary: str, detail: str) -> str:
        normalized_summary = summary.strip()
        normalized_detail = detail.strip()
        if not normalized_summary:
            return normalized_detail
        if not normalized_detail:
            return normalized_summary
        if normalized_detail in normalized_summary:
            return normalized_summary
        if not AssistantAgent._is_structured_query_result(normalized_detail):
            return normalized_summary
        return f"{normalized_summary}\n\n执行结果：\n{normalized_detail}"

    @staticmethod
    def _is_structured_query_result(result: str) -> bool:
        if "\n|" in result:
            return True
        prefixes = (
            "待办列表",
            "待办详情",
            "搜索结果",
            "日程列表",
            "日历视图(",
            "日程详情",
            "互联网搜索结果",
        )
        return result.startswith(prefixes)

    def _format_step_limit_response(self, task: PendingPlanTask) -> str:
        llm_tools = {"planner", "plan", "thought", "replan"}
        completed = [obs for obs in task.observations if obs.ok and obs.tool not in llm_tools]
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
        self._last_task_completed = True
        self._emit_progress("任务状态：已完成。")
        return response

    def _finalize_interrupted_task(self, task: PendingPlanTask) -> str:
        if self._pending_plan_task is task:
            self._pending_plan_task = None
        self._emit_progress("任务状态：已中断。")
        self._clear_interrupt_request()
        return "当前任务已被新消息中断，正在按最新输入重新执行。"

    def _rewrite_final_response(self, response: str) -> str:
        rewriter = self._final_response_rewriter
        if rewriter is None:
            return response
        try:
            rewritten = rewriter(response)
        except Exception:
            return response
        normalized = rewritten.strip()
        return normalized or response

    @staticmethod
    def _planner_unavailable_text() -> str:
        return "抱歉，当前计划执行服务暂时不可用。你可以稍后重试，或先使用 /todo、/schedule 命令继续操作。"

    @staticmethod
    def _post_plan_missing_done_text(task: PendingPlanTask) -> str:
        latest_success = next((item for item in reversed(task.observations) if item.ok), None)
        if latest_success is not None:
            preview = _truncate_text(latest_success.result.replace("\n", " "), 200)
            return (
                "计划步骤已执行完毕，但模型未返回可用的子任务结论。\n"
                f"最近一次成功结果：{preview}\n"
                "我会继续交给 replan 决策是否收口，你也可以直接使用 /todo、/schedule 命令查看结果。"
            )
        return (
            "计划步骤已执行完毕，但模型未返回可用的子任务结论。"
            "我会继续交给 replan 决策是否收口，你也可以直接使用 /todo、/schedule 命令查看结果。"
        )

    def _emit_progress(self, message: str) -> None:
        callback = self._progress_callback
        if callback is None:
            return
        callback(message)

    def _raise_if_task_interrupted(self) -> None:
        if self._is_interrupt_requested():
            raise TaskInterruptedError("task interrupted by newer input")

    def _is_interrupt_requested(self) -> bool:
        with self._interrupt_lock:
            return self._interrupt_requested

    def _clear_interrupt_request(self) -> None:
        with self._interrupt_lock:
            self._interrupt_requested = False

    def _emit_plan_progress(self, task: PendingPlanTask) -> None:
        outer = self._outer_context(task)
        if not outer.latest_plan:
            return
        signature = tuple((step.item, step.completed) for step in outer.latest_plan)
        if task.last_reported_plan_signature == signature:
            return
        task.last_reported_plan_signature = signature
        lines = ["计划列表："]
        for idx, step in enumerate(outer.latest_plan, start=1):
            status = "完成" if step.completed else "待办"
            lines.append(f"{idx}. [{status}] {step.item}")
        self._emit_progress("\n".join(lines))

    def _emit_current_plan_item_progress(self, task: PendingPlanTask) -> None:
        outer = self._outer_context(task)
        if not outer.latest_plan:
            return
        if outer.current_plan_index < 0 or outer.current_plan_index >= len(outer.latest_plan):
            return
        index = outer.current_plan_index + 1
        total = len(outer.latest_plan)
        self._emit_progress(f"当前计划项：{index}/{total} - {outer.latest_plan[outer.current_plan_index].item}")

    @staticmethod
    def _progress_total_text(task: PendingPlanTask) -> str:
        if task.outer_context is None or not task.outer_context.latest_plan:
            return "未定"
        # Replan may shorten current plan. Keep denominator >= executed steps to avoid "20/1" style confusion.
        return str(max(task.step_count, len(task.outer_context.latest_plan)))

    @staticmethod
    def _current_plan_total_text(task: PendingPlanTask) -> str | None:
        if task.outer_context is None or not task.outer_context.latest_plan:
            return None
        return str(len(task.outer_context.latest_plan))

    def _handle_command(self, command: str) -> str:
        if command == "/help":
            return self._help_text()
        if command == "/version":
            if self._app_version == UNKNOWN_APP_VERSION:
                return "当前版本：unknown"
            return f"当前版本：v{self._app_version}"
        if command.split(maxsplit=1)[0] == "/version":
            return "用法: /version"

        if command == "/profile refresh":
            runner = self._user_profile_refresh_runner
            if runner is None:
                return "当前未启用 user_profile 刷新服务。请检查 USER_PROFILE_REFRESH_ENABLED、USER_PROFILE_PATH 与 LLM 配置。"
            try:
                return runner()
            except Exception as exc:  # noqa: BLE001
                self._app_logger.warning(
                    "manual user profile refresh failed",
                    extra={
                        "event": "user_profile_manual_refresh_failed",
                        "context": {"error": repr(exc)},
                    },
                )
                return f"刷新 user_profile 失败: {exc}"

        if command == "/history list" or command.startswith("/history list "):
            history_limit = _parse_history_list_limit(command)
            if history_limit is None:
                return "用法: /history list [--limit <>=1>]"
            turns = self.db.recent_turns(limit=history_limit)
            if not turns:
                return "暂无历史会话。"
            rows = [
                [
                    str(index),
                    _truncate_text(item.user_content, 300) or "-",
                    _truncate_text(item.assistant_content, 300) or "-",
                    item.created_at,
                ]
                for index, item in enumerate(turns, start=1)
            ]
            table = _render_table(headers=["#", "用户输入", "最终回答", "时间"], rows=rows)
            return f"历史会话(最近 {len(turns)} 轮):\n{table}"

        if command.startswith("/history search "):
            history_search = _parse_history_search_input(command.removeprefix("/history search ").strip())
            if history_search is None:
                return "用法: /history search <关键词> [--limit <>=1>]"
            keyword, history_limit = history_search
            turns = self.db.search_turns(keyword, limit=history_limit)
            if not turns:
                return f"未找到包含“{keyword}”的历史会话。"
            rows = [
                [
                    str(index),
                    _truncate_text(item.user_content, 300) or "-",
                    _truncate_text(item.assistant_content, 300) or "-",
                    item.created_at,
                ]
                for index, item in enumerate(turns, start=1)
            ]
            table = _render_table(headers=["#", "用户输入", "最终回答", "时间"], rows=rows)
            return f"历史搜索(关键词: {keyword}, 命中 {len(turns)} 轮):\n{table}"

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
                return _todo_list_empty_text(tag=list_tag, view_name=list_view)

            header = _todo_list_header(tag=list_tag, view_name=list_view)
            table = _render_todo_table(todos)
            return f"{header}\n{table}"

        if command.startswith("/todo search "):
            search_parsed = _parse_todo_search_input(command.removeprefix("/todo search ").strip())
            if search_parsed is None:
                return "用法: /todo search <关键词> [--tag <标签>]"
            keyword, search_tag = search_parsed
            todos = self.db.search_todos(keyword, tag=search_tag)
            if not todos:
                return _todo_search_empty_text(keyword=keyword, tag=search_tag)

            table = _render_todo_table(todos)
            header = _todo_search_header(keyword=keyword, tag=search_tag)
            return f"{header}\n{table}"

        if command.startswith("/todo get "):
            get_todo_id = _parse_positive_int(command.removeprefix("/todo get ").strip())
            if get_todo_id is None:
                return "用法: /todo get <id>"
            todo = self.db.get_todo(get_todo_id)
            if todo is None:
                return f"未找到待办 #{get_todo_id}"
            table = _render_todo_table([todo])
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

        if command == "/schedule list" or command.startswith("/schedule list "):
            list_tag = _parse_schedule_list_tag_input(command.removeprefix("/schedule list").strip())
            if list_tag is _INVALID_OPTION_VALUE:
                return "用法: /schedule list [--tag <标签>]"
            window_start, window_end = _default_schedule_list_window(
                window_days=self._schedule_max_window_days
            )
            items = self.db.list_schedules(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._schedule_max_window_days,
                tag=list_tag,
            )
            if not items:
                return _schedule_list_empty_text(window_days=self._schedule_max_window_days, tag=list_tag)
            table = _render_table(
                headers=_schedule_table_headers(),
                rows=_schedule_table_rows(items),
            )
            title = _schedule_list_title(window_days=self._schedule_max_window_days, tag=list_tag)
            return f"{title}:\n{table}"

        if command.startswith("/schedule view "):
            view_parsed = _parse_schedule_view_command_input(command.removeprefix("/schedule view ").strip())
            if view_parsed is None:
                return "用法: /schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM] [--tag <标签>]"
            view_name, anchor, view_tag = view_parsed
            window_start, window_end = _resolve_schedule_view_window(view_name=view_name, anchor=anchor)
            items = self.db.list_schedules(
                window_start=window_start,
                window_end=window_end,
                max_window_days=self._schedule_max_window_days,
                tag=view_tag,
            )
            items = _filter_schedules_by_calendar_view(items, view_name=view_name, anchor=anchor)
            if not items:
                return f"{view_name} 视图下{f'（标签:{view_tag}）' if view_tag else ''}暂无日程。"
            table = _render_table(
                headers=_schedule_table_headers(),
                rows=_schedule_table_rows(items),
            )
            title = _schedule_view_title(view_name=view_name, anchor=anchor, tag=view_tag)
            return f"{title}:\n{table}"

        if command.startswith("/schedule get "):
            schedule_id = _parse_positive_int(command.removeprefix("/schedule get ").strip())
            if schedule_id is None:
                return "用法: /schedule get <id>"
            item = self.db.get_schedule(schedule_id)
            if item is None:
                return f"未找到日程 #{schedule_id}"
            table = _render_table(
                headers=_schedule_table_headers(),
                rows=_schedule_table_rows([item]),
            )
            return f"日程详情:\n{table}"

        if command.startswith("/schedule add"):
            add_schedule_parsed = _parse_schedule_add_input(command.removeprefix("/schedule add").strip())
            if add_schedule_parsed is None:
                return (
                    "用法: /schedule add <YYYY-MM-DD HH:MM> <标题> "
                    "[--tag <标签>] "
                    "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
                    "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
                )
            (
                add_event_time,
                add_title,
                add_tag,
                add_duration_minutes,
                add_remind_at,
                add_repeat_interval_minutes,
                add_repeat_times,
                add_repeat_remind_start_time,
            ) = add_schedule_parsed
            schedule_id = self.db.add_schedule(
                title=add_title,
                event_time=add_event_time,
                duration_minutes=add_duration_minutes,
                remind_at=add_remind_at,
                tag=add_tag,
            )
            if add_repeat_interval_minutes is not None and add_repeat_times != 1:
                self.db.set_schedule_recurrence(
                    schedule_id,
                    start_time=add_event_time,
                    repeat_interval_minutes=add_repeat_interval_minutes,
                    repeat_times=add_repeat_times,
                    remind_start_time=add_repeat_remind_start_time,
                )
            remind_meta = _format_schedule_remind_meta_inline(
                remind_at=add_remind_at,
                repeat_remind_start_time=add_repeat_remind_start_time,
            )
            if add_repeat_times == 1:
                return (
                    f"已添加日程 #{schedule_id} [标签:{add_tag}]: {add_event_time} {add_title} "
                    f"({add_duration_minutes} 分钟){remind_meta}"
                )
            if add_repeat_times == -1:
                return (
                    f"已添加无限重复日程 #{schedule_id} [标签:{add_tag}]: {add_event_time} {add_title} "
                    f"(duration={add_duration_minutes}m, interval={add_repeat_interval_minutes}m{remind_meta})"
                )
            return (
                f"已添加重复日程 {add_repeat_times} 条 [标签:{add_tag}]: {add_event_time} {add_title} "
                f"(duration={add_duration_minutes}m, interval={add_repeat_interval_minutes}m, "
                f"times={add_repeat_times}{remind_meta})"
            )

        if command.startswith("/schedule update "):
            update_schedule_parsed = _parse_schedule_update_input(command.removeprefix("/schedule update ").strip())
            if update_schedule_parsed is None:
                return (
                    "用法: /schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
                    "[--tag <标签>] "
                    "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
                    "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]"
                )
            (
                schedule_id,
                event_time,
                title,
                parsed_tag,
                has_tag,
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
            schedule_update_kwargs: dict[str, Any] = {
                "title": title,
                "event_time": event_time,
                "duration_minutes": applied_duration_minutes,
            }
            if has_tag:
                schedule_update_kwargs["tag"] = parsed_tag
            if has_remind:
                schedule_update_kwargs["remind_at"] = parsed_remind_at
            if has_repeat_remind_start_time:
                schedule_update_kwargs["repeat_remind_start_time"] = repeat_remind_start_time
            updated = self.db.update_schedule(schedule_id, **schedule_update_kwargs)
            if not updated:
                return f"未找到日程 #{schedule_id}"
            if repeat_times == 1:
                self.db.clear_schedule_recurrence(schedule_id)
                item = self.db.get_schedule(schedule_id)
                remind_meta = _format_schedule_remind_meta_inline(
                    remind_at=item.remind_at if item else None,
                    repeat_remind_start_time=item.repeat_remind_start_time if item else None,
                )
                if item is not None:
                    return (
                        f"已更新日程 #{schedule_id} [标签:{item.tag}]: {event_time} {title} "
                        f"({applied_duration_minutes} 分钟){remind_meta}"
                    )
                return (
                    f"已更新日程 #{schedule_id} [标签:{parsed_tag if has_tag else current_item.tag}]: "
                    f"{event_time} {title} ({applied_duration_minutes} 分钟){remind_meta}"
                )
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
                    f"已更新为无限重复日程 #{schedule_id} [标签:{item.tag if item else current_item.tag}]: "
                    f"{event_time} {title} "
                    f"(duration={applied_duration_minutes}m, interval={repeat_interval_minutes}m{remind_meta})"
                )
            return (
                f"已更新日程 #{schedule_id} [标签:{item.tag if item else current_item.tag}]: {event_time} {title} "
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
    def _help_text() -> str:
        return (
            "可用命令:\n"
            "/help\n"
            "/version\n"
            "/profile refresh\n"
            "/history list [--limit <>=1>]\n"
            "/history search <关键词> [--limit <>=1>]\n"
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
            "[--tag <标签>] "
            "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
            "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]\n"
            "/schedule get <id>\n"
            "/schedule view <day|week|month> [YYYY-MM-DD|YYYY-MM] [--tag <标签>]\n"
            "/schedule update <id> <YYYY-MM-DD HH:MM> <标题> "
            "[--tag <标签>] "
            "[--duration <>=1>] [--remind <YYYY-MM-DD HH:MM>] "
            "[--interval <>=1>] [--times <-1|>=2>] [--remind-start <YYYY-MM-DD HH:MM>]\n"
            "/schedule repeat <id> <on|off>\n"
            "/schedule delete <id>\n"
            "/schedule list [--tag <标签>]\n"
            "你也可以直接说自然语言（会走 plan -> thought -> act -> observe -> replan 循环）。\n"
            "当前版本仅支持计划链路，不再走 chat 直聊分支。"
        )


def _parse_positive_int(raw: str) -> int | None:
    if not raw.isdigit():
        return None
    value = int(raw)
    if value <= 0:
        return None
    return value


def _parse_history_list_limit(command: str) -> int | None:
    if command == "/history list":
        return DEFAULT_HISTORY_LIST_LIMIT
    raw = command.removeprefix("/history list").strip()
    if not raw:
        return DEFAULT_HISTORY_LIST_LIMIT
    parts = raw.split()
    if len(parts) != 2 or parts[0] != "--limit":
        return None
    parsed = _parse_positive_int(parts[1])
    if parsed is None:
        return None
    return min(parsed, MAX_HISTORY_LIST_LIMIT)


def _parse_history_search_input(raw: str) -> tuple[str, int] | None:
    text = raw.strip()
    if not text:
        return None
    working = text
    limit = DEFAULT_HISTORY_LIST_LIMIT
    limit_match = HISTORY_LIMIT_OPTION_PATTERN.search(working)
    if limit_match:
        parsed_limit = _parse_positive_int(limit_match.group(2))
        if parsed_limit is None:
            return None
        limit = min(parsed_limit, MAX_HISTORY_LIST_LIMIT)
        working = _remove_option_span(working, limit_match.span())
    keyword = re.sub(r"\s+", " ", working).strip()
    if not keyword:
        return None
    return keyword, limit


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
) -> tuple[str, str, str, int, str | None, int | None, int, str | None] | None:
    parsed = _parse_schedule_input(raw, default_tag="default", default_duration_minutes=60)
    if parsed is None:
        return None
    (
        event_time,
        title,
        tag,
        _has_tag,
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
        tag,
        duration_minutes,
        remind_at,
        repeat_interval_minutes,
        repeat_times,
        repeat_remind_start_time,
    )


def _parse_schedule_update_input(
    raw: str,
) -> tuple[int, str, str, str | None, bool, int | None, str | None, bool, int | None, int, str | None, bool] | None:
    parts = raw.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    schedule_id = _parse_positive_int(parts[0])
    if schedule_id is None:
        return None
    parsed = _parse_schedule_input(parts[1], default_tag=None, default_duration_minutes=None)
    if parsed is None:
        return None
    (
        event_time,
        title,
        tag,
        has_tag,
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
        tag,
        has_tag,
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
    default_tag: str | None,
    default_duration_minutes: int | None,
) -> tuple[str, str, str | None, bool, int | None, str | None, bool, int | None, int, str | None, bool] | None:
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
    tag: str | None = default_tag
    has_tag = False
    remind_at: str | None = None
    has_remind = False
    repeat_interval_minutes: int | None = None
    repeat_times = 1
    has_repeat_times = False
    repeat_remind_start_time: str | None = None
    has_repeat_remind_start_time = False

    tag_match = TODO_TAG_OPTION_PATTERN.search(working)
    if tag_match:
        parsed_tag = _sanitize_tag(tag_match.group(2))
        if not parsed_tag:
            return None
        tag = parsed_tag
        has_tag = True
        working = _remove_option_span(working, tag_match.span())

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
    if re.search(r"(^|\s)--(tag|duration|interval|times|remind|remind-start)\b", title):
        return None
    return (
        event_time,
        title,
        tag,
        has_tag,
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


def _parse_schedule_list_tag_input(raw: str) -> str | None | object:
    text = raw.strip()
    if not text:
        return None
    option_match = re.fullmatch(r"--tag\s+(\S+)", text)
    if option_match is None:
        return _INVALID_OPTION_VALUE
    tag = _sanitize_tag(option_match.group(1))
    if tag is None:
        return _INVALID_OPTION_VALUE
    return tag


def _parse_schedule_view_command_input(raw: str) -> tuple[str, str | None, str | None] | None:
    text = raw.strip()
    if not text:
        return None

    working = text
    tag: str | None = None

    tag_match = TODO_TAG_OPTION_PATTERN.search(working)
    if tag_match:
        parsed_tag = _sanitize_tag(tag_match.group(2))
        if not parsed_tag:
            return None
        tag = parsed_tag
        working = _remove_option_span(working, tag_match.span())

    if re.search(r"(^|\s)--tag\b", working):
        return None

    view_parsed = _parse_schedule_view_input(working.strip())
    if view_parsed is None:
        return None
    view_name, anchor = view_parsed
    return view_name, anchor, tag


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


_INVALID_OPTION_VALUE = object()


def _normalize_optional_datetime_value(value: Any, *, key_present: bool) -> str | None | object:
    if not key_present:
        return None
    if value is None:
        return None
    normalized = _normalize_datetime_text(str(value))
    if normalized is None:
        return _INVALID_OPTION_VALUE
    return normalized


def _normalize_positive_int_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        return parsed if parsed > 0 else None
    if value is None:
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    parsed = int(text)
    if parsed <= 0:
        return None
    return parsed


def _normalize_todo_tag_value(value: Any) -> str | None:
    if value is None:
        return None
    return _sanitize_tag(str(value))


def _normalize_schedule_tag_value(value: Any) -> str | None:
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
        return value if value >= 2 else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
        if parsed == -1:
            return -1
        return parsed if parsed >= 2 else None
    text = str(value).strip()
    if not re.fullmatch(r"-?\d+", text):
        return None
    parsed = int(text)
    if parsed == -1:
        return -1
    if parsed < 2:
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


def _now_time_text() -> str:
    return datetime.now().replace(microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def _is_valid_datetime_text(value: str) -> bool:
    return _normalize_datetime_text(value) is not None


def _remove_option_span(text: str, span: tuple[int, int]) -> str:
    start, end = span
    return (text[:start] + " " + text[end:]).strip()


def _todo_table_rows(todos: list[Any]) -> list[list[str]]:
    return [
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


def _render_todo_table(todos: list[Any]) -> str:
    return _render_table(headers=TODO_TABLE_HEADERS, rows=_todo_table_rows(todos))


def _todo_list_empty_text(*, tag: str | None, view_name: str) -> str:
    if tag is None and view_name == "all":
        return "暂无待办。"
    if tag is None:
        return f"视图 {view_name} 下暂无待办。"
    if view_name == "all":
        return f"标签 {tag} 下暂无待办。"
    return f"标签 {tag} 的 {view_name} 视图下暂无待办。"


def _todo_list_header(*, tag: str | None, view_name: str) -> str:
    header_parts: list[str] = []
    if tag is not None:
        header_parts.append(f"标签: {tag}")
    if view_name != "all":
        header_parts.append(f"视图: {view_name}")
    if not header_parts:
        return "待办列表:"
    return f"待办列表({', '.join(header_parts)}):"


def _todo_search_empty_text(*, keyword: str, tag: str | None) -> str:
    if tag is None:
        return f"未找到包含“{keyword}”的待办。"
    return f"未在标签 {tag} 下找到包含“{keyword}”的待办。"


def _todo_search_header(*, keyword: str, tag: str | None) -> str:
    if tag is None:
        return f"搜索结果(关键词: {keyword}):"
    return f"搜索结果(关键词: {keyword}, 标签: {tag}):"


def _schedule_list_empty_text(*, window_days: int, tag: str | None) -> str:
    if tag:
        return f"前天起未来 {window_days} 天内（标签:{tag}）暂无日程。"
    return f"前天起未来 {window_days} 天内暂无日程。"


def _schedule_list_title(*, window_days: int, tag: str | None) -> str:
    title_suffix = f"，标签:{tag}" if tag else ""
    return f"日程列表(前天起未来 {window_days} 天{title_suffix})"


def _schedule_view_title(*, view_name: str, anchor: str | None, tag: str | None) -> str:
    title = f"日历视图({view_name}, {anchor})" if anchor else f"日历视图({view_name})"
    if tag:
        title = f"{title} [标签:{tag}]"
    return title


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


def _schedule_table_headers() -> list[str]:
    return [
        "ID",
        "时间",
        "时长(分钟)",
        "标签",
        "标题",
        "提醒时间",
        "重复提醒开始",
        "重复间隔(分钟)",
        "重复次数",
        "重复启用",
        "创建时间",
    ]


def _schedule_table_rows(items: list[Any]) -> list[list[str]]:
    return [
        [
            str(item.id),
            item.event_time,
            str(item.duration_minutes),
            item.tag,
            item.title,
            item.remind_at or "-",
            item.repeat_remind_start_time or "-",
            str(item.repeat_interval_minutes) if item.repeat_interval_minutes is not None else "-",
            str(item.repeat_times) if item.repeat_times is not None else "-",
            _repeat_enabled_text(item.repeat_enabled),
            item.created_at,
        ]
        for item in items
    ]


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


def _is_planner_command_success(result: str, *, tool: str) -> bool:
    text = result.strip()
    if not text:
        return False

    if text.startswith("用法:") or text.startswith("未知命令"):
        return False

    if tool == "todo":
        if text.startswith("未找到待办 #") or text.startswith("提醒时间需要"):
            return False
    elif tool == "schedule":
        if text.startswith("未找到日程 #") or "没有可切换的重复规则" in text:
            return False
    elif tool == "history_search":
        if text.startswith("未找到包含") or text.startswith("暂无历史会话"):
            return False

    return True


def _format_search_results(results: list[SearchResult], *, top_k: int) -> str:
    limit = max(top_k, 1)
    lines = [f"互联网搜索结果（Top {limit}）:"]
    for index, item in enumerate(results[:limit], start=1):
        snippet = item.snippet or "-"
        lines.append(f"{index}. {item.title}")
        lines.append(f"   摘要: {snippet}")
        lines.append(f"   链接: {item.url}")
    return "\n".join(lines)
