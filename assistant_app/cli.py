from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TextIO

from assistant_app.agent import AssistantAgent
from assistant_app.config import load_config
from assistant_app.db import AssistantDB
from assistant_app.llm import OpenAICompatibleClient

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


def _configure_llm_trace_logger(log_path: str) -> None:
    logger = logging.getLogger("assistant_app.llm_trace")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    path = log_path.strip()
    if not path:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            close = getattr(handler, "close", None)
            if callable(close):
                close()
        logger.addHandler(logging.NullHandler())
        return

    abs_path = str(Path(path).expanduser().resolve())
    for handler in list(logger.handlers):
        if isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)
            continue
        existing_path = getattr(handler, "baseFilename", None)
        if not existing_path:
            continue
        if str(Path(existing_path).resolve()) == abs_path:
            return
    Path(abs_path).parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(abs_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(file_handler)


def main() -> None:
    config = load_config()
    _configure_llm_trace_logger(config.llm_trace_log_path)
    db = AssistantDB(config.db_path)
    progress_color_prefix, progress_color_suffix = _resolve_progress_color(config.cli_progress_color)

    llm_client = None
    if config.api_key:
        llm_client = OpenAICompatibleClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
        )

    agent = AssistantAgent(
        db=db,
        llm_client=llm_client,
        plan_replan_max_steps=config.plan_replan_max_steps,
        plan_replan_retry_count=config.plan_replan_retry_count,
        plan_observation_char_limit=config.plan_observation_char_limit,
        plan_observation_history_limit=config.plan_observation_history_limit,
        plan_continuous_failure_limit=config.plan_continuous_failure_limit,
        task_cancel_command=config.task_cancel_command,
        internet_search_top_k=config.internet_search_top_k,
        schedule_max_window_days=config.schedule_max_window_days,
        infinite_repeat_conflict_preview_days=config.infinite_repeat_conflict_preview_days,
    )

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


if __name__ == "__main__":
    main()
