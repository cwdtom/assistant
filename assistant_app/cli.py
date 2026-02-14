from __future__ import annotations

import sys
import threading
from typing import TextIO

from assistant_app.agent import AssistantAgent
from assistant_app.config import load_config
from assistant_app.db import AssistantDB
from assistant_app.llm import OpenAICompatibleClient

WAITING_FRAME_INTERVAL = 0.25
WAITING_CLEAR_WIDTH = 48
CLEAR_TERMINAL_SEQUENCE = "\033[3J\033[2J\033[H"


def _should_show_waiting(agent: AssistantAgent, user_input: str) -> bool:
    text = user_input.strip()
    return bool(text) and not text.startswith("/") and agent.llm_client is not None


def _render_waiting_frame(frame_index: int) -> str:
    dots = "." * (frame_index % 3 + 1)
    return f"助手> 正在思考{dots}"


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
    agent: AssistantAgent,
    user_input: str,
    stream: TextIO = sys.stdout,
    interval: float = WAITING_FRAME_INTERVAL,
) -> str:
    if not _should_show_waiting(agent, user_input):
        return agent.handle_input(user_input)

    state: dict[str, object | None] = {"response": None, "error": None}

    def _worker() -> None:
        try:
            state["response"] = agent.handle_input(user_input)
        except Exception as exc:  # noqa: BLE001
            state["error"] = exc

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()

    frame_index = 0
    while worker.is_alive():
        stream.write("\r" + _render_waiting_frame(frame_index))
        stream.flush()
        frame_index += 1
        worker.join(timeout=interval)

    stream.write("\r" + " " * WAITING_CLEAR_WIDTH + "\r")
    stream.flush()

    if state["error"] is not None:
        raise RuntimeError(str(state["error"]))
    return str(state["response"] or "")


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
