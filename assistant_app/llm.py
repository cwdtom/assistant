from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


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
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai SDK 未安装，请先执行: pip install -e ."
            ) from exc

        client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        resp = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0.3,
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
