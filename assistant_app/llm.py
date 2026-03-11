from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Protocol

from assistant_app.schemas.llm import (
    LLMAssistantMessageCompat,
    parse_assistant_message,
    parse_chat_completion_response,
)
from assistant_app.schemas.planner import (
    ToolReplyPayload,
    normalize_assistant_tool_message,
    parse_tool_reply_payload,
)

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 v2 only supports OpenSSL 1\.1\.1\+.*",
)

try:
    from urllib3.exceptions import NotOpenSSLWarning

    warnings.filterwarnings("ignore", category=NotOpenSSLWarning)
except Exception:
    # Keep startup resilient when urllib3 internals differ.
    pass


class LLMClient(Protocol):
    def reply(self, messages: list[dict[str, Any]]) -> str: ...


@dataclass
class OpenAICompatibleClient:
    api_key: str
    base_url: str
    model: str
    temperature: float = 1.3
    timeout: float = 60.0

    def reply(self, messages: list[dict[str, Any]]) -> str:
        return self._create_reply(messages=messages, temperature=self.temperature)

    def reply_json(self, messages: list[dict[str, Any]]) -> str:
        return self._create_reply(
            messages=messages,
            temperature=self.temperature,
            response_format={"type": "json_object"},
        )

    def reply_with_temperature(self, messages: list[dict[str, Any]], *, temperature: float) -> str:
        return self._create_reply(messages=messages, temperature=temperature)

    def reply_with_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
    ) -> ToolReplyPayload:
        if "reasoner" in self.model.strip().lower():
            raise RuntimeError("thought 阶段暂不支持 thinking 模式（例如 deepseek-reasoner）。")

        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("openai SDK 未安装，请先执行: pip install -e .") from exc

        if hasattr(openai, "OpenAI"):
            client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
            resp = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                temperature=self.temperature,
            )
            message = self._first_message_from_response(resp)
            return self._build_tool_reply_payload(message)

        # Legacy openai SDK (e.g. 0.28.x)
        openai.api_key = self.api_key
        if hasattr(openai, "api_base"):
            openai.api_base = self.base_url

        resp = openai.ChatCompletion.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=self.temperature,
        )
        message = self._first_message_from_response(resp)
        return self._build_tool_reply_payload(message)

    def _create_reply(
        self,
        messages: list[dict[str, Any]],
        temperature: float,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError("openai SDK 未安装，请先执行: pip install -e .") from exc

        if hasattr(openai, "OpenAI"):
            client = openai.OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
            kwargs: dict[str, Any] = {}
            if response_format is not None:
                kwargs["response_format"] = response_format

            resp = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                **kwargs,
            )
            return self._first_message_from_response(resp).content_text()

        # Legacy openai SDK (e.g. 0.28.x)
        openai.api_key = self.api_key
        if hasattr(openai, "api_base"):
            openai.api_base = self.base_url

        kwargs = {}
        if response_format is not None:
            kwargs["response_format"] = response_format

        try:
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                **kwargs,
            )
        except Exception:
            # Some compatible providers / old SDKs don't support response_format.
            resp = openai.ChatCompletion.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
        return self._first_message_from_response(resp).content_text()

    @staticmethod
    def _build_tool_reply_payload(message: Any) -> ToolReplyPayload:
        normalized_message = parse_assistant_message(message)
        message_payload = normalized_message.to_plain_payload()
        reasoning_text = normalized_message.reasoning_text()
        payload = parse_tool_reply_payload(
            {
                "assistant_message": message_payload,
                "reasoning_content": reasoning_text,
            },
        )
        if payload is None:
            payload = ToolReplyPayload.model_validate(
                {
                    "assistant_message": normalize_assistant_tool_message(
                        {"role": "assistant", "content": None, "tool_calls": []}
                    ),
                    "reasoning_content": reasoning_text,
                }
            )
        return payload

    @staticmethod
    def _first_message_from_response(response: Any) -> LLMAssistantMessageCompat:
        try:
            return parse_chat_completion_response(response).first_message()
        except Exception as exc:
            raise RuntimeError("LLM 返回为空，请检查模型或 API 配置") from exc
