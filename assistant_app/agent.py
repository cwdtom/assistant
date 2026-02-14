from __future__ import annotations

import json
import re
from typing import Any, Optional

from assistant_app.db import AssistantDB
from assistant_app.llm import LLMClient

SCHEDULE_ADD_PATTERN = re.compile(r"^/schedule add (\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(.+)$")
INTENT_LABELS = (
    "todo_add",
    "todo_list",
    "todo_done",
    "schedule_add",
    "schedule_list",
    "chat",
)
INTENT_JSON_RETRY_COUNT = 2
INTENT_SERVICE_UNAVAILABLE = "__intent_service_unavailable__"

INTENT_ANALYZE_PROMPT = """
你是 CLI 助手的意图识别器。你必须只输出一个 json 对象，不能输出任何其他内容。

可选 intent:
- todo_add
- todo_list
- todo_done
- schedule_add
- schedule_list
- chat

输出 JSON 字段固定为:
{{
  "intent": "todo_add|todo_list|todo_done|schedule_add|schedule_list|chat",
  "todo_content": "string|null",
  "todo_tag": "string|null",
  "todo_id": "number|null",
  "event_time": "YYYY-MM-DD HH:MM|null",
  "title": "string|null"
}}

规则:
- 只在用户明确要操作待办/日程时，返回对应 intent，否则 intent=chat
- 与 intent 无关的字段必须填 null
- 缺少或不确定的字段填 null
- event_time 必须是 YYYY-MM-DD HH:MM，无法确定就填 null
""".strip()


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

        if not self.llm_client:
            return "当前未配置 LLM。请设置 DEEPSEEK_API_KEY 后重试。"

        intent_payload = self._analyze_intent(text)
        intent_response = self._dispatch_intent(intent_payload)
        if intent_response is not None:
            return intent_response

        return self._handle_chat(text)

    def _handle_command(self, command: str) -> str:
        if command == "/help":
            return self._help_text()

        if command.startswith("/todo add "):
            parsed = _parse_todo_add_input(command.removeprefix("/todo add ").strip())
            if parsed is None:
                return "用法: /todo add <内容> [--tag <标签>]"
            content, tag = parsed
            if not content:
                return "用法: /todo add <内容> [--tag <标签>]"
            todo_id = self.db.add_todo(content, tag=tag)
            return f"已添加待办 #{todo_id} [标签:{tag}]: {content}"

        if command == "/todo list" or command.startswith("/todo list "):
            parsed_tag = _parse_todo_list_tag(command)
            if parsed_tag is _INVALID_TODO_TAG:
                return "用法: /todo list [--tag <标签>]"
            tag = parsed_tag
            todos = self.db.list_todos(tag=tag)
            if not todos:
                if tag is None:
                    return "暂无待办。"
                return f"标签 {tag} 下暂无待办。"

            header = f"待办列表(标签: {tag}):" if tag is not None else "待办列表:"
            lines = [header]
            for item in todos:
                status = "x" if item.done else " "
                lines.append(f"- [{status}] {item.id}. [{item.tag}] {item.content}")
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

    def _analyze_intent(self, text: str) -> dict[str, Any]:
        if not self.llm_client:
            return {"intent": "chat"}

        messages = [
            {"role": "system", "content": INTENT_ANALYZE_PROMPT},
            {"role": "user", "content": text},
        ]
        max_attempts = 1 + INTENT_JSON_RETRY_COUNT

        for _ in range(max_attempts):
            try:
                raw = self._llm_reply_for_intent(messages)
            except Exception:
                continue

            cleaned = _strip_think_blocks(raw).strip()
            payload = _try_parse_json(cleaned)
            if isinstance(payload, dict):
                intent = str(payload.get("intent", "")).strip().lower()
                if intent not in INTENT_LABELS:
                    intent = "chat"
                payload["intent"] = intent
                if self._intent_requires_retry(payload):
                    continue
                return payload

        return {"intent": INTENT_SERVICE_UNAVAILABLE}

    def _intent_requires_retry(self, payload: dict[str, Any]) -> bool:
        intent = str(payload.get("intent", "chat")).strip().lower()

        if intent == "todo_add":
            content = str(payload.get("todo_content") or "").strip()
            return not content

        if intent == "todo_done":
            todo_id = payload.get("todo_id")
            if todo_id is None:
                return True
            try:
                return self._to_int(todo_id) <= 0
            except (TypeError, ValueError):
                return True

        if intent == "schedule_add":
            event_time = str(payload.get("event_time") or "").strip()
            title = str(payload.get("title") or "").strip()
            if not event_time or not title:
                return True
            return re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", event_time) is None

        return False

    def _llm_reply_for_intent(self, messages: list[dict[str, str]]) -> str:
        if self.llm_client is None:
            return ""

        reply_json = getattr(self.llm_client, "reply_json", None)
        if callable(reply_json):
            try:
                return str(reply_json(messages))
            except Exception:
                # Some OpenAI-compatible providers don't support response_format.
                pass
        return self.llm_client.reply(messages)

    def _dispatch_intent(self, payload: dict[str, Any]) -> str | None:
        intent = str(payload.get("intent", "chat")).lower()

        if intent == INTENT_SERVICE_UNAVAILABLE:
            return (
                "抱歉，当前意图识别服务暂时不可用。"
                "你可以稍后重试，或先使用 /todo、/schedule 命令继续操作。"
            )

        if intent == "todo_list":
            tag = _normalize_todo_tag_value(payload.get("todo_tag"))
            if tag is None:
                return self._handle_command("/todo list")
            return self._handle_command(f"/todo list --tag {tag}")

        if intent == "schedule_list":
            return self._handle_command("/schedule list")

        if intent == "todo_add":
            content = str(payload.get("todo_content") or "").strip()
            if not content:
                return "我识别到你可能要添加待办，但缺少内容。请再说具体事项。"
            tag = _normalize_todo_tag_value(payload.get("todo_tag")) or "default"
            return self._handle_command(f"/todo add {content} --tag {tag}")

        if intent == "todo_done":
            todo_id = payload.get("todo_id")
            if todo_id is None:
                return "我识别到你可能要完成待办，但缺少编号。请告诉我待办 id。"
            return self._handle_command(f"/todo done {self._to_int(todo_id)}")

        if intent == "schedule_add":
            event_time = str(payload.get("event_time") or "").strip()
            title = str(payload.get("title") or "").strip()
            if not event_time or not title:
                return "我识别到你可能要添加日程，但时间或标题不完整。"
            return self._handle_command(f"/schedule add {event_time} {title}")

        return None

    @staticmethod
    def _to_int(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        value_str = str(value).strip()
        if not value_str:
            return 0
        return int(float(value_str))

    def _handle_chat(self, text: str) -> str:
        if not self.llm_client:
            return "当前未配置 LLM。请设置 DEEPSEEK_API_KEY 后重试。"

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

        answer = _strip_think_blocks(answer).strip()
        if not answer:
            answer = "我这次没有拿到有效回复，可以再试一次。"

        self.db.save_message("assistant", answer)
        return answer

    def _build_system_prompt(self) -> str:
        todos = [item for item in self.db.list_todos() if not item.done][:5]
        schedules = self.db.list_schedules()[:5]

        todo_lines = "\n".join(f"- {item.id}. [{item.tag}] {item.content}" for item in todos) or "- 无"
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
            "/todo add <内容> [--tag <标签>]\n"
            "/todo list [--tag <标签>]\n"
            "/todo done <id>\n"
            "/schedule add <YYYY-MM-DD HH:MM> <标题>\n"
            "/schedule list\n"
            "你也可以直接说自然语言（会先做意图识别，再执行动作）。\n"
            "其他文本会直接发给 AI。"
        )


_INVALID_TODO_TAG = object()


def _parse_todo_add_input(raw: str) -> tuple[str, str] | None:
    text = raw.strip()
    if not text:
        return None

    head = re.match(r"^--tag\s+(\S+)\s+(.+)$", text)
    if head:
        tag = _sanitize_tag(head.group(1))
        content = head.group(2).strip()
        if not tag or not content:
            return None
        return content, tag

    tail = re.match(r"^(.+?)\s+--tag\s+(\S+)$", text)
    if tail:
        content = tail.group(1).strip()
        tag = _sanitize_tag(tail.group(2))
        if not tag or not content:
            return None
        return content, tag

    return text, "default"


def _parse_todo_list_tag(command: str) -> str | None | object:
    if command == "/todo list":
        return None

    suffix = command.removeprefix("/todo list").strip()
    if not suffix:
        return None
    if suffix == "--tag":
        return _INVALID_TODO_TAG

    option_match = re.match(r"^--tag\s+(\S+)$", suffix)
    if option_match:
        tag = _sanitize_tag(option_match.group(1))
        return tag if tag else _INVALID_TODO_TAG

    if " " in suffix:
        return _INVALID_TODO_TAG

    tag = _sanitize_tag(suffix)
    return tag if tag else _INVALID_TODO_TAG


def _sanitize_tag(tag: str | None) -> str | None:
    if tag is None:
        return None
    normalized = tag.strip().lower()
    if not normalized:
        return None
    normalized = normalized.lstrip("#")
    if not normalized:
        return None
    return re.sub(r"\s+", "-", normalized)


def _normalize_todo_tag_value(value: Any) -> str | None:
    if value is None:
        return None
    return _sanitize_tag(str(value))


def _strip_think_blocks(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.IGNORECASE | re.DOTALL)


def _extract_intent_label(text: str) -> str | None:
    cleaned = text.strip().lower()
    if not cleaned:
        return None

    parsed = _try_parse_json(cleaned)
    if isinstance(parsed, dict):
        candidate = str(parsed.get("intent", "")).strip().lower()
        if candidate in INTENT_LABELS:
            return candidate

    for label in INTENT_LABELS:
        if re.search(rf"\b{re.escape(label)}\b", cleaned):
            return label

    # If the model returns only one token-like word, accept it when possible.
    token = cleaned.split()[0]
    if token in INTENT_LABELS:
        return token

    # Semantic fallback from model free-form output.
    has_todo = "待办" in cleaned or "todo" in cleaned
    has_schedule = "日程" in cleaned or "schedule" in cleaned

    list_words = ("查看", "看一下", "列表", "记录", "还有", "当前", "列出", "如下", "清单", "汇总", "扫")
    add_words = ("添加", "新增", "增加", "记下", "记录了", "创建", "已添加", "已更新", "加", "加进去")
    done_words = ("完成", "done", "标记完成")

    if has_todo and any(word in cleaned for word in list_words):
        return "todo_list"
    if has_todo and any(word in cleaned for word in done_words):
        return "todo_done"
    if has_todo and any(word in cleaned for word in add_words):
        return "todo_add"

    if has_schedule and any(word in cleaned for word in list_words):
        return "schedule_list"
    if has_schedule and any(word in cleaned for word in add_words):
        return "schedule_add"

    return None


def _try_parse_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None

    cleaned = text.strip()
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    return None


def _extract_args_from_text(text: str, intent: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "todo_content": None,
        "todo_id": None,
        "event_time": None,
        "title": None,
    }
    cleaned = text.strip()

    if intent == "todo_add":
        extracted = _extract_todo_content(cleaned)
        if extracted:
            payload["todo_content"] = extracted
        return payload

    if intent == "todo_done":
        matched = re.search(r"(?:待办|todo)\s*#?\s*(\d+)", cleaned, flags=re.IGNORECASE)
        if matched:
            payload["todo_id"] = int(matched.group(1))
        return payload

    if intent == "schedule_add":
        time_match = re.search(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}", cleaned)
        if time_match:
            payload["event_time"] = time_match.group(0).replace("  ", " ")

        title_patterns = [
            r"(?:事项|标题)[:：]\s*[`\"“”']?([^`\"“”'\n。]+)",
            r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s*[，, ]\s*([^。\n]+)",
        ]
        for pattern in title_patterns:
            matched = re.search(pattern, cleaned)
            if matched:
                payload["title"] = matched.group(1).strip().strip("。")
                break
        return payload

    return payload


def _extract_todo_content(text: str) -> str | None:
    candidates: list[str] = []

    quoted = re.findall(r"[\"“”'`](.+?)[\"“”'`]", text)
    candidates.extend(item.strip() for item in quoted if item.strip())

    patterns = [
        r"(?:添加|增加|新增|创建|记下|记录|加个|加一个)(?:一条|一个|条)?(?:测试)?待办[，,:：\s]*(.+)$",
        r"(?:帮我|请)?(?:把)?待办[，,:：\s]*(.+)$",
        r"todo[，,:：\s]*(.+)$",
        r"把(.+?)(?:加进去|加入|记下|添加)$",
    ]
    for pattern in patterns:
        matched = re.search(pattern, text, flags=re.IGNORECASE)
        if matched:
            value = matched.group(1).strip()
            if value:
                candidates.append(value)

    for raw in candidates:
        normalized = raw.strip("，,。；;：: ")
        normalized = re.sub(r"\s+", " ", normalized).strip()
        # Ignore obvious non-task artifacts from hallucinated analysis text.
        if re.fullmatch(r"\.[a-z0-9]{1,8}", normalized, flags=re.IGNORECASE):
            continue
        if normalized:
            return normalized
    return None
