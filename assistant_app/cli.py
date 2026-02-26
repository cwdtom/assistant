from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TextIO

from assistant_app.agent import AssistantAgent
from assistant_app.config import load_config
from assistant_app.db import AssistantDB
from assistant_app.feishu_adapter import create_feishu_runner
from assistant_app.llm import OpenAICompatibleClient
from assistant_app.logging_setup import (
    configure_app_logger,
    configure_feishu_logger,
    configure_llm_trace_logger,
)
from assistant_app.persona import PersonaRewriter
from assistant_app.reminder_service import ReminderService
from assistant_app.reminder_sink import StdoutReminderSink
from assistant_app.search import create_search_provider
from assistant_app.timer import TimerEngine

CLEAR_TERMINAL_SEQUENCE = "\033[3J\033[2J\033[H"
PROGRESS_COLOR_PREFIX = "\033[90m"
PROGRESS_COLOR_SUFFIX = "\033[0m"


class _AgentLike(Protocol):
    llm_client: Any

    def handle_input(self, user_input: str) -> str: ...
    def set_progress_callback(self, callback: Callable[[str], None] | None) -> None: ...


def _should_show_waiting(agent: _AgentLike, user_input: str) -> bool:
    text = user_input.strip()
    return bool(text) and not text.startswith("/") and agent.llm_client is not None


def _clear_terminal_history(stream: TextIO = sys.stdout) -> None:
    stream.write(CLEAR_TERMINAL_SEQUENCE)
    stream.flush()


def _exit_cli(stream: TextIO = sys.stdout, with_leading_newline: bool = False) -> None:
    _clear_terminal_history(stream=stream)
    if with_leading_newline:
        stream.write("\n")
    stream.write("已退出。\n")
    stream.flush()


def _handle_input_with_feedback(
    agent: _AgentLike,
    user_input: str,
    stream: TextIO = sys.stdout,
    progress_color_prefix: str = PROGRESS_COLOR_PREFIX,
    progress_color_suffix: str = PROGRESS_COLOR_SUFFIX,
) -> str:
    show_progress = _should_show_waiting(agent, user_input)
    original_setter = getattr(agent, "set_progress_callback", None)
    if callable(original_setter):
        if show_progress:
            original_setter(
                lambda msg: _write_progress_line(
                    stream=stream,
                    message=msg,
                    color_prefix=progress_color_prefix,
                    color_suffix=progress_color_suffix,
                )
            )
        else:
            original_setter(None)
    try:
        return agent.handle_input(user_input)
    finally:
        if callable(original_setter):
            original_setter(None)


def _write_progress_line(
    stream: TextIO,
    message: str,
    *,
    color_prefix: str = PROGRESS_COLOR_PREFIX,
    color_suffix: str = PROGRESS_COLOR_SUFFIX,
) -> None:
    for line in message.splitlines():
        stream.write(f"{color_prefix}进度> {line}{color_suffix}\n")
    stream.flush()


def _resolve_progress_color(color: str) -> tuple[str, str]:
    normalized = color.strip().lower()
    if normalized in {"", "gray", "grey"}:
        return PROGRESS_COLOR_PREFIX, PROGRESS_COLOR_SUFFIX
    if normalized in {"off", "none", "no"}:
        return "", ""
    return PROGRESS_COLOR_PREFIX, PROGRESS_COLOR_SUFFIX


def _configure_llm_trace_logger(log_path: str, retention_days: int) -> logging.Logger:
    return configure_llm_trace_logger(log_path, retention_days=retention_days)


def _configure_feishu_logger(log_path: str, retention_days: int) -> logging.Logger:
    return configure_feishu_logger(log_path, retention_days)


def _configure_app_logger(log_path: str, retention_days: int) -> logging.Logger:
    return configure_app_logger(log_path, retention_days)


def _is_same_log_path(path_a: str, path_b: str) -> bool:
    normalized_a = path_a.strip()
    normalized_b = path_b.strip()
    if not normalized_a or not normalized_b:
        return False
    return Path(normalized_a).expanduser().resolve() == Path(normalized_b).expanduser().resolve()


def main() -> None:
    config = load_config()
    app_logger = _configure_app_logger(config.app_log_path, config.app_log_retention_days)
    _configure_llm_trace_logger(config.llm_trace_log_path, retention_days=config.app_log_retention_days)
    db = AssistantDB(config.db_path)
    progress_color_prefix, progress_color_suffix = _resolve_progress_color(config.cli_progress_color)
    search_provider = create_search_provider(
        provider_name=config.search_provider,
        bocha_api_key=config.bocha_api_key,
        bocha_summary=config.bocha_search_summary,
    )

    llm_client = None
    if config.api_key:
        llm_client = OpenAICompatibleClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
        )
    persona_rewriter = PersonaRewriter(
        llm_client=llm_client,
        persona=config.assistant_persona,
        enabled=config.persona_rewrite_enabled,
        logger=app_logger,
    )

    agent = AssistantAgent(
        db=db,
        llm_client=llm_client,
        search_provider=search_provider,
        app_logger=app_logger,
        user_profile_path=config.user_profile_path,
        plan_replan_max_steps=config.plan_replan_max_steps,
        plan_replan_retry_count=config.plan_replan_retry_count,
        plan_observation_char_limit=config.plan_observation_char_limit,
        plan_observation_history_limit=config.plan_observation_history_limit,
        plan_continuous_failure_limit=config.plan_continuous_failure_limit,
        task_cancel_command=config.task_cancel_command,
        internet_search_top_k=config.internet_search_top_k,
        schedule_max_window_days=config.schedule_max_window_days,
        infinite_repeat_conflict_preview_days=config.infinite_repeat_conflict_preview_days,
        final_response_rewriter=persona_rewriter.rewrite_final_response,
    )
    timer_engine: TimerEngine | None = None
    feishu_runner = None
    if config.timer_enabled:
        reminder_sink = StdoutReminderSink(stream=sys.stdout)
        reminder_service = ReminderService(
            db=db,
            sink=reminder_sink,
            lookahead_seconds=config.timer_lookahead_seconds,
            catchup_seconds=config.timer_catchup_seconds,
            batch_limit=config.timer_batch_limit,
            logger=app_logger,
            content_rewriter=persona_rewriter.rewrite_reminder_content,
        )
        timer_engine = TimerEngine(
            reminder_service=reminder_service,
            poll_interval_seconds=config.timer_poll_interval_seconds,
            logger=app_logger,
        )
        timer_engine.start()

    if config.feishu_enabled:
        if not config.feishu_app_id or not config.feishu_app_secret:
            print("助手> FEISHU_ENABLED=true 但 FEISHU_APP_ID/FEISHU_APP_SECRET 未配置，已跳过 Feishu 接入。")
        else:
            feishu_retention_days = config.feishu_log_retention_days
            if _is_same_log_path(config.feishu_log_path, config.app_log_path):
                feishu_retention_days = config.app_log_retention_days
            feishu_logger = _configure_feishu_logger(
                log_path=config.feishu_log_path,
                retention_days=feishu_retention_days,
            )
            feishu_runner = create_feishu_runner(
                app_id=config.feishu_app_id,
                app_secret=config.feishu_app_secret,
                agent=agent,
                logger=feishu_logger,
                allowed_open_ids=set(config.feishu_allowed_open_ids),
                send_retry_count=config.feishu_send_retry_count,
                text_chunk_size=config.feishu_text_chunk_size,
                dedup_ttl_seconds=config.feishu_dedup_ttl_seconds,
                ack_reaction_enabled=config.feishu_ack_reaction_enabled,
                ack_emoji_type=config.feishu_ack_emoji_type,
                done_emoji_type=config.feishu_done_emoji_type,
            )
            feishu_runner.start_background()
            print("助手> Feishu 长连接已在后台启动（单聊模式）。")

    try:
        _clear_terminal_history()
        print("CLI 个人助手已启动。输入 /help 查看命令，输入 exit 退出。")
        while True:
            try:
                raw = input("你> ").strip()
            except (EOFError, KeyboardInterrupt):
                _exit_cli(with_leading_newline=True)
                break

            if raw.lower() in {"exit", "quit"}:
                _exit_cli()
                break

            try:
                response = _handle_input_with_feedback(
                    agent,
                    raw,
                    progress_color_prefix=progress_color_prefix,
                    progress_color_suffix=progress_color_suffix,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"助手> 处理失败: {exc}")
                continue
            print(f"助手> {response}")
    finally:
        if feishu_runner is not None:
            feishu_runner.stop()
        if timer_engine is not None:
            timer_engine.stop()


if __name__ == "__main__":
    main()
