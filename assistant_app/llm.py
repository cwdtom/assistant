from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any, Protocol

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
    temperature: float = 0.3
    timeout: float = 30.0

    def reply(self, messages: list[dict[str, Any]]) -> str:
        return self._create_reply(messages=messages, temperature=self.temperature)

    def reply_json(self, messages: list[dict[str, Any]]) -> str:
        return self._create_reply(
            messages=messages,
            temperature=self.temperature,
            response_format={"type": "json_object"},
        )

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
        content = message_payload.get("content")
        content_text: str | None
        if content is None:
            content_text = None
        elif isinstance(content, str):
            content_text = content
        else:
            content_text = str(content)

        reasoning = message_payload.get("reasoning_content")
        reasoning_text: str | None
        if reasoning is None:
            reasoning_text = None
        elif isinstance(reasoning, str):
            reasoning_text = reasoning
        else:
            reasoning_text = str(reasoning)

        return {
            "assistant_message": {
                "role": "assistant",
                "content": content_text,
                "tool_calls": OpenAICompatibleClient._normalize_tool_calls(message_payload.get("tool_calls")),
            },
            "reasoning_content": reasoning_text,
        }

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
    def _normalize_tool_calls(raw_tool_calls: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_tool_calls, list):
            return []
        normalized: list[dict[str, Any]] = []
        for raw in raw_tool_calls:
            payload = OpenAICompatibleClient._to_plain_tool_call(raw)
            function = payload.get("function")
            if not isinstance(function, dict):
                continue
            name = str(function.get("name") or "").strip()
            arguments = function.get("arguments")
            if arguments is None:
                arguments = "{}"
            elif not isinstance(arguments, str):
                arguments = str(arguments)
            normalized.append(
                {
                    "id": str(payload.get("id") or ""),
                    "type": str(payload.get("type") or "function"),
                    "function": {"name": name, "arguments": arguments},
                }
            )
        return normalized

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
