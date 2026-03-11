from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar, cast

from assistant_app.agent_components.models import PendingPlanTask, ThoughtToolCallingError
from assistant_app.agent_components.planner_session import PlannerSession
from assistant_app.agent_components.render_helpers import _strip_think_blocks, _try_parse_json
from assistant_app.llm import LLMClient
from assistant_app.planner_plan_replan import normalize_plan_decision, normalize_replan_decision
from assistant_app.planner_thought import (
    build_thought_tool_schemas,
    normalize_thought_decision,
    normalize_thought_tool_call,
)
from assistant_app.schemas.planner import (
    PlanResponsePayload,
    ReplanDoneDecision,
    ReplannedDecision,
    ReplanResponsePayload,
    ThoughtAskUserDecision,
    ThoughtContinueDecision,
    ThoughtDecision,
    ThoughtDoneDecision,
    ThoughtResponsePayload,
    parse_tool_reply_payload,
)
from assistant_app.schemas.tools import parse_json_object, validate_thought_tool_arguments

PayloadT = TypeVar("PayloadT")


class PlannerPayloadRequester:
    def __init__(
        self,
        *,
        llm_client: LLMClient | None,
        llm_trace_logger: logging.Logger,
        plan_replan_retry_count: int,
        session: PlannerSession,
    ) -> None:
        self._llm_client = llm_client
        self._llm_trace_logger = llm_trace_logger
        self._plan_replan_retry_count = plan_replan_retry_count
        self._session = session
        self._llm_trace_call_seq = 0

    def request_plan_payload(self, task: PendingPlanTask) -> PlanResponsePayload | None:
        if self._llm_client is None:
            return None

        planner_messages = self._session.build_plan_messages(task)
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
            self._session.append_outer_message_turn(
                task=task,
                user_message_content=raw_user_message,
                assistant_response=payload.raw_response,
            )
        return payload

    def request_thought_payload(self, task: PendingPlanTask) -> ThoughtResponsePayload | None:
        if self._llm_client is None:
            return None

        request_messages = self._session.build_thought_request_messages(task)
        payload = self._request_thought_payload_with_retry(task, request_messages)
        if payload is None:
            return None
        if payload.assistant_message is not None:
            self._session.append_thought_assistant_message(task, payload.assistant_message)
        else:
            self._session.append_thought_decision_message(task, payload.decision)
        return payload

    def request_replan_payload(self, task: PendingPlanTask) -> ReplanResponsePayload | None:
        if self._llm_client is None:
            return None

        planner_messages = self._session.build_replan_messages(task)

        def _build_replan_response(
            decision: ReplannedDecision | ReplanDoneDecision,
            raw_response: str,
        ) -> ReplanResponsePayload:
            return ReplanResponsePayload(decision=decision, raw_response=raw_response)

        replan_normalizer = cast(
            Callable[[dict[str, Any]], ReplannedDecision | ReplanDoneDecision | None],
            normalize_replan_decision,
        )
        payload = self._request_payload_with_retry(
            planner_messages,
            replan_normalizer,
            _build_replan_response,
        )
        if payload is None:
            return None
        raw_user_message = planner_messages[-1].get("content")
        if isinstance(raw_user_message, str):
            self._session.append_outer_message_turn(
                task=task,
                user_message_content=raw_user_message,
                assistant_response=payload.raw_response,
            )
        return payload

    def _request_payload_with_retry(
        self,
        messages: list[dict[str, object]],
        normalizer: Callable[[dict[str, Any]], Any | None],
        payload_builder: Callable[[Any, str], PayloadT],
    ) -> PayloadT | None:
        max_attempts = 1 + self._plan_replan_retry_count
        phase = self._llm_trace_phase(messages)
        for attempt in range(1, max_attempts + 1):
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
                self._log_planner_payload_validation_failure(
                    phase=phase,
                    reason="response_not_json_object",
                    payload_type=self._classify_json_text_payload(raw),
                    attempt=attempt,
                )
                continue

            decision = normalizer(payload)
            if decision is not None:
                return payload_builder(decision, raw)
            self._log_planner_payload_validation_failure(
                phase=phase,
                reason="schema_validation_failed",
                payload_type="dict",
                attempt=attempt,
            )
        return None

    def _request_thought_payload_with_retry(
        self,
        task: PendingPlanTask,
        messages: list[dict[str, object]],
    ) -> ThoughtResponsePayload | None:
        max_attempts = 1 + self._plan_replan_retry_count
        phase = self._llm_trace_phase(messages)
        thought_tool_names = self._session.current_thought_tool_names(task)
        thought_tool_schemas = build_thought_tool_schemas(
            thought_tool_names,
            allow_ask_user="ask_user" in thought_tool_names,
        )
        allowed_tool_names = set(thought_tool_names)
        for attempt in range(1, max_attempts + 1):
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
            self._log_llm_trace_event(
                {
                    "event": "llm_response",
                    "call_id": call_id,
                    "phase": phase,
                    "attempt": attempt,
                    "response": response.model_dump(mode="json", exclude_none=True) if response is not None else None,
                }
            )
            if response is not None:
                return response
        return None

    def _llm_reply_for_planner(self, messages: list[dict[str, object]]) -> str:
        if self._llm_client is None:
            return ""

        reply_json = getattr(self._llm_client, "reply_json", None)
        if callable(reply_json):
            try:
                return str(reply_json(messages))
            except Exception:
                pass
        return self._llm_client.reply(messages)

    def _llm_reply_for_thought(
        self,
        messages: list[dict[str, object]],
        *,
        thought_tool_schemas: list[dict[str, Any]],
        allowed_tool_names: set[str],
    ) -> ThoughtResponsePayload | None:
        if self._llm_client is None:
            return None

        reply_with_tools = getattr(self._llm_client, "reply_with_tools", None)
        if not callable(reply_with_tools):
            raw = self._llm_reply_for_planner(messages)
            parsed_response = _try_parse_json(_strip_think_blocks(raw).strip())
            if not isinstance(parsed_response, dict):
                self._log_planner_payload_validation_failure(
                    phase="thought",
                    reason="response_not_json_object",
                    payload_type=self._classify_json_text_payload(raw),
                )
                return None
            decision = normalize_thought_decision(parsed_response)
            if decision is None:
                self._log_planner_payload_validation_failure(
                    phase="thought",
                    reason="schema_validation_failed",
                    payload_type="dict",
                )
                return None
            if not self._is_thought_decision_tool_allowed(decision, allowed_tool_names):
                self._log_planner_payload_validation_failure(
                    phase="thought",
                    reason="tool_not_allowed",
                    payload_type="dict",
                )
                return None
            return ThoughtResponsePayload(decision=decision)

        try:
            raw_tool_response = reply_with_tools(messages, tools=thought_tool_schemas, tool_choice="auto")
        except RuntimeError as exc:
            message = str(exc)
            lowered = message.lower()
            if "thinking" in lowered or "reasoning_content" in lowered or "reasoner" in lowered:
                raise ThoughtToolCallingError(message) from exc
            raise

        tool_response = parse_tool_reply_payload(raw_tool_response)
        if tool_response is None:
            self._log_planner_payload_validation_failure(
                phase="thought",
                reason="tool_reply_payload_invalid",
                payload_type=type(raw_tool_response).__name__,
            )
            return None

        reasoning_content = str(tool_response.reasoning_content or "").strip()
        if reasoning_content:
            raise ThoughtToolCallingError(
                "当前版本 thought 阶段暂不支持 thinking 模式（检测到 reasoning_content），"
                "请切换到非 thinking 模式后重试。"
            )

        assistant_message = tool_response.assistant_message
        tool_calls = assistant_message.tool_calls
        if tool_calls:
            if len(tool_calls) > 1:
                raise ThoughtToolCallingError(
                    f"thought 阶段每轮最多调用 1 个工具（本轮收到 {len(tool_calls)} 个），请重试。"
                )
            first_tool_call = tool_calls[0]
            tool_name = first_tool_call.function.name.strip().lower()
            parsed_arguments = parse_json_object(first_tool_call.function.arguments)
            if parsed_arguments is None:
                self._log_thought_tool_arguments_validation_failure(
                    tool_name=tool_name,
                    reason="arguments_not_json_object",
                )
                return None
            validated_arguments = validate_thought_tool_arguments(tool_name, parsed_arguments)
            if validated_arguments is None:
                self._log_thought_tool_arguments_validation_failure(
                    tool_name=tool_name,
                    reason="arguments_schema_invalid_or_unknown_tool",
                )
                return None
            decision = normalize_thought_tool_call(first_tool_call.model_dump())
            if decision is None:
                self._log_thought_tool_arguments_validation_failure(
                    tool_name=tool_name,
                    reason="decision_mapping_failed",
                )
                return None
            if not self._is_thought_decision_tool_allowed(decision, allowed_tool_names):
                self._log_planner_payload_validation_failure(
                    phase="thought",
                    reason="tool_not_allowed",
                    payload_type="tool_call",
                )
                return None
            call_id = first_tool_call.id.strip() or None
            return ThoughtResponsePayload(
                decision=decision,
                assistant_message=assistant_message,
                tool_call_id=call_id,
            )

        content = str(assistant_message.content or "").strip()
        parsed_content = _try_parse_json(_strip_think_blocks(content))
        if not isinstance(parsed_content, dict):
            self._log_planner_payload_validation_failure(
                phase="thought",
                reason="response_not_json_object",
                payload_type=self._classify_json_text_payload(content),
            )
            return None
        decision = normalize_thought_decision(parsed_content)
        if decision is None:
            self._log_planner_payload_validation_failure(
                phase="thought",
                reason="schema_validation_failed",
                payload_type="dict",
            )
            return None
        if not self._is_thought_decision_tool_allowed(decision, allowed_tool_names):
            self._log_planner_payload_validation_failure(
                phase="thought",
                reason="tool_not_allowed",
                payload_type="dict",
            )
            return None
        return ThoughtResponsePayload(
            decision=decision,
            assistant_message=assistant_message,
        )

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

    def _llm_trace_phase(self, messages: list[dict[str, object]]) -> str:
        if not messages:
            return "unknown"
        payload = _try_parse_json(str(messages[-1].get("content", "")))
        if not isinstance(payload, dict):
            return "unknown"
        phase = str(payload.get("phase") or "").strip().lower()
        return phase or "unknown"

    @staticmethod
    def _classify_json_text_payload(raw_text: str) -> str:
        text = raw_text.strip()
        if not text:
            return "empty"
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return "invalid_json"
        return type(parsed).__name__

    def _log_planner_payload_validation_failure(
        self,
        *,
        phase: str,
        reason: str,
        payload_type: str,
        attempt: int | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "event": "planner_payload_validation_failed",
            "phase": phase,
            "reason": reason,
            "payload_type": payload_type,
        }
        if attempt is not None:
            payload["attempt"] = attempt
        self._log_llm_trace_event(payload)

    def _log_thought_tool_arguments_validation_failure(self, *, tool_name: str, reason: str) -> None:
        self._log_llm_trace_event(
            {
                "event": "thought_tool_arguments_validation_failed",
                "phase": "thought",
                "tool_name": tool_name,
                "reason": reason,
            }
        )

    def _log_llm_trace_event(self, payload: dict[str, object]) -> None:
        try:
            self._llm_trace_logger.info(json.dumps(payload, ensure_ascii=False))
        except Exception:
            return
