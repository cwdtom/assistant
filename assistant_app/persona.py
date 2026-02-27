from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Protocol


class PersonaLLMClient(Protocol):
    def reply(self, messages: list[dict[str, str]]) -> str: ...


PERSONA_REWRITE_SYSTEM_PROMPT = """
你是一个“文本风格改写器”。
任务：把输入文本改写成指定人物风格，但必须严格保留原始事实信息。

硬性约束（必须遵守）：
1. 不新增、不删除、不篡改事实。
2. 不改变时间、日期、数字、ID、命令、实体名称。
3. 不改变任务结论与执行状态。
4. 不输出解释、分析、前后缀说明，只输出最终改写文本。
""".strip()


@dataclass
class PersonaRewriter:
    llm_client: PersonaLLMClient | None
    persona: str = ""
    enabled: bool = True
    logger: logging.Logger | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def rewrite_final_response(self, text: str) -> str:
        return self._rewrite_text(text=text, scene="final_response", use_lock=True)

    def rewrite_reminder_content(self, text: str) -> str:
        return self._rewrite_text(text=text, scene="reminder", use_lock=True)

    def rewrite_progress_update(self, text: str) -> str:
        return self._rewrite_text(text=text, scene="progress_update", use_lock=False)

    def _rewrite_text(self, *, text: str, scene: str, use_lock: bool) -> str:
        normalized_text = text.strip()
        if not normalized_text:
            return text
        if not self.enabled:
            return text
        persona = self.persona.strip()
        if not persona or self.llm_client is None:
            return text
        payload = {
            "scene": scene,
            "persona": persona,
            "text": normalized_text,
            "requirements": self._scene_requirements(scene=scene),
        }
        messages = [
            {"role": "system", "content": PERSONA_REWRITE_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            if use_lock:
                with self._lock:
                    rewritten = self.llm_client.reply(messages)
            else:
                rewritten = self.llm_client.reply(messages)
        except Exception as exc:  # noqa: BLE001
            self._log_rewrite_error(scene=scene, error=exc)
            return text
        normalized_rewritten = rewritten.strip()
        if not normalized_rewritten:
            return text
        return normalized_rewritten

    @staticmethod
    def _scene_requirements(*, scene: str) -> list[str]:
        requirements = [
            "保持原文语言",
            "可润色语气与表达顺序，但不得改变事实内容",
            "输出长度控制在原文的 0.7~1.3 倍",
        ]
        if scene == "final_response":
            requirements.extend(
                [
                    "语气更像真人同步结果：先说结论，再补充关键细节",
                    "由你判断是否拆成多条发送；若拆分，请用空行分隔每条内容",
                ]
            )
        if scene == "progress_update":
            requirements.extend(
                [
                    "只输出子任务完成文本本体，不添加任何解释、前后缀或额外包装文案",
                    "可润色语气，但必须完整保留原始子任务名称与完成状态事实",
                ]
            )
        return requirements

    def _log_rewrite_error(self, *, scene: str, error: Exception) -> None:
        logger = self.logger
        if logger is None:
            return
        try:
            logger.warning(
                "persona rewrite failed",
                extra={
                    "event": "persona_rewrite_error",
                    "context": {
                        "scene": scene,
                        "error": repr(error),
                    },
                },
            )
        except Exception:
            return
