from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from assistant_app.agent_components.command_handlers import (
    handle_command as _handle_command_impl,
)
from assistant_app.agent_components.command_handlers import (
    help_text as _help_text_impl,
)
from assistant_app.agent_components.models import (
    ClarificationTurn,
    PendingPlanTask,
    PlannerObservation,
    TaskInterruptedError,
)
from assistant_app.agent_components.parsing_utils import (
    _parse_history_list_limit,
    _parse_history_search_input,
)
from assistant_app.agent_components.planner_loop import (
    run_outer_plan_loop as _run_outer_plan_loop_impl,
)
from assistant_app.agent_components.planner_payload_requester import (
    PlannerPayloadRequester,
)
from assistant_app.agent_components.planner_session import PlannerSession
from assistant_app.agent_components.planner_tool_executor import PlannerToolExecutor
from assistant_app.agent_components.render_helpers import (
    _strip_think_blocks,
    _try_parse_json,
)
from assistant_app.agent_components.tools.history import (
    execute_history_system_action as _execute_history_system_action_impl,
)
from assistant_app.agent_components.tools.internet_search import (
    execute_internet_search_planner_action as _execute_internet_search_planner_action_impl,
)
from assistant_app.agent_components.tools.schedule import (
    execute_schedule_system_action as _execute_schedule_system_action_impl,
)
from assistant_app.agent_components.tools.thoughts import (
    execute_thoughts_system_action as _execute_thoughts_system_action_impl,
)
from assistant_app.db import AssistantDB, ScheduleItem
from assistant_app.llm import LLMClient
from assistant_app.schemas.proactive import ProactiveExecutionResult
from assistant_app.schemas.routing import RuntimePlannerActionPayload
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
DEFAULT_USER_PROFILE_MAX_CHARS = 6000
UNKNOWN_APP_VERSION = "unknown"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
        proactive_notify_runner: Callable[[], ProactiveExecutionResult] | None = None,
        proactive_notify_target_open_id: str = "",
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

        self._pending_plan_task: PendingPlanTask | None = None
        self._last_task_completed = False
        self._skip_history_once = False
        self._interrupt_lock = threading.Lock()
        self._interrupt_requested = False

        self._plan_replan_max_steps = max(plan_replan_max_steps, 1)
        self._plan_continuous_failure_limit = max(plan_continuous_failure_limit, 1)
        self._task_cancel_command = task_cancel_command.strip() or DEFAULT_TASK_CANCEL_COMMAND
        self._internet_search_top_k = max(internet_search_top_k, 1)
        self._schedule_max_window_days = max(schedule_max_window_days, 1)
        normalized_retry_count = max(plan_replan_retry_count, 0)
        normalized_observation_char_limit = max(plan_observation_char_limit, 1)
        normalized_observation_history_limit = max(plan_observation_history_limit, 1)
        normalized_user_profile_max_chars = max(user_profile_max_chars, 1)

        self._planner_session = PlannerSession(
            db=self.db,
            app_logger=self._app_logger,
            plan_replan_max_steps=self._plan_replan_max_steps,
            plan_observation_char_limit=normalized_observation_char_limit,
            plan_observation_history_limit=normalized_observation_history_limit,
            user_profile_path=user_profile_path,
            user_profile_max_chars=normalized_user_profile_max_chars,
            project_root=PROJECT_ROOT,
            progress_callback=progress_callback,
        )
        self._planner_payload_requester = PlannerPayloadRequester(
            llm_client=self.llm_client,
            llm_trace_logger=self._llm_trace_logger,
            plan_replan_retry_count=normalized_retry_count,
            session=self._planner_session,
        )
        self._planner_tool_executor = PlannerToolExecutor(
            command_executor=self._handle_command,
            schedule_executor=lambda payload, raw_input: self._execute_schedule_system_action(
                payload,
                raw_input=raw_input,
            ),
            history_executor=lambda payload, raw_input: self._execute_history_system_action(
                payload,
                raw_input=raw_input,
            ),
            history_search_executor=lambda payload, raw_input: self._execute_history_system_action(
                payload,
                raw_input=raw_input,
                observation_tool="history_search",
            ),
            thoughts_executor=lambda payload, raw_input: self._execute_thoughts_system_action(
                payload,
                raw_input=raw_input,
            ),
            internet_search_executor=self._execute_internet_search_planner_action,
        )

        self._user_profile_refresh_runner = user_profile_refresh_runner
        self._proactive_notify_runner = proactive_notify_runner
        self._proactive_notify_target_open_id = proactive_notify_target_open_id.strip()
        self._final_response_rewriter = final_response_rewriter
        self._app_version = app_version.strip() or UNKNOWN_APP_VERSION
        self._schedule_sync_service = schedule_sync_service

    def set_progress_callback(self, callback: Callable[[str], None] | None) -> None:
        self._planner_session.set_progress_callback(callback)

    def set_user_profile_refresh_runner(self, runner: Callable[[], str] | None) -> None:
        self._user_profile_refresh_runner = runner

    def set_proactive_notify_runner(
        self,
        runner: Callable[[], ProactiveExecutionResult] | None,
        *,
        target_open_id: str = "",
    ) -> None:
        self._proactive_notify_runner = runner
        self._proactive_notify_target_open_id = target_open_id.strip()

    def set_subtask_result_callback(self, callback: Callable[[str], None] | None) -> None:
        self._planner_session.set_subtask_result_callback(callback)

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
            outer = self._planner_session.outer_context(pending_task)
            outer.clarification_history.append(ClarificationTurn(role="user_answer", content=text))
            pending_task.awaiting_clarification = False
            pending_task.needs_replan = True
            return _run_outer_plan_loop_impl(self, task=pending_task)

        return _run_outer_plan_loop_impl(self, task=PendingPlanTask(goal=text))

    def _save_turn_history(self, *, user_text: str, assistant_text: str) -> None:
        try:
            self.db.save_turn(user_content=user_text, assistant_content=assistant_text)
        except Exception:
            self._app_logger.warning(
                "failed to save chat history",
                extra={"event": "chat_history_save_failed"},
                exc_info=True,
            )

    def reload_user_profile(self) -> bool:
        return self._planner_session.reload_user_profile()

    def _serialize_user_profile(self) -> str | None:
        return self._planner_session.serialize_user_profile()

    def _execute_planner_tool(
        self,
        *,
        action_tool: str,
        action_input: str,
        action_payload: RuntimePlannerActionPayload | None = None,
    ) -> PlannerObservation:
        return self._planner_tool_executor.execute(
            action_tool=action_tool,
            action_input=action_input,
            action_payload=action_payload,
        )

    def _execute_schedule_system_action(
        self,
        payload: dict[str, Any] | RuntimePlannerActionPayload,
        *,
        raw_input: str,
    ) -> PlannerObservation:
        return _execute_schedule_system_action_impl(self, payload, raw_input=raw_input)

    def _execute_history_system_action(
        self,
        payload: dict[str, Any] | RuntimePlannerActionPayload,
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
        payload: dict[str, Any] | RuntimePlannerActionPayload,
        *,
        raw_input: str,
    ) -> PlannerObservation:
        return _execute_thoughts_system_action_impl(self, payload, raw_input=raw_input)

    def _execute_internet_search_planner_action(
        self,
        action_input: str,
        action_payload: RuntimePlannerActionPayload | None = None,
    ) -> PlannerObservation:
        return _execute_internet_search_planner_action_impl(
            self,
            action_input=action_input,
            action_payload=action_payload,
            fetch_main_text=fetch_webpage_main_text,
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
        self._planner_session.emit_progress("任务状态：已完成。")
        return response

    def _finalize_interrupted_task(self, task: PendingPlanTask) -> str:
        if self._pending_plan_task is task:
            self._pending_plan_task = None
        self._planner_session.emit_progress("任务状态：已中断。")
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

    def _raise_if_task_interrupted(self) -> None:
        if self._is_interrupt_requested():
            raise TaskInterruptedError("task interrupted by newer input")

    def _is_interrupt_requested(self) -> bool:
        with self._interrupt_lock:
            return self._interrupt_requested

    def _clear_interrupt_request(self) -> None:
        with self._interrupt_lock:
            self._interrupt_requested = False

    def _handle_command(self, command: str) -> str:
        return _handle_command_impl(self, command)

    @staticmethod
    def _help_text() -> str:
        return _help_text_impl()
