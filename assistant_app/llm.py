from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Protocol

from assistant_app.schemas.planner import (
    ToolReplyPayload,
    normalize_assistant_tool_message,
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
    timeout: float = 30.0

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
    ) -> dict[str, Any]:
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
            if not resp.choices:
                raise RuntimeError("LLM 返回为空，请检查模型或 API 配置")
            message = resp.choices[0].message
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
        choices = resp.get("choices", []) if isinstance(resp, dict) else []
        if not choices:
            raise RuntimeError("LLM 返回为空，请检查模型或 API 配置")
        message = choices[0].get("message", {})
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

            if not resp.choices:
                raise RuntimeError("LLM 返回为空，请检查模型或 API 配置")

            content = resp.choices[0].message.content
            if isinstance(content, str):
                return content.strip()

            # Some OpenAI-compatible providers may return structured content blocks.
            if content is None:
                return ""

            return str(content).strip()

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

        choices = resp.get("choices", []) if isinstance(resp, dict) else []
        if not choices:
            raise RuntimeError("LLM 返回为空，请检查模型或 API 配置")

        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if content is None:
            return ""
        return str(content).strip()

    @staticmethod
    def _build_tool_reply_payload(message: Any) -> dict[str, Any]:
        message_payload = OpenAICompatibleClient._to_plain_message(message)
        reasoning = message_payload.get("reasoning_content")
        reasoning_text: str | None
        if reasoning is None:
            reasoning_text = None
        elif isinstance(reasoning, str):
            reasoning_text = reasoning
        else:
            reasoning_text = str(reasoning)

        assistant_message = normalize_assistant_tool_message(
            message_payload,
            plain_tool_call_converter=OpenAICompatibleClient._to_plain_tool_call,
        )
        if assistant_message is None:
            assistant_message = normalize_assistant_tool_message(
                {"role": "assistant", "content": None, "tool_calls": []}
            )
            assert assistant_message is not None
        payload = ToolReplyPayload.model_validate(
            {
                "assistant_message": assistant_message,
                "reasoning_content": reasoning_text,
            }
        )
        return payload.model_dump()

    @staticmethod
    def _to_plain_message(message: Any) -> dict[str, Any]:
        if isinstance(message, dict):
            return dict(message)
        model_dump = getattr(message, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        return {
            "role": getattr(message, "role", "assistant"),
            "content": getattr(message, "content", None),
            "tool_calls": getattr(message, "tool_calls", None),
            "reasoning_content": getattr(message, "reasoning_content", None),
        }

    @staticmethod
    def _to_plain_tool_call(tool_call: Any) -> dict[str, Any]:
        if isinstance(tool_call, dict):
            return dict(tool_call)
        model_dump = getattr(tool_call, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        function_payload = getattr(tool_call, "function", None)
        if isinstance(function_payload, dict):
            function = function_payload
        else:
            function = {
                "name": getattr(function_payload, "name", ""),
                "arguments": getattr(function_payload, "arguments", "{}"),
            }
        return {
            "id": getattr(tool_call, "id", ""),
            "type": getattr(tool_call, "type", "function"),
            "function": function,
        }
