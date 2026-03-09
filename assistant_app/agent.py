from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from assistant_app.agent_components.command_handlers import (
    handle_command as _handle_command_impl,
)
from assistant_app.agent_components.command_handlers import (
    help_text as _help_text_impl,
)
from assistant_app.agent_components.models import (
    ClarificationTurn,
    CompletedSubtask,
    InnerReActContext,
    OuterPlanContext,
    PendingPlanTask,
    PlannerObservation,
    PlannerTextMessage,
    PlannerToolMessage,
    PlanStep,
    TaskInterruptedError,
    ThoughtMessage,
    ThoughtToolCallingError,
)
from assistant_app.agent_components.parsing_utils import (
    _parse_history_list_limit,
    _parse_history_search_input,
)
from assistant_app.agent_components.planner_loop import (
    emit_decision_progress as _emit_decision_progress_impl,
)
from assistant_app.agent_components.planner_loop import (
    initialize_plan_once as _initialize_plan_once_impl,
)
from assistant_app.agent_components.planner_loop import (
    run_inner_react_loop as _run_inner_react_loop_impl,
)
from assistant_app.agent_components.planner_loop import (
    run_outer_plan_loop as _run_outer_plan_loop_impl,
)
from assistant_app.agent_components.planner_loop import (
    run_replan_gate as _run_replan_gate_impl,
)
from assistant_app.agent_components.render_helpers import (
    _strip_think_blocks,
    _truncate_text,
    _try_parse_json,
)
from assistant_app.agent_components.tools.history import (
    execute_history_system_action as _execute_history_system_action_impl,
)
from assistant_app.agent_components.tools.internet_search import (
    execute_internet_search_planner_action as _execute_internet_search_planner_action_impl,
)
from assistant_app.agent_components.tools.planner_tool_routing import (
    JsonPlannerToolRoute,
    build_json_planner_tool_executor,
)
from assistant_app.agent_components.tools.schedule import (
    execute_schedule_system_action as _execute_schedule_system_action_impl,
)
from assistant_app.agent_components.tools.thoughts import (
    execute_thoughts_system_action as _execute_thoughts_system_action_impl,
)
from assistant_app.db import AssistantDB, ChatTurn, ScheduleItem
from assistant_app.llm import LLMClient
from assistant_app.planner_plan_replan import (
    PLAN_ONCE_PROMPT,
    REPLAN_PROMPT,
    normalize_plan_decision,
    normalize_replan_decision,
)
from assistant_app.planner_thought import (
    THOUGHT_PROMPT,
    build_thought_tool_schemas,
    normalize_thought_decision,
    normalize_thought_tool_call,
    resolve_current_subtask_tool_names,
)
from assistant_app.schemas.planner import (
    AssistantToolMessage,
    PlanResponsePayload,
    ReplanResponsePayload,
    ThoughtAskUserDecision,
    ThoughtContinueDecision,
    ThoughtDecision,
    ThoughtDoneDecision,
    ThoughtResponsePayload,
)
from assistant_app.search import BingSearchProvider, SearchProvider, fetch_webpage_main_text

__all__ = [
    "AssistantAgent",
    "_parse_history_list_limit",
    "_parse_history_search_input",
    "_strip_think_blocks",
    "_try_parse_json",
]

DEFAULT_PLAN_REPLAN_MAX_STEPS = 100
DEFAULT_PLAN_REPLAN_RETRY_COUNT = 3
DEFAULT_PLAN_OBSERVATION_CHAR_LIMIT = 10000
DEFAULT_PLAN_OBSERVATION_HISTORY_LIMIT = 100
DEFAULT_PLAN_CONTINUOUS_FAILURE_LIMIT = 3
DEFAULT_TASK_CANCEL_COMMAND = "取消当前任务"
DEFAULT_INTERNET_SEARCH_TOP_K = 3
DEFAULT_SCHEDULE_MAX_WINDOW_DAYS = 31
UNKNOWN_APP_VERSION = "unknown"
PLAN_HISTORY_LOOKBACK_HOURS = 24
PLAN_HISTORY_MAX_TURNS = 50
DEFAULT_USER_PROFILE_MAX_CHARS = 6000
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DecisionT = TypeVar("DecisionT")
PayloadT = TypeVar("PayloadT")

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
        schedule_sync_service: Any | None = None,
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
        self._skip_history_once = False
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
        self._schedule_sync_service = schedule_sync_service
        self._planner_tool_routes = self._build_planner_tool_routes()

    def set_progress_callback(self, callback: Callable[[str], None] | None) -> None:
        self._progress_callback = callback

    def set_user_profile_refresh_runner(self, runner: Callable[[], str] | None) -> None:
        self._user_profile_refresh_runner = runner

    def set_subtask_result_callback(self, callback: Callable[[str], None] | None) -> None:
        self._subtask_result_callback = callback

    def set_schedule_sync_service(self, service: Any | None) -> None:
        self._schedule_sync_service = service

    def notify_schedule_added(self, schedule_id: int) -> None:
        self._notify_schedule_sync(action="add", schedule_id=schedule_id)

    def notify_schedule_updated(self, schedule_id: int, old_schedule: ScheduleItem | None = None) -> None:
        self._notify_schedule_sync(action="update", schedule_id=schedule_id, schedule_snapshot=old_schedule)

    def notify_schedule_deleted(self, schedule_id: int, deleted_schedule: ScheduleItem | None = None) -> None:
        self._notify_schedule_sync(action="delete", schedule_id=schedule_id, schedule_snapshot=deleted_schedule)

    def _notify_schedule_sync(
        self,
        *,
        action: str,
        schedule_id: int,
        schedule_snapshot: ScheduleItem | None = None,
    ) -> None:
        service = self._schedule_sync_service
        if service is None:
            return
        method_name = {
            "add": "on_local_schedule_added",
            "update": "on_local_schedule_updated",
            "delete": "on_local_schedule_deleted",
        }.get(action, "")
        if not method_name:
            return
        callback = getattr(service, method_name, None)
        if not callable(callback):
            return
        try:
            if action == "update":
                callback(schedule_id=schedule_id, old_schedule=schedule_snapshot)
                return
            if action == "delete":
                callback(schedule_id=schedule_id, deleted_schedule=schedule_snapshot)
                return
            callback(schedule_id=schedule_id)
        except Exception as exc:  # noqa: BLE001
            self._app_logger.warning(
                "schedule sync notify failed",
                extra={
                    "event": "schedule_sync_notify_failed",
                    "context": {
                        "action": action,
                        "schedule_id": schedule_id,
                        "has_schedule_snapshot": schedule_snapshot is not None,
                        "error": repr(exc),
                    },
                },
            )

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
            thought_messages=[PlannerTextMessage(role="system", content=THOUGHT_PROMPT), *deepcopy(outer_messages)],
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
        self._skip_history_once = False
        self._clear_interrupt_request()
        response = self._handle_input_text(text)
        if not text.startswith("/"):
            if self._skip_history_once:
                self._app_logger.info(
                    "chat history skipped",
                    extra={
                        "event": "chat_history_skipped",
                        "context": {"reason": "plan_ack_only"},
                    },
                )
            else:
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

    @staticmethod
    def _message_to_payload(
        message: PlannerTextMessage | AssistantToolMessage | PlannerToolMessage,
    ) -> dict[str, Any]:
        return message.model_dump(exclude_none=True)

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
        return _run_outer_plan_loop_impl(self, task)

    def _emit_decision_progress(self, task: PendingPlanTask) -> None:
        _emit_decision_progress_impl(self, task)

    def _initialize_plan_once(self, task: PendingPlanTask) -> bool:
        return _initialize_plan_once_impl(self, task)

    def _run_replan_gate(self, task: PendingPlanTask) -> tuple[str, str | None]:
        return _run_replan_gate_impl(self, task)

    def _run_inner_react_loop(self, task: PendingPlanTask) -> tuple[str, str | None]:
        return _run_inner_react_loop_impl(self, task)

    def _request_plan_payload(self, task: PendingPlanTask) -> PlanResponsePayload | None:
        if not self.llm_client:
            return None

        planner_messages = self._build_plan_messages(task)
        payload = self._request_payload_with_retry(
            planner_messages,
            normalize_plan_decision,
            lambda decision, raw_response: PlanResponsePayload(
                decision=decision,
                raw_response=raw_response,
            ),
        )
        if payload is None:
            return None
        raw_user_message = planner_messages[-1].get("content")
        if isinstance(raw_user_message, str):
            self._append_outer_message_turn(
                task=task,
                user_message_content=raw_user_message,
                assistant_response=payload.raw_response,
            )
        return payload

    def _request_thought_payload(self, task: PendingPlanTask) -> ThoughtResponsePayload | None:
        if not self.llm_client:
            return None

        planner_messages = self._ensure_thought_messages(task)
        context_payload = self._build_thought_context(task)
        context_payload["phase"] = "thought"
        context_payload["current_plan_item"] = self._current_plan_item_text(task)
        planner_messages.append(
            PlannerTextMessage(role="user", content=json.dumps(context_payload, ensure_ascii=False))
        )
        request_messages = [self._message_to_payload(item) for item in deepcopy(planner_messages)]
        payload = self._request_thought_payload_with_retry(task, request_messages)
        if payload is None:
            return None
        if payload.assistant_message is not None:
            self._append_thought_assistant_message(task, payload.assistant_message)
        else:
            # Backward-compatible path for clients without tool-calling support.
            self._append_thought_decision_message(task, payload.decision)
        return payload

    def _request_replan_payload(self, task: PendingPlanTask) -> ReplanResponsePayload | None:
        if not self.llm_client:
            return None

        planner_messages = self._build_replan_messages(task)
        payload = self._request_payload_with_retry(
            planner_messages,
            normalize_replan_decision,
            lambda decision, raw_response: ReplanResponsePayload(
                decision=decision,
                raw_response=raw_response,
            ),
        )
        if payload is None:
            return None
        raw_user_message = planner_messages[-1].get("content")
        if isinstance(raw_user_message, str):
            self._append_outer_message_turn(
                task=task,
                user_message_content=raw_user_message,
                assistant_response=payload.raw_response,
            )
        return payload

    def _request_payload_with_retry(
        self,
        messages: list[dict[str, Any]],
        normalizer: Callable[[dict[str, Any]], DecisionT | None],
        payload_builder: Callable[[DecisionT, str], PayloadT],
    ) -> PayloadT | None:
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
                return payload_builder(decision, raw)
        return None

    def _request_thought_payload_with_retry(
        self,
        task: PendingPlanTask,
        messages: list[dict[str, Any]],
    ) -> ThoughtResponsePayload | None:
        max_attempts = 1 + self._plan_replan_retry_count
        phase = self._llm_trace_phase(messages)
        thought_tool_names = self._current_thought_tool_names(task)
        thought_tool_schemas = build_thought_tool_schemas(thought_tool_names)
        allowed_tool_names = set(thought_tool_names)
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
                    "tools": thought_tool_schemas,
                }
            )
            try:
                response = self._llm_reply_for_thought(
                    messages,
                    thought_tool_schemas=thought_tool_schemas,
                    allowed_tool_names=allowed_tool_names,
                )
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
            if decision is None:
                continue

            payload: dict[str, Any] = {"decision": decision}
            assistant_message = response.get("assistant_message")
            if isinstance(assistant_message, dict):
                payload["assistant_message"] = assistant_message
            tool_call_id = response.get("tool_call_id")
            if isinstance(tool_call_id, str) and tool_call_id.strip():
                payload["tool_call_id"] = tool_call_id.strip()
            try:
                return ThoughtResponsePayload.model_validate(payload)
            except ValidationError:
                continue
        return None

    def _build_plan_messages(self, task: PendingPlanTask) -> list[dict[str, str]]:
        outer_messages = deepcopy(self._ensure_outer_messages(task))
        context_payload = self._build_planner_context(task)
        context_payload["phase"] = "plan"
        messages = [PlannerTextMessage(role="system", content=PLAN_ONCE_PROMPT), *outer_messages]
        messages.append(PlannerTextMessage(role="user", content=json.dumps(context_payload, ensure_ascii=False)))
        return [self._message_to_payload(item) for item in messages]

    def _ensure_thought_messages(self, task: PendingPlanTask) -> list[ThoughtMessage]:
        inner = self._ensure_inner_context(task)
        if inner.thought_messages:
            return inner.thought_messages
        outer_messages = deepcopy(self._ensure_outer_messages(task))
        inner.thought_messages = [PlannerTextMessage(role="system", content=THOUGHT_PROMPT), *outer_messages]
        return inner.thought_messages

    def _append_thought_assistant_message(
        self,
        task: PendingPlanTask,
        assistant_message: AssistantToolMessage,
    ) -> None:
        messages = self._ensure_thought_messages(task)
        messages.append(assistant_message.model_copy(deep=True))

    def _append_thought_decision_message(self, task: PendingPlanTask, decision: ThoughtDecision) -> None:
        messages = self._ensure_thought_messages(task)
        decision_payload = {
            "phase": "thought_decision",
            "decision": decision.model_dump(exclude_none=True),
        }
        messages.append(PlannerTextMessage(role="assistant", content=json.dumps(decision_payload, ensure_ascii=False)))

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
            PlannerToolMessage(
                tool_call_id=tool_call_id,
                content=json.dumps(tool_payload, ensure_ascii=False),
            )
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
        messages.append(PlannerTextMessage(role="user", content=json.dumps(observation_payload, ensure_ascii=False)))

    def _build_replan_messages(self, task: PendingPlanTask) -> list[dict[str, str]]:
        outer_messages = deepcopy(self._ensure_outer_messages(task))
        context_payload = self._build_planner_context(task)
        context_payload["phase"] = "replan"
        context_payload["current_plan_item"] = self._current_plan_item_text(task)
        messages = [PlannerTextMessage(role="system", content=REPLAN_PROMPT), *outer_messages]
        messages.append(PlannerTextMessage(role="user", content=json.dumps(context_payload, ensure_ascii=False)))
        return [self._message_to_payload(item) for item in messages]

    def _ensure_outer_messages(self, task: PendingPlanTask) -> list[PlannerTextMessage]:
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
        outer_messages.append(PlannerTextMessage(role="user", content=user_message_content))
        outer_messages.append(PlannerTextMessage(role="assistant", content=assistant_response))

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
        current_tools = self._current_thought_tool_names(task)
        current_subtask: dict[str, Any] = {
            "item": inner.current_subtask,
            "index": outer.current_plan_index + 1,
            "total": len(outer.latest_plan),
            "tools": current_tools,
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
                "tools": item.tools,
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
    def _serialize_chat_turns_as_messages(chat_turns: list[ChatTurn]) -> list[PlannerTextMessage]:
        history_messages: list[PlannerTextMessage] = []
        for item in chat_turns:
            if item.user_content.strip():
                history_messages.append(PlannerTextMessage(role="user", content=item.user_content))
            if item.assistant_content.strip():
                history_messages.append(PlannerTextMessage(role="assistant", content=item.assistant_content))
        return history_messages

    @staticmethod
    def _current_plan_item_text(task: PendingPlanTask) -> str:
        step = AssistantAgent._current_plan_step(task)
        if step is None:
            return ""
        return step.item

    @staticmethod
    def _current_plan_step(task: PendingPlanTask) -> PlanStep | None:
        if task.outer_context is None:
            return None
        if not task.outer_context.latest_plan:
            return None
        if task.outer_context.current_plan_index < 0:
            return None
        if task.outer_context.current_plan_index >= len(task.outer_context.latest_plan):
            return None
        return task.outer_context.latest_plan[task.outer_context.current_plan_index]

    def _current_thought_tool_names(self, task: PendingPlanTask) -> list[str]:
        current_step = self._current_plan_step(task)
        raw_tools: Any = []
        if current_step is not None:
            raw_tools = current_step.tools
        return resolve_current_subtask_tool_names(raw_tools)

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

    def _llm_reply_for_thought(
        self,
        messages: list[dict[str, Any]],
        *,
        thought_tool_schemas: list[dict[str, Any]],
        allowed_tool_names: set[str],
    ) -> dict[str, Any]:
        if self.llm_client is None:
            return {}

        reply_with_tools = getattr(self.llm_client, "reply_with_tools", None)
        if not callable(reply_with_tools):
            raw = self._llm_reply_for_planner(messages)
            parsed_response = _try_parse_json(_strip_think_blocks(raw).strip())
            if not isinstance(parsed_response, dict):
                return {}
            decision = normalize_thought_decision(parsed_response)
            if decision is None:
                return {}
            if not self._is_thought_decision_tool_allowed(decision, allowed_tool_names):
                return {}
            return {"decision": decision}

        try:
            tool_response = reply_with_tools(messages, tools=thought_tool_schemas, tool_choice="auto")
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

        response_payload: dict[str, Any] = {"assistant_message": assistant_message}
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
            if not self._is_thought_decision_tool_allowed(decision, allowed_tool_names):
                return {}
            response_payload["decision"] = decision
            call_id = str(first_tool_call.get("id") or "").strip()
            if call_id:
                response_payload["tool_call_id"] = call_id
            return response_payload

        raw_content = assistant_message.get("content")
        content = str(raw_content or "").strip()
        parsed_content = _try_parse_json(_strip_think_blocks(content))
        if not isinstance(parsed_content, dict):
            return {}
        decision = normalize_thought_decision(parsed_content)
        if decision is None:
            return {}
        if not self._is_thought_decision_tool_allowed(decision, allowed_tool_names):
            return {}
        response_payload["decision"] = decision
        return response_payload

    @staticmethod
    def _is_thought_decision_tool_allowed(decision: ThoughtDecision, allowed_tool_names: set[str]) -> bool:
        if isinstance(decision, ThoughtContinueDecision):
            return decision.next_action.tool in allowed_tool_names
        if isinstance(decision, ThoughtAskUserDecision):
            return "ask_user" in allowed_tool_names
        if isinstance(decision, ThoughtDoneDecision):
            return "done" in allowed_tool_names
        return False

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
        handler = self._planner_tool_routes.get(action_tool)
        if handler is None:
            return PlannerObservation(
                tool=action_tool or "unknown",
                input_text=action_input,
                ok=False,
                result=f"未知工具: {action_tool}",
            )
        return handler(action_input)

    def _build_planner_tool_routes(self) -> dict[str, Callable[[str], PlannerObservation]]:
        json_routes: dict[str, JsonPlannerToolRoute] = {
            "schedule": JsonPlannerToolRoute(
                tool="schedule",
                invalid_json_result="schedule 工具参数无效：需要 JSON 对象。",
                legacy_command_prefix="/schedule",
                payload_executor=lambda payload, raw_input: self._execute_schedule_system_action(
                    payload, raw_input=raw_input
                ),
            ),
            "history": JsonPlannerToolRoute(
                tool="history",
                invalid_json_result="history 工具参数无效：需要 JSON 对象。",
                legacy_command_prefix="/history",
                payload_executor=lambda payload, raw_input: self._execute_history_system_action(
                    payload, raw_input=raw_input
                ),
            ),
            "history_search": JsonPlannerToolRoute(
                tool="history_search",
                invalid_json_result="history_search 工具参数无效：需要 JSON 对象。",
                legacy_command_prefix="/history search",
                compat_action="search",
                payload_executor=lambda payload, raw_input: self._execute_history_system_action(
                    payload, raw_input=raw_input, observation_tool="history_search"
                ),
            ),
            "thoughts": JsonPlannerToolRoute(
                tool="thoughts",
                invalid_json_result="thoughts 工具参数无效：需要 JSON 对象。",
                legacy_command_prefix="/thoughts",
                payload_executor=lambda payload, raw_input: self._execute_thoughts_system_action(
                    payload, raw_input=raw_input
                ),
            ),
        }
        routes = {
            name: build_json_planner_tool_executor(route=route, command_executor=self._handle_command)
            for name, route in json_routes.items()
        }
        routes["internet_search"] = lambda action_input: _execute_internet_search_planner_action_impl(
            self,
            action_input=action_input,
            fetch_main_text=fetch_webpage_main_text,
        )
        return routes

    def _execute_schedule_system_action(self, payload: dict[str, Any], *, raw_input: str) -> PlannerObservation:
        return _execute_schedule_system_action_impl(self, payload, raw_input=raw_input)

    def _execute_history_system_action(
        self,
        payload: dict[str, Any],
        *,
        raw_input: str,
        observation_tool: str = "history",
    ) -> PlannerObservation:
        return _execute_history_system_action_impl(
            self,
            payload,
            raw_input=raw_input,
            observation_tool=observation_tool,
        )

    def _execute_thoughts_system_action(
        self,
        payload: dict[str, Any],
        *,
        raw_input: str,
    ) -> PlannerObservation:
        return _execute_thoughts_system_action_impl(
            self,
            payload,
            raw_input=raw_input,
        )

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
        decision: Any,
    ) -> None:
        normalized_phase = phase.strip().lower()
        if normalized_phase not in {"plan", "thought", "replan"}:
            normalized_phase = "planner"
        if hasattr(decision, "model_dump"):
            serialized = decision.model_dump(exclude_none=True)
        elif isinstance(decision, dict):
            serialized = decision
        else:
            serialized = {"value": str(decision)}
        result = _truncate_text(
            json.dumps(serialized, ensure_ascii=False, separators=(",", ":")),
            self._plan_observation_char_limit,
        )
        status = normalized_phase
        if isinstance(serialized, dict):
            status = str(serialized.get("status") or normalized_phase).strip() or normalized_phase
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
            "搜索结果",
            "日程列表",
            "日历视图(",
            "日程详情",
            "想法列表",
            "想法详情",
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
            "- 或直接使用 /schedule 或 /thoughts 命令完成关键操作。"
        )

    def _finalize_planner_task(self, task: PendingPlanTask, response: str) -> str:
        if self._pending_plan_task is task:
            self._pending_plan_task = None
        self._last_task_completed = True
        if task.plan_ack_only:
            self._skip_history_once = True
            self._app_logger.info(
                "plan ack-only completed",
                extra={
                    "event": "plan_ack_only_completed",
                    "context": {"goal": task.goal},
                },
            )
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
        return "抱歉，当前计划执行服务暂时不可用。你可以稍后重试，或先使用 /schedule 或 /thoughts 命令继续操作。"

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
        signature = tuple((step.item, step.completed, tuple(step.tools)) for step in outer.latest_plan)
        if task.last_reported_plan_signature == signature:
            return
        task.last_reported_plan_signature = signature
        lines = ["计划列表："]
        for idx, step in enumerate(outer.latest_plan, start=1):
            status = "完成" if step.completed else "未完成"
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
        return _handle_command_impl(self, command)

    @staticmethod
    def _help_text() -> str:
        return _help_text_impl()
