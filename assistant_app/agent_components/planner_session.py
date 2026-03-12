from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from pathlib import Path

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
    ThoughtMessage,
)
from assistant_app.agent_components.render_helpers import _truncate_text
from assistant_app.db import AssistantDB, ChatTurn
from assistant_app.planner_plan_replan import PLAN_ONCE_PROMPT, REPLAN_PROMPT
from assistant_app.planner_thought import THOUGHT_PROMPT, resolve_current_subtask_tool_names
from assistant_app.schemas.planner import (
    AssistantToolMessage,
    ClarificationTurnPayload,
    CompletedSubtaskPayload,
    ObservationPayload,
    PlannerContextPayload,
    PlanPromptPayload,
    PlanStepPayload,
    ReplanPromptPayload,
    ThoughtContextPayload,
    ThoughtCurrentSubtaskPayload,
    ThoughtDecision,
    ThoughtDecisionMessagePayload,
    ThoughtObservationMessagePayload,
    ThoughtPromptPayload,
)

PLAN_HISTORY_LOOKBACK_HOURS = 24
PLAN_HISTORY_MAX_TURNS = 50
PLANNER_UNAVAILABLE_TEXT = (
    "抱歉，当前计划执行服务暂时不可用。你可以稍后重试，"
    "或先使用 /schedule 或 /thoughts 命令继续操作。"
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PlannerSession:
    def __init__(
        self,
        *,
        db: AssistantDB,
        app_logger: logging.Logger,
        plan_replan_max_steps: int,
        plan_observation_char_limit: int,
        plan_observation_history_limit: int,
        user_profile_path: str,
        user_profile_max_chars: int,
        project_root: Path = PROJECT_ROOT,
        progress_callback: Callable[[str], None] | None = None,
        subtask_result_callback: Callable[[str], None] | None = None,
    ) -> None:
        self._db = db
        self._app_logger = app_logger
        self._plan_replan_max_steps = plan_replan_max_steps
        self._plan_observation_char_limit = plan_observation_char_limit
        self._plan_observation_history_limit = plan_observation_history_limit
        self._progress_callback = progress_callback
        self._subtask_result_callback = subtask_result_callback
        self._callback_lock = threading.Lock()
        self._user_profile_max_chars = user_profile_max_chars
        self._project_root = project_root
        self._user_profile_path, self._user_profile_content = self._load_user_profile(user_profile_path)

    def set_progress_callback(self, callback: Callable[[str], None] | None) -> None:
        with self._callback_lock:
            self._progress_callback = callback

    def set_subtask_result_callback(self, callback: Callable[[str], None] | None) -> None:
        with self._callback_lock:
            self._subtask_result_callback = callback

    @staticmethod
    def outer_context(task: PendingPlanTask) -> OuterPlanContext:
        if task.outer_context is None:
            task.outer_context = OuterPlanContext(goal=task.goal)
        return task.outer_context

    def new_inner_context(self, task: PendingPlanTask) -> InnerReActContext:
        outer = self.outer_context(task)
        outer_messages = self.ensure_outer_messages(task)
        return InnerReActContext(
            current_subtask=self.current_plan_item_text(task),
            completed_subtasks=[
                CompletedSubtask(item=item.item, result=item.result) for item in outer.completed_subtasks
            ],
            observations=[],
            thought_messages=[PlannerTextMessage(role="system", content=THOUGHT_PROMPT), *deepcopy(outer_messages)],
            response=None,
        )

    def ensure_inner_context(self, task: PendingPlanTask) -> InnerReActContext:
        if task.inner_context is None:
            task.inner_context = self.new_inner_context(task)
        inner_context = task.inner_context
        if inner_context is None:
            raise RuntimeError("planner inner context initialization failed")
        return inner_context

    @staticmethod
    def message_to_payload(
        message: PlannerTextMessage | AssistantToolMessage | PlannerToolMessage,
    ) -> dict[str, object]:
        return message.model_dump(exclude_none=True)

    def build_plan_messages(self, task: PendingPlanTask) -> list[dict[str, object]]:
        outer_messages = deepcopy(self.ensure_outer_messages(task))
        context_payload = PlanPromptPayload(
            phase="plan",
            **self.build_planner_context(task).model_dump(mode="python"),
        )
        messages = [PlannerTextMessage(role="system", content=PLAN_ONCE_PROMPT), *outer_messages]
        messages.append(
            PlannerTextMessage(
                role="user",
                content=json.dumps(context_payload.model_dump(mode="json"), ensure_ascii=False),
            )
        )
        return [self.message_to_payload(item) for item in messages]

    def build_thought_request_messages(self, task: PendingPlanTask) -> list[dict[str, object]]:
        planner_messages = self.ensure_thought_messages(task)
        context_payload = ThoughtPromptPayload(
            phase="thought",
            current_plan_item=self.current_plan_item_text(task),
            **self.build_thought_context(task).model_dump(mode="python"),
        )
        planner_messages.append(
            PlannerTextMessage(
                role="user",
                content=json.dumps(context_payload.model_dump(mode="json"), ensure_ascii=False),
            )
        )
        return [self.message_to_payload(item) for item in deepcopy(planner_messages)]

    def build_replan_messages(self, task: PendingPlanTask) -> list[dict[str, object]]:
        outer_messages = deepcopy(self.ensure_outer_messages(task))
        context_payload = ReplanPromptPayload(
            phase="replan",
            current_plan_item=self.current_plan_item_text(task),
            **self.build_planner_context(task).model_dump(mode="python"),
        )
        messages = [PlannerTextMessage(role="system", content=REPLAN_PROMPT), *outer_messages]
        messages.append(
            PlannerTextMessage(
                role="user",
                content=json.dumps(context_payload.model_dump(mode="json"), ensure_ascii=False),
            )
        )
        return [self.message_to_payload(item) for item in messages]

    def ensure_thought_messages(self, task: PendingPlanTask) -> list[ThoughtMessage]:
        inner = self.ensure_inner_context(task)
        if inner.thought_messages:
            return inner.thought_messages
        outer_messages = deepcopy(self.ensure_outer_messages(task))
        inner.thought_messages = [PlannerTextMessage(role="system", content=THOUGHT_PROMPT), *outer_messages]
        return inner.thought_messages

    def append_thought_assistant_message(
        self,
        task: PendingPlanTask,
        assistant_message: AssistantToolMessage,
    ) -> None:
        self.ensure_thought_messages(task).append(assistant_message.model_copy(deep=True))

    def append_thought_decision_message(self, task: PendingPlanTask, decision: ThoughtDecision) -> None:
        decision_payload = ThoughtDecisionMessagePayload(phase="thought_decision", decision=decision)
        self.ensure_thought_messages(task).append(
            PlannerTextMessage(
                role="assistant",
                content=json.dumps(decision_payload.model_dump(mode="json", exclude_none=True), ensure_ascii=False),
            )
        )

    def append_thought_tool_result_message(
        self,
        task: PendingPlanTask,
        *,
        observation: PlannerObservation,
        tool_call_id: str,
    ) -> None:
        tool_payload = self.serialize_observation(observation)
        self.ensure_thought_messages(task).append(
            PlannerToolMessage(
                tool_call_id=tool_call_id,
                content=json.dumps(tool_payload.model_dump(mode="json"), ensure_ascii=False),
            )
        )

    def append_thought_observation_message(
        self,
        task: PendingPlanTask,
        observation: PlannerObservation,
    ) -> None:
        observation_payload = ThoughtObservationMessagePayload(
            phase="thought_observation",
            observation=self.serialize_observation(observation),
        )
        self.ensure_thought_messages(task).append(
            PlannerTextMessage(
                role="user",
                content=json.dumps(observation_payload.model_dump(mode="json"), ensure_ascii=False),
            )
        )

    def ensure_outer_messages(self, task: PendingPlanTask) -> list[PlannerTextMessage]:
        outer = self.outer_context(task)
        if outer.outer_messages is not None:
            return outer.outer_messages
        recent_chat_turns = self._db.recent_turns_for_planner(
            lookback_hours=PLAN_HISTORY_LOOKBACK_HOURS,
            limit=PLAN_HISTORY_MAX_TURNS,
        )
        outer.outer_messages = self.serialize_chat_turns_as_messages(recent_chat_turns)
        return outer.outer_messages

    def append_outer_message_turn(
        self,
        *,
        task: PendingPlanTask,
        user_message_content: str,
        assistant_response: str,
    ) -> None:
        outer_messages = self.ensure_outer_messages(task)
        outer_messages.append(PlannerTextMessage(role="user", content=user_message_content))
        outer_messages.append(PlannerTextMessage(role="assistant", content=assistant_response))

    def build_planner_context(self, task: PendingPlanTask) -> PlannerContextPayload:
        outer = self.outer_context(task)
        return PlannerContextPayload(
            goal=outer.goal,
            clarification_history=self.serialize_clarification_history(outer.clarification_history),
            step_count=task.step_count,
            max_steps=self._plan_replan_max_steps,
            latest_plan=self.serialize_latest_plan(outer.latest_plan),
            current_plan_index=outer.current_plan_index,
            completed_subtasks=self.serialize_completed_subtasks(outer.completed_subtasks),
            user_profile=self.serialize_user_profile(),
            time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

    def build_thought_context(self, task: PendingPlanTask) -> ThoughtContextPayload:
        outer = self.outer_context(task)
        inner = self.ensure_inner_context(task)
        current_subtask_observations = self.serialize_observations(
            inner.observations[-self._plan_observation_history_limit :]
        )
        current_subtask = ThoughtCurrentSubtaskPayload(
            item=inner.current_subtask,
            index=outer.current_plan_index + 1 if inner.current_subtask else None,
            total=len(outer.latest_plan) if inner.current_subtask and outer.latest_plan else None,
            tools=self.current_thought_tool_names(task),
        )
        return ThoughtContextPayload(
            clarification_history=self.serialize_clarification_history(outer.clarification_history),
            step_count=task.step_count,
            max_steps=self._plan_replan_max_steps,
            current_subtask=current_subtask,
            completed_subtasks=self.serialize_completed_subtasks(inner.completed_subtasks),
            current_subtask_observations=current_subtask_observations,
            user_profile=self.serialize_user_profile(),
            time=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )

    @staticmethod
    def serialize_observation(observation: PlannerObservation) -> ObservationPayload:
        return ObservationPayload(
            tool=observation.tool,
            input=observation.input_text,
            ok=observation.ok,
            result=observation.result,
        )

    @staticmethod
    def serialize_observations(observations: list[PlannerObservation]) -> list[ObservationPayload]:
        return [PlannerSession.serialize_observation(item) for item in observations]

    @staticmethod
    def serialize_completed_subtasks(
        completed_subtasks: list[CompletedSubtask],
    ) -> list[CompletedSubtaskPayload]:
        return [CompletedSubtaskPayload(item=item.item, result=item.result) for item in completed_subtasks]

    @staticmethod
    def serialize_latest_plan(latest_plan: list[PlanStep]) -> list[PlanStepPayload]:
        return [PlanStepPayload(task=item.item, completed=item.completed, tools=item.tools) for item in latest_plan]

    def serialize_user_profile(self) -> str | None:
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

    def _resolve_user_profile_path(self, user_profile_path: str) -> Path:
        path = Path(user_profile_path).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (self._project_root / path).resolve()

    @staticmethod
    def serialize_clarification_history(
        clarification_history: list[ClarificationTurn],
    ) -> list[ClarificationTurnPayload]:
        return [ClarificationTurnPayload(role=item.role, content=item.content) for item in clarification_history]

    @staticmethod
    def serialize_chat_turns_as_messages(chat_turns: list[ChatTurn]) -> list[PlannerTextMessage]:
        history_messages: list[PlannerTextMessage] = []
        for item in chat_turns:
            if item.user_content.strip():
                history_messages.append(PlannerTextMessage(role="user", content=item.user_content))
            if item.assistant_content.strip():
                history_messages.append(PlannerTextMessage(role="assistant", content=item.assistant_content))
        return history_messages

    @staticmethod
    def current_plan_item_text(task: PendingPlanTask) -> str:
        step = PlannerSession.current_plan_step(task)
        if step is None:
            return ""
        return step.item

    @staticmethod
    def current_plan_step(task: PendingPlanTask) -> PlanStep | None:
        if task.outer_context is None:
            return None
        if not task.outer_context.latest_plan:
            return None
        if task.outer_context.current_plan_index < 0:
            return None
        if task.outer_context.current_plan_index >= len(task.outer_context.latest_plan):
            return None
        return task.outer_context.latest_plan[task.outer_context.current_plan_index]

    def current_thought_tool_names(self, task: PendingPlanTask) -> list[str]:
        current_step = self.current_plan_step(task)
        raw_tools: list[str] = current_step.tools if current_step is not None else []
        return resolve_current_subtask_tool_names(
            raw_tools,
            allow_ask_user=task.source == "interactive",
            allow_timer=task.source == "interactive",
        )

    @staticmethod
    def sync_current_plan_index(outer: OuterPlanContext) -> None:
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

    def append_observation(self, task: PendingPlanTask, observation: PlannerObservation) -> PlannerObservation:
        truncated = _truncate_text(observation.result, self._plan_observation_char_limit)
        normalized = PlannerObservation(
            tool=observation.tool,
            input_text=observation.input_text,
            ok=observation.ok,
            result=truncated,
        )
        task.observations.append(normalized)
        self.ensure_inner_context(task).observations.append(normalized)
        return normalized

    def append_planner_decision_observation(
        self,
        task: PendingPlanTask,
        *,
        phase: str,
        decision: object,
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
        self.append_observation(
            task,
            PlannerObservation(
                tool=normalized_phase,
                input_text=status,
                ok=True,
                result=result,
            ),
        )

    def append_completed_subtask(self, task: PendingPlanTask, *, item: str, result: str) -> None:
        normalized_item = item.strip() or "当前子任务"
        normalized_result = result.strip() or "子任务已完成。"
        self.outer_context(task).completed_subtasks.append(
            CompletedSubtask(
                item=normalized_item,
                result=_truncate_text(normalized_result, self._plan_observation_char_limit),
            )
        )

    def notify_replan_continue_subtask_result(self, task: PendingPlanTask) -> None:
        if task.source != "interactive":
            return
        callback = self._get_subtask_result_callback()
        if callback is None:
            return
        completed_subtasks = self.outer_context(task).completed_subtasks
        total_completed = len(completed_subtasks)
        if total_completed <= 0:
            return
        if task.last_notified_completed_subtask_count >= total_completed:
            return
        task.last_notified_completed_subtask_count = total_completed
        latest_item = completed_subtasks[-1].item.strip()
        if not latest_item:
            return
        try:
            callback(f"{latest_item}已完成")
        except Exception:
            self._app_logger.warning(
                "failed to notify replan continue subtask result",
                extra={"event": "replan_continue_subtask_notify_failed"},
                exc_info=True,
            )

    def notify_plan_goal_result(self, task: PendingPlanTask, expanded_goal: str) -> None:
        if task.source != "interactive":
            return
        callback = self._get_subtask_result_callback()
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
    def latest_success_observation_result(task: PendingPlanTask) -> str:
        llm_tools = {"plan", "thought", "replan"}
        for item in reversed(task.observations):
            if item.ok and item.tool not in llm_tools:
                return item.result
        return ""

    @staticmethod
    def merge_summary_with_detail(*, summary: str, detail: str) -> str:
        normalized_summary = summary.strip()
        normalized_detail = detail.strip()
        if not normalized_summary:
            return normalized_detail
        if not normalized_detail:
            return normalized_summary
        if normalized_detail in normalized_summary:
            return normalized_summary
        if not PlannerSession.is_structured_query_result(normalized_detail):
            return normalized_summary
        return f"{normalized_summary}\n\n执行结果：\n{normalized_detail}"

    @staticmethod
    def is_structured_query_result(result: str) -> bool:
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

    def planner_unavailable_text(self) -> str:
        return PLANNER_UNAVAILABLE_TEXT

    def format_step_limit_response(self, task: PendingPlanTask) -> str:
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

    def emit_progress(self, message: str) -> None:
        callback = self._get_progress_callback()
        if callback is None:
            return
        callback(message)

    def _get_progress_callback(self) -> Callable[[str], None] | None:
        with self._callback_lock:
            return self._progress_callback

    def _get_subtask_result_callback(self) -> Callable[[str], None] | None:
        with self._callback_lock:
            return self._subtask_result_callback

    def emit_plan_progress(self, task: PendingPlanTask) -> None:
        outer = self.outer_context(task)
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
        self.emit_progress("\n".join(lines))

    def emit_current_plan_item_progress(self, task: PendingPlanTask) -> None:
        outer = self.outer_context(task)
        if not outer.latest_plan:
            return
        if outer.current_plan_index < 0 or outer.current_plan_index >= len(outer.latest_plan):
            return
        index = outer.current_plan_index + 1
        total = len(outer.latest_plan)
        self.emit_progress(f"当前计划项：{index}/{total} - {outer.latest_plan[outer.current_plan_index].item}")

    @staticmethod
    def progress_total_text(task: PendingPlanTask) -> str:
        if task.outer_context is None or not task.outer_context.latest_plan:
            return "未定"
        return str(max(task.step_count, len(task.outer_context.latest_plan)))

    @staticmethod
    def current_plan_total_text(task: PendingPlanTask) -> str | None:
        if task.outer_context is None or not task.outer_context.latest_plan:
            return None
        return str(len(task.outer_context.latest_plan))
