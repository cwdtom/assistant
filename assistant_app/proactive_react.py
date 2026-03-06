from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from assistant_app.config import DEFAULT_PROACTIVE_REMINDER_SCORE_THRESHOLD
from assistant_app.llm import LLMClient
from assistant_app.proactive_tools import ProactiveToolExecutor, build_proactive_tool_schemas

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


@dataclass(frozen=True)
class ProactiveDecision:
    score: int
    message: str
    reason: str


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

    def run_once(self, *, context_payload: dict[str, Any]) -> ProactiveDecision | None:
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

        tools = build_proactive_tool_schemas()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PROACTIVE_REACT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(context_payload, ensure_ascii=False)},
        ]
        self._logger.info(
            "proactive react started",
            extra={
                "event": "proactive_react_start",
                "context": {
                    "max_steps": self._max_steps,
                    "score_threshold": _extract_score_threshold(context_payload),
                    "has_user_profile": bool(
                        isinstance(context_payload.get("user_profile"), dict)
                        and bool(context_payload["user_profile"].get("content"))
                    ),
                },
            },
        )

        for step_index in range(1, self._max_steps + 1):
            payload = reply_with_tools(messages, tools=tools, tool_choice="auto")
            assistant_message = _normalize_assistant_message(payload)
            messages.append(assistant_message)
            tool_calls = assistant_message.get("tool_calls") or []
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
            tool_id = str(tool_call.get("id") or f"proactive_tool_{step_index}")
            function_payload = tool_call.get("function") if isinstance(tool_call, dict) else None
            if not isinstance(function_payload, dict):
                self._logger.warning(
                    "proactive react invalid action: malformed function payload",
                    extra={
                        "event": "proactive_react_invalid_action",
                        "context": {"action_tool": "", "reason": "malformed_function", "step_index": step_index},
                    },
                )
                break
            tool_name = str(function_payload.get("name") or "").strip()
            raw_arguments = function_payload.get("arguments")
            arguments = _parse_arguments(raw_arguments)
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
                    score_threshold=_extract_score_threshold(context_payload),
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


def _normalize_assistant_message(payload: dict[str, Any]) -> dict[str, Any]:
    assistant_message = payload.get("assistant_message") if isinstance(payload, dict) else None
    if not isinstance(assistant_message, dict):
        return {"role": "assistant", "content": "", "tool_calls": []}
    role = str(assistant_message.get("role") or "assistant")
    content = assistant_message.get("content")
    if not isinstance(content, str):
        content = "" if content is None else str(content)
    tool_calls = assistant_message.get("tool_calls")
    if not isinstance(tool_calls, list):
        tool_calls = []
    return {
        "role": role,
        "content": content,
        "tool_calls": tool_calls,
    }


def _parse_arguments(raw_arguments: Any) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return dict(raw_arguments)
    if not isinstance(raw_arguments, str):
        return {}
    text = raw_arguments.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


def _normalize_done_arguments(
    arguments: dict[str, Any],
    *,
    score_threshold: int,
) -> ProactiveDecision | None:
    score = arguments.get("score")
    message = arguments.get("message")
    reason = arguments.get("reason")
    if not isinstance(score, int) or isinstance(score, bool):
        return None
    if score < 0 or score > 100:
        return None
    if not isinstance(message, str):
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    normalized_message = message.strip()
    if score >= score_threshold and not normalized_message:
        return None
    return ProactiveDecision(
        score=score,
        message=normalized_message,
        reason=reason.strip(),
    )


def _extract_score_threshold(context_payload: dict[str, Any]) -> int:
    policy = context_payload.get("policy")
    if isinstance(policy, dict):
        raw_value = policy.get("score_threshold")
        if isinstance(raw_value, int) and not isinstance(raw_value, bool) and 0 <= raw_value <= 100:
            return raw_value
    return DEFAULT_PROACTIVE_REMINDER_SCORE_THRESHOLD


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
