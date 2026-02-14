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
    def reply(self, messages: list[dict[str, str]]) -> str:
        ...


@dataclass
class OpenAICompatibleClient:
    api_key: str
    base_url: str
    model: str
    timeout: float = 30.0

    def reply(self, messages: list[dict[str, str]]) -> str:
        return self._create_reply(messages=messages, temperature=0.3)

    def reply_json(self, messages: list[dict[str, str]]) -> str:
        return self._create_reply(
            messages=messages,
            temperature=0.0,
            response_format={"type": "json_object"},
        )

    def _create_reply(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError(
                "openai SDK 未安装，请先执行: pip install -e ."
            ) from exc

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
