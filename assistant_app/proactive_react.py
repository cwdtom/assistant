from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from assistant_app.llm import LLMClient
from assistant_app.proactive_tools import ProactiveToolExecutor, build_proactive_tool_schemas
from assistant_app.schemas.planner import AssistantToolMessage, ToolReplyPayload, parse_tool_reply_payload
from assistant_app.schemas.proactive import ProactiveDecision, ProactivePromptPayload
from assistant_app.schemas.tools import parse_json_object

PROACTIVE_REACT_SYSTEM_PROMPT = """
你是“主动提醒决策器”。

你的目标：
- 基于提供的上下文，判断是否应当主动提醒用户。
- 若需要提醒，给出简洁、可执行的提醒文案与理由。

工作方式：
- 使用当前会话中提供的工具逐步取证。
- 在有限步数内结束并输出结构化 done 决策。
- 夜间时段优先保持克制，除非事项紧急。

输出要求：
- 终态必须通过 done 输出：score/message/reason。
- score 必须是 0~100 的整数，表示当前事项的提醒价值。
- 当 score 大于等于当前阈值时，message 必须是非空提醒文案。
- 不要输出额外解释性前后缀。
""".strip()

class ProactiveReactRunner:
    def __init__(
        self,
        *,
        llm_client: LLMClient | None,
        tool_executor: ProactiveToolExecutor,
        max_steps: int,
        logger: logging.Logger,
    ) -> None:
        self._llm_client = llm_client
        self._tool_executor = tool_executor
        self._max_steps = max(max_steps, 1)
        self._logger = logger
        self._allowed_tool_names = _extract_allowed_tool_names()

    def run_once(self, *, context_payload: ProactivePromptPayload | dict[str, Any]) -> ProactiveDecision | None:
        llm_client = self._llm_client
        if llm_client is None:
            self._logger.warning(
                "proactive llm missing",
                extra={"event": "proactive_llm_missing"},
            )
            return None
        reply_with_tools = getattr(llm_client, "reply_with_tools", None)
        if not callable(reply_with_tools):
            self._logger.warning(
                "proactive llm lacks tool-calling support",
                extra={"event": "proactive_llm_no_tool_calling"},
            )
            return None

        normalized_context = _normalize_context_payload(context_payload)
        if normalized_context is None:
            self._logger.warning(
                "proactive context payload invalid",
                extra={"event": "proactive_context_payload_invalid"},
            )
            return None

        tools = build_proactive_tool_schemas()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PROACTIVE_REACT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(normalized_context.model_dump(mode="json"), ensure_ascii=False)},
        ]
        self._logger.info(
            "proactive react started",
            extra={
                "event": "proactive_react_start",
                "context": {
                    "max_steps": self._max_steps,
                    "score_threshold": normalized_context.score_threshold,
                    "has_user_profile": bool(normalized_context.user_profile.content),
                },
            },
        )

        for step_index in range(1, self._max_steps + 1):
            raw_payload = reply_with_tools(messages, tools=tools, tool_choice="auto")
            payload = _normalize_tool_reply_payload(raw_payload)
            assistant_message = payload.assistant_message
            messages.append(_assistant_message_to_chat_payload(assistant_message))
            tool_calls = assistant_message.tool_calls
            if not tool_calls:
                self._logger.warning(
                    "proactive react invalid action: no tool calls",
                    extra={
                        "event": "proactive_react_invalid_action",
                        "context": {"action_tool": "", "reason": "missing_tool_calls", "step_index": step_index},
                    },
                )
                break

            tool_call = tool_calls[0]
            tool_id = tool_call.id.strip() or f"proactive_tool_{step_index}"
            tool_name = tool_call.function.name.strip()
            arguments = _parse_arguments(tool_call.function.arguments)
            self._logger.info(
                "proactive react step",
                extra={
                    "event": "proactive_react_step",
                    "context": {"step_index": step_index, "action_tool": tool_name},
                },
            )

            if tool_name == "done":
                decision = _normalize_done_arguments(
                    arguments,
                    score_threshold=normalized_context.score_threshold,
                )
                if decision is None:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": "done 参数非法：需要 score/message/reason。",
                        }
                    )
                    self._logger.warning(
                        "proactive react invalid done payload",
                        extra={
                            "event": "proactive_react_invalid_action",
                            "context": {
                                "action_tool": tool_name,
                                "reason": "invalid_done_payload",
                                "step_index": step_index,
                            },
                        },
                    )
                    continue
                self._logger.info(
                    "proactive react done: score=%s",
                    decision.score,
                    extra={
                        "event": "proactive_react_done",
                        "context": {"score": decision.score},
                    },
                )
                return decision

            if tool_name not in self._allowed_tool_names:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": "tool 不可用，请使用当前会话可用工具。",
                    }
                )
                self._logger.warning(
                    "proactive react invalid action",
                    extra={
                        "event": "proactive_react_invalid_action",
                        "context": {
                            "action_tool": tool_name,
                            "reason": "tool_not_allowed",
                            "step_index": step_index,
                        },
                    },
                )
                continue

            try:
                observation = self._tool_executor.execute(tool_name=tool_name, arguments=arguments)
            except Exception as exc:  # noqa: BLE001
                observation = f"tool 执行失败: {exc}"
                self._logger.warning(
                    "proactive react invalid action",
                    extra={
                        "event": "proactive_react_invalid_action",
                        "context": {
                            "action_tool": tool_name,
                            "reason": repr(exc),
                            "step_index": step_index,
                        },
                    },
                )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": observation,
                }
            )

        self._logger.warning(
            "proactive react max steps reached",
            extra={
                "event": "proactive_react_max_steps_reached",
                "context": {"max_steps": self._max_steps},
            },
        )
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


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    parsed = parse_json_object(raw_arguments)
    if parsed is None:
        return {}
    return parsed


def _normalize_context_payload(
    context_payload: ProactivePromptPayload | dict[str, Any],
) -> ProactivePromptPayload | None:
    if isinstance(context_payload, ProactivePromptPayload):
        return context_payload
    try:
        return ProactivePromptPayload.model_validate(context_payload)
    except ValidationError:
        return None


def _normalize_done_arguments(
    arguments: dict[str, Any],
    *,
    score_threshold: int,
) -> ProactiveDecision | None:
    try:
        parsed = ProactiveDecision.model_validate(arguments)
    except ValidationError:
        return None
    if parsed.score >= score_threshold and not parsed.message:
        return None
    return parsed


def _extract_allowed_tool_names() -> set[str]:
    names: set[str] = set()
    for tool in build_proactive_tool_schemas():
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        name = str(function.get("name") or "").strip()
        if not name or name == "done":
            continue
        names.add(name)
    return names
