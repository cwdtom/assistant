from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from assistant_app.config import DEFAULT_PROACTIVE_REMINDER_SCORE_THRESHOLD
from assistant_app.db import AssistantDB
from assistant_app.feishu_adapter import split_semantic_messages
from assistant_app.llm import LLMClient
from assistant_app.proactive_context import build_proactive_context_snapshot
from assistant_app.proactive_react import ProactiveReactRunner
from assistant_app.proactive_tools import ProactiveToolExecutor
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
        score_threshold: int,
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
        self._score_threshold = (
            score_threshold
            if 0 <= score_threshold <= 100
            else DEFAULT_PROACTIVE_REMINDER_SCORE_THRESHOLD
        )
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

        if not self._target_open_id:
            self._logger.warning(
                "proactive target open_id missing",
                extra={"event": "proactive_target_missing"},
            )
            return

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
            score_threshold=self._score_threshold,
            max_steps=self._max_steps,
            internet_search_allowed=True,
        )
        decision = react_runner.run_once(context_payload=prompt_payload)
        if decision is None:
            return
        should_notify = decision.score >= self._score_threshold
        self._logger.info(
            "proactive gate decided: score=%s threshold=%s notify=%s",
            decision.score,
            self._score_threshold,
            should_notify,
            extra={
                "event": "proactive_gate_decision",
                "context": {
                    "score": decision.score,
                    "threshold": self._score_threshold,
                    "notify": should_notify,
                },
            },
        )
        if not should_notify:
            return
        content = decision.message.strip()
        if not content:
            self._logger.warning(
                "proactive send skipped: empty message",
                extra={"event": "proactive_send_skipped_empty_message"},
            )
            return
        final_content = self._rewrite_content(content)
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
            return
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

        marker = (
            f"[proactive_tick] now={now.strftime('%Y-%m-%d %H:%M')} "
            f"score={decision.score} threshold={self._score_threshold} reason={decision.reason}"
        )
        try:
            self._db.save_turn(user_content=marker, assistant_content=final_content)
            self._logger.info(
                "proactive history saved",
                extra={
                    "event": "proactive_history_saved",
                    "context": {"saved": True},
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "proactive history save failed",
                extra={
                    "event": "proactive_history_save_failed",
                    "context": {"error": repr(exc)},
                },
            )

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
