from __future__ import annotations

import sys
from collections.abc import Callable
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
) -> str:
    show_progress = _should_show_waiting(agent, user_input)
    original_setter = getattr(agent, "set_progress_callback", None)
    if callable(original_setter):
        if show_progress:
            original_setter(lambda msg: _write_progress_line(stream=stream, message=msg))
        else:
            original_setter(None)
    try:
        return agent.handle_input(user_input)
    finally:
        if callable(original_setter):
            original_setter(None)


def _write_progress_line(stream: TextIO, message: str) -> None:
    for line in message.splitlines():
        stream.write(f"{PROGRESS_COLOR_PREFIX}进度> {line}{PROGRESS_COLOR_SUFFIX}\n")
    stream.flush()


def main() -> None:
    config = load_config()
    db = AssistantDB(config.db_path)

    llm_client = None
    if config.api_key:
        llm_client = OpenAICompatibleClient(
            api_key=config.api_key,
            base_url=config.base_url,
            model=config.model,
        )

    agent = AssistantAgent(db=db, llm_client=llm_client)

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
            response = _handle_input_with_feedback(agent, raw)
        except Exception as exc:  # noqa: BLE001
            print(f"助手> 处理失败: {exc}")
            continue
        print(f"助手> {response}")


if __name__ == "__main__":
    main()
