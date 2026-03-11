from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from assistant_app.db import AssistantDB
from assistant_app.feishu_adapter import split_semantic_messages
from assistant_app.llm import LLMClient
from assistant_app.proactive_context import build_proactive_context_snapshot
from assistant_app.proactive_react import ProactiveReactRunner
from assistant_app.proactive_tools import ProactiveToolExecutor
from assistant_app.schemas.proactive import ProactiveDecision, ProactiveExecutionResult
from assistant_app.search import SearchProvider


class ProactiveReminderService:
    def __init__(
        self,
        *,
        db: AssistantDB,
        llm_client: LLMClient | None,
        search_provider: SearchProvider,
        logger: logging.Logger,
        target_open_id: str,
        send_text_to_open_id: Callable[[str, str], None],
        lookahead_hours: int,
        interval_minutes: int,
        night_quiet_hint: str,
        max_steps: int,
        user_profile_path: str,
        internet_search_top_k: int,
        final_content_rewriter: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = db
        self._llm_client = llm_client
        self._search_provider = search_provider
        self._logger = logger
        self._target_open_id = target_open_id.strip()
        self._send_text_to_open_id = send_text_to_open_id
        self._lookahead_hours = max(lookahead_hours, 1)
        self._interval_minutes = max(interval_minutes, 60)
        self._night_quiet_hint = night_quiet_hint.strip() or "23:00-08:00"
        self._max_steps = max(max_steps, 1)
        self._user_profile_path = user_profile_path
        self._internet_search_top_k = max(internet_search_top_k, 1)
        self._final_content_rewriter = final_content_rewriter
        self._clock = clock or datetime.now
        self._next_due_at: datetime | None = None

    def poll_scheduled(self) -> None:
        now = self._clock()
        next_due_at = self._next_due_at
        if next_due_at is not None and now < next_due_at:
            self._logger.info(
                "proactive tick skipped: not due",
                extra={
                    "event": "proactive_tick_skipped_not_due",
                    "context": {
                        "now": now.strftime("%Y-%m-%d %H:%M:%S"),
                        "next_due_at": next_due_at.strftime("%Y-%m-%d %H:%M:%S"),
                        "interval_minutes": self._interval_minutes,
                    },
                },
            )
            return

        self._next_due_at = now + timedelta(minutes=self._interval_minutes)
        self._logger.info(
            "proactive tick started",
            extra={
                "event": "proactive_tick_start",
                "context": {
                    "target_open_id": self._target_open_id,
                    "lookahead_hours": self._lookahead_hours,
                    "interval_minutes": self._interval_minutes,
                },
            },
        )
        self._execute_once(now=now, raise_on_failure=False)

    def _execute_once(
        self,
        *,
        now: datetime,
        raise_on_failure: bool,
    ) -> ProactiveExecutionResult | None:

        if not self._target_open_id:
            self._logger.warning(
                "proactive target open_id missing",
                extra={"event": "proactive_target_missing"},
            )
            return self._raise_or_none(
                RuntimeError("缺少 PROACTIVE_REMINDER_TARGET_OPEN_ID，无法执行主动提醒。"),
                raise_on_failure=raise_on_failure,
            )

        decision = self._run_decision(now=now, raise_on_failure=raise_on_failure)
        if decision is None:
            return None
        result = ProactiveExecutionResult(
            should_send=decision.should_send,
            message=decision.message.strip(),
        )
        self._logger.info(
            "proactive decision decided: should_send=%s",
            result.should_send,
            extra={
                "event": "proactive_decision_result",
                "context": {
                    "should_send": result.should_send,
                    "message_length": len(result.message),
                },
            },
        )
        if not result.should_send:
            return result
        if not result.message:
            self._logger.warning(
                "proactive send skipped: empty message",
                extra={"event": "proactive_send_skipped_empty_message"},
            )
            return self._raise_or_none(
                RuntimeError("主动提醒消息为空，无法发送。"),
                raise_on_failure=raise_on_failure,
            )
        final_content = self._rewrite_content(result.message)
        if final_content != result.message:
            result = result.model_copy(update={"message": final_content})
        segments = split_semantic_messages(final_content)

        self._logger.info(
            "proactive send started",
            extra={
                "event": "proactive_send_start",
                "context": {
                    "target_open_id": self._target_open_id,
                    "message_length": len(final_content),
                },
            },
        )
        try:
            for segment in segments:
                self._send_text_to_open_id(self._target_open_id, segment)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "proactive send failed",
                extra={
                    "event": "proactive_send_failed",
                    "context": {"error": repr(exc)},
                },
            )
            return self._raise_or_none(exc, raise_on_failure=raise_on_failure)
        self._logger.info(
            "proactive send completed",
            extra={
                "event": "proactive_send_done",
                "context": {
                    "target_open_id": self._target_open_id,
                    "segment_count": len(segments),
                },
            },
        )
        return result

    def _run_decision(
        self,
        *,
        now: datetime,
        raise_on_failure: bool,
    ) -> ProactiveDecision | None:
        snapshot = build_proactive_context_snapshot(
            db=self._db,
            now=now,
            lookahead_hours=self._lookahead_hours,
            chat_lookback_hours=24,
            user_profile_path=self._user_profile_path,
            logger=self._logger,
        )
        tool_executor = ProactiveToolExecutor(
            db=self._db,
            search_provider=self._search_provider,
            now=now,
            lookahead_hours=self._lookahead_hours,
            chat_lookback_hours=24,
            internet_search_top_k=self._internet_search_top_k,
        )
        react_runner = ProactiveReactRunner(
            llm_client=self._llm_client,
            tool_executor=tool_executor,
            max_steps=self._max_steps,
            logger=self._logger,
        )
        prompt_payload = snapshot.to_prompt_payload(
            night_quiet_hint=self._night_quiet_hint,
            max_steps=self._max_steps,
            internet_search_allowed=True,
        )
        decision = react_runner.run_once(context_payload=prompt_payload)
        if decision is not None:
            return decision
        return self._raise_or_none(
            RuntimeError("主动提醒未产出有效决策。"),
            raise_on_failure=raise_on_failure,
        )

    @staticmethod
    def _raise_or_none(
        exc: Exception,
        *,
        raise_on_failure: bool,
    ) -> None:
        if raise_on_failure:
            raise exc
        return None

    def _rewrite_content(self, content: str) -> str:
        rewriter = self._final_content_rewriter
        if rewriter is None:
            return content
        try:
            rewritten = rewriter(content)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "proactive reminder rewrite failed",
                extra={
                    "event": "proactive_rewrite_failed",
                    "context": {"error": repr(exc)},
                },
            )
            return content
        normalized = rewritten.strip()
        if not normalized:
            return content
        return normalized
