from __future__ import annotations

import re
from typing import Optional

from assistant_app.db import AssistantDB
from assistant_app.llm import LLMClient

SCHEDULE_ADD_PATTERN = re.compile(r"^/schedule add (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(.+)$")


class AssistantAgent:
    def __init__(self, db: AssistantDB, llm_client: Optional[LLMClient] = None) -> None:
        self.db = db
        self.llm_client = llm_client

    def handle_input(self, user_input: str) -> str:
        text = user_input.strip()
        if not text:
            return "请输入内容。输入 /help 查看可用命令。"

        if text.startswith("/"):
            return self._handle_command(text)

        return self._handle_chat(text)

    def _handle_command(self, command: str) -> str:
        if command == "/help":
            return self._help_text()

        if command.startswith("/todo add "):
            content = command.removeprefix("/todo add ").strip()
            if not content:
                return "用法: /todo add <内容>"
            todo_id = self.db.add_todo(content)
            return f"已添加待办 #{todo_id}: {content}"

        if command == "/todo list":
            todos = self.db.list_todos()
            if not todos:
                return "暂无待办。"
            lines = ["待办列表:"]
            for item in todos:
                status = "x" if item.done else " "
                lines.append(f"- [{status}] {item.id}. {item.content}")
            return "\n".join(lines)

        if command.startswith("/todo done "):
            id_text = command.removeprefix("/todo done ").strip()
            if not id_text.isdigit():
                return "用法: /todo done <id>"
            done = self.db.mark_todo_done(int(id_text))
            if not done:
                return f"未找到待办 #{id_text}"
            return f"待办 #{id_text} 已完成。"

        if command == "/schedule list":
            items = self.db.list_schedules()
            if not items:
                return "暂无日程。"
            lines = ["日程列表:"]
            for item in items:
                lines.append(f"- {item.id}. {item.event_time} | {item.title}")
            return "\n".join(lines)

        if command.startswith("/schedule add"):
            matched = SCHEDULE_ADD_PATTERN.match(command)
            if not matched:
                return "用法: /schedule add <YYYY-MM-DD HH:MM> <标题>"
            event_time = matched.group(1)
            title = matched.group(2).strip()
            schedule_id = self.db.add_schedule(title=title, event_time=event_time)
            return f"已添加日程 #{schedule_id}: {event_time} {title}"

        return "未知命令。输入 /help 查看可用命令。"

    def _handle_chat(self, text: str) -> str:
        if not self.llm_client:
            return "当前未配置 LLM。请设置 OPENAI_API_KEY 后重试。"

        self.db.save_message("user", text)

        messages = [
            {
                "role": "system",
                "content": self._build_system_prompt(),
            }
        ]

        for item in self.db.recent_messages(limit=8):
            messages.append({"role": item.role, "content": item.content})

        try:
            answer = self.llm_client.reply(messages)
        except Exception as exc:  # noqa: BLE001
            return f"调用模型失败: {exc}"

        if not answer:
            answer = "我这次没有拿到有效回复，可以再试一次。"

        self.db.save_message("assistant", answer)
        return answer

    def _build_system_prompt(self) -> str:
        todos = [item for item in self.db.list_todos() if not item.done][:5]
        schedules = self.db.list_schedules()[:5]

        todo_lines = "\n".join(f"- {item.id}. {item.content}" for item in todos) or "- 无"
        schedule_lines = (
            "\n".join(f"- {item.event_time} {item.title}" for item in schedules) or "- 无"
        )

        return (
            "你是一个中文优先的个人助手。回答尽量简洁、可执行。\n"
            "你可以参考当前用户的本地事项：\n"
            f"待办（未完成）:\n{todo_lines}\n"
            f"日程（按时间排序）:\n{schedule_lines}\n"
            "如果用户问到计划安排，优先结合这些事项给建议。"
        )

    @staticmethod
    def _help_text() -> str:
        return (
            "可用命令:\n"
            "/help\n"
            "/todo add <内容>\n"
            "/todo list\n"
            "/todo done <id>\n"
            "/schedule add <YYYY-MM-DD HH:MM> <标题>\n"
            "/schedule list\n"
            "其他文本会直接发给 AI。"
        )
