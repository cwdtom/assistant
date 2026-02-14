from __future__ import annotations

from assistant_app.agent import AssistantAgent
from assistant_app.config import load_config
from assistant_app.db import AssistantDB
from assistant_app.llm import OpenAICompatibleClient


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

    print("CLI 个人助手已启动。输入 /help 查看命令，输入 exit 退出。")
    while True:
        try:
            raw = input("你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if raw.lower() in {"exit", "quit"}:
            print("已退出。")
            break

        response = agent.handle_input(raw)
        print(f"助手> {response}")


if __name__ == "__main__":
    main()
