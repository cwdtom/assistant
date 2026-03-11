from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from assistant_app.llm import LLMClient
from assistant_app.schemas.planner import AssistantToolMessage, ToolReplyPayload, parse_tool_reply_payload
from assistant_app.schemas.scheduled_tasks import (
    ScheduledTaskResultDecision,
    ScheduledTaskResultDecisionPromptPayload,
)
from assistant_app.schemas.tools import build_function_tool_schema, parse_json_object

SCHEDULED_RESULT_DECISION_SYSTEM_PROMPT = """
你是“定时任务结果发送决策器”。

目标：
- 基于定时任务的执行结果，判断是否值得向用户发送一条 Feishu 最终消息。
- 输入会提供 result、user_profile、chat_history、plan_step_trace，请综合判断。

约束：
- 只允许通过 done 输出 should_send。
- should_send 必须是布尔值。
- 不要输出任何额外解释。
""".strip()

_DONE_TOOL_SCHEMA = build_function_tool_schema(
    name="done",
    description="Finish scheduled task result delivery decision with should_send.",
    arguments_model=ScheduledTaskResultDecision,
)


class ScheduledResultDecisionRunner:
    def __init__(
        self,
        *,
        llm_client: LLMClient | None,
        max_steps: int,
        logger: logging.Logger,
    ) -> None:
        self._llm_client = llm_client
        self._max_steps = max(max_steps, 1)
        self._logger = logger

    def run_once(
        self,
        *,
        context_payload: ScheduledTaskResultDecisionPromptPayload | dict[str, Any],
    ) -> ScheduledTaskResultDecision | None:
        llm_client = self._llm_client
        if llm_client is None:
            self._logger.info(
                "scheduled result decision skipped: llm missing",
                extra={
                    "event": "scheduled_result_send_skipped",
                    "context": {"reason": "llm_missing"},
                },
            )
            return None
        reply_with_tools = getattr(llm_client, "reply_with_tools", None)
        if not callable(reply_with_tools):
            self._logger.info(
                "scheduled result decision skipped: tool calling unavailable",
                extra={
                    "event": "scheduled_result_send_skipped",
                    "context": {"reason": "tool_calling_unavailable"},
                },
            )
            return None

        normalized_context = _normalize_context_payload(context_payload)
        if normalized_context is None:
            self._logger.warning(
                "scheduled result decision skipped: invalid context",
                extra={
                    "event": "scheduled_result_send_skipped",
                    "context": {"reason": "invalid_context_payload"},
                },
            )
            return None

        self._logger.info(
            "scheduled result decision started",
            extra={
                "event": "scheduled_result_decision_start",
                "context": {
                    "task_name": normalized_context.result.task_name,
                    "has_user_profile": bool((normalized_context.user_profile or "").strip()),
                    "chat_history_count": len(normalized_context.chat_history),
                    "plan_trace_counts": {
                        "latest_plan": len(normalized_context.plan_step_trace.latest_plan),
                        "completed_subtasks": len(normalized_context.plan_step_trace.completed_subtasks),
                        "observations": len(normalized_context.plan_step_trace.observations),
                    },
                },
            },
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SCHEDULED_RESULT_DECISION_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(normalized_context.model_dump(mode="json"), ensure_ascii=False)},
        ]
        for step_index in range(1, self._max_steps + 1):
            raw_payload = reply_with_tools(messages, tools=[_DONE_TOOL_SCHEMA], tool_choice="auto")
            payload = _normalize_tool_reply_payload(raw_payload)
            assistant_message = payload.assistant_message
            messages.append(_assistant_message_to_chat_payload(assistant_message))
            tool_calls = assistant_message.tool_calls
            if not tool_calls:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": f"scheduled_result_decision_{step_index}",
                        "content": "必须调用 done 输出 should_send。",
                    }
                )
                self._logger.warning(
                    "scheduled result decision invalid action",
                    extra={
                        "event": "scheduled_result_decision_invalid_action",
                        "context": {
                            "task_name": normalized_context.result.task_name,
                            "reason": "missing_tool_calls",
                            "step_index": step_index,
                        },
                    },
                )
                continue

            tool_call = tool_calls[0]
            tool_name = tool_call.function.name.strip().lower()
            tool_call_id = tool_call.id.strip() or f"scheduled_result_decision_{step_index}"
            if tool_name != "done":
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": "当前仅允许 done。",
                    }
                )
                self._logger.warning(
                    "scheduled result decision invalid action",
                    extra={
                        "event": "scheduled_result_decision_invalid_action",
                        "context": {
                            "task_name": normalized_context.result.task_name,
                            "reason": "tool_not_allowed",
                            "action_tool": tool_name,
                            "step_index": step_index,
                        },
                    },
                )
                continue

            arguments = parse_json_object(tool_call.function.arguments) or {}
            decision = _normalize_done_arguments(arguments)
            if decision is None:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": "done 参数非法：需要 should_send。",
                    }
                )
                self._logger.warning(
                    "scheduled result decision invalid action",
                    extra={
                        "event": "scheduled_result_decision_invalid_action",
                        "context": {
                            "task_name": normalized_context.result.task_name,
                            "reason": "invalid_done_payload",
                            "step_index": step_index,
                        },
                    },
                )
                continue

            self._logger.info(
                "scheduled result decision done: should_send=%s",
                decision.should_send,
                extra={
                    "event": "scheduled_result_decision_done",
                    "context": {
                        "task_name": normalized_context.result.task_name,
                        "should_send": decision.should_send,
                    },
                },
            )
            return decision

        self._logger.warning(
            "scheduled result decision reached max steps",
            extra={
                "event": "scheduled_result_decision_failed",
                "context": {
                    "task_name": normalized_context.result.task_name,
                    "reason": "max_steps_reached",
                    "max_steps": self._max_steps,
                },
            },
        )
        return None


def _normalize_context_payload(
    context_payload: ScheduledTaskResultDecisionPromptPayload | dict[str, Any],
) -> ScheduledTaskResultDecisionPromptPayload | None:
    if isinstance(context_payload, ScheduledTaskResultDecisionPromptPayload):
        return context_payload
    try:
        return ScheduledTaskResultDecisionPromptPayload.model_validate(context_payload)
    except ValidationError:
        return None


def _normalize_done_arguments(arguments: dict[str, Any]) -> ScheduledTaskResultDecision | None:
    try:
        return ScheduledTaskResultDecision.model_validate(arguments)
    except ValidationError:
        return None


def _normalize_tool_reply_payload(payload: Any) -> ToolReplyPayload:
    normalized = parse_tool_reply_payload(payload)
    if normalized is not None:
        return normalized
    return ToolReplyPayload.model_validate(
        {
            "assistant_message": {"role": "assistant", "content": "", "tool_calls": []},
            "reasoning_content": None,
        }
    )


def _assistant_message_to_chat_payload(message: AssistantToolMessage) -> dict[str, Any]:
    normalized = message.model_dump()
    content = normalized.get("content")
    normalized["content"] = "" if content is None else str(content)
    return normalized
