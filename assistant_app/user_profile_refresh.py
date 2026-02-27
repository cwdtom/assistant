from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from assistant_app.db import AssistantDB, ChatTurn
from assistant_app.llm import LLMClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent

USER_PROFILE_REFRESH_SYSTEM_PROMPT = """
你是“用户画像维护器”。
目标：基于“当前 user_profile + 最近对话样本”产出一份完整的新 user_profile（Markdown）。

硬性约束（必须遵守）：
1) 仅依据输入信息更新，禁止凭空捏造。
2) 保留稳定且仍然成立的信息；对明显过时或冲突的信息做修订。
3) 若证据不足，不要下结论；可在“待观察”中记录假设。
4) 输出必须是可直接落盘的完整 Markdown 文本，不要输出解释、前后缀、代码块标记。
5) 时间敏感偏好优先参考近 30 天对话中的高频和近期信号。
6) 尽量保持原有 user_profile 的结构与字段命名；若原结构缺失，可补充清晰小节。
""".strip()


@dataclass(frozen=True)
class UserProfileRefreshResult:
    ok: bool
    reason: str
    profile_content: str | None = None
    used_turns: int = 0


class UserProfileRefreshService:
    def __init__(
        self,
        *,
        db: AssistantDB,
        llm_client: LLMClient | None,
        user_profile_path: str,
        agent_reloader: Callable[[], bool],
        logger: logging.Logger | None = None,
        clock: Callable[[], datetime] | None = None,
        scheduled_hour: int = 4,
        lookback_days: int = 30,
        max_turns: int = 10000,
    ) -> None:
        self._db = db
        self._llm_client = llm_client
        self._user_profile_path = user_profile_path
        self._agent_reloader = agent_reloader
        self._clock = clock or datetime.now
        self._logger = logger or logging.getLogger("assistant_app.app")
        if logger is None:
            self._logger.propagate = False
            if not self._logger.handlers:
                self._logger.addHandler(logging.NullHandler())
        self._scheduled_hour = min(max(scheduled_hour, 0), 23)
        self._lookback_days = max(lookback_days, 1)
        self._max_turns = max(max_turns, 1)
        now = self._clock()
        self._last_poll_time = now
        self._last_scheduled_run_date = None

    def poll_scheduled(self) -> None:
        now = self._clock()
        should_run = self._should_run_scheduled(now=now)
        self._last_poll_time = now
        if not should_run:
            return
        self._last_scheduled_run_date = now.date()
        self._run_refresh(trigger="scheduled", now=now)

    def run_manual_refresh(self) -> str:
        result = self._run_refresh(trigger="manual", now=self._clock())
        if result.ok and result.profile_content is not None:
            return result.profile_content
        return result.reason

    def _should_run_scheduled(self, *, now: datetime) -> bool:
        if self._last_scheduled_run_date == now.date():
            return False
        due_time = now.replace(hour=self._scheduled_hour, minute=0, second=0, microsecond=0)
        if self._last_poll_time > now:
            self._last_poll_time = now
            return False
        return self._last_poll_time < due_time <= now

    def _run_refresh(self, *, trigger: str, now: datetime) -> UserProfileRefreshResult:
        profile_path = _resolve_profile_path(self._user_profile_path)
        if profile_path is None:
            return self._skip(
                trigger=trigger,
                reason="未配置 USER_PROFILE_PATH，已跳过 user_profile 刷新。",
                event="user_profile_refresh_path_empty",
            )
        if not profile_path.exists():
            return self._skip(
                trigger=trigger,
                reason=f"未找到 user_profile 文件: {profile_path}",
                event="user_profile_refresh_path_missing",
                path=profile_path,
            )
        llm_client = self._llm_client
        if llm_client is None:
            return self._skip(
                trigger=trigger,
                reason="当前未配置 LLM，无法刷新 user_profile。",
                event="user_profile_refresh_no_llm",
                path=profile_path,
            )
        try:
            current_profile = profile_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            return self._fail(
                trigger=trigger,
                reason=f"读取 user_profile 文件失败: {exc}",
                event="user_profile_refresh_read_failed",
                path=profile_path,
                error=repr(exc),
            )

        turns = self._collect_chat_turns(now=now)
        if not turns:
            return self._skip(
                trigger=trigger,
                reason=f"最近 {self._lookback_days} 天暂无可用对话，已跳过 user_profile 刷新。",
                event="user_profile_refresh_no_turns",
                path=profile_path,
            )

        messages = self._build_messages(current_profile=current_profile, turns=turns, now=now)
        try:
            refreshed = self._reply_with_temperature_zero(llm_client=llm_client, messages=messages).strip()
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                trigger=trigger,
                reason=f"调用 LLM 刷新 user_profile 失败: {exc}",
                event="user_profile_refresh_llm_failed",
                path=profile_path,
                error=repr(exc),
                turns=len(turns),
            )
        if not refreshed:
            return self._fail(
                trigger=trigger,
                reason="LLM 返回空内容，未刷新 user_profile。",
                event="user_profile_refresh_empty_output",
                path=profile_path,
                turns=len(turns),
            )

        try:
            profile_path.write_text(refreshed, encoding="utf-8")
            latest_profile = profile_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return self._fail(
                trigger=trigger,
                reason=f"写回 user_profile 文件失败: {exc}",
                event="user_profile_refresh_write_failed",
                path=profile_path,
                error=repr(exc),
                turns=len(turns),
            )

        try:
            self._agent_reloader()
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                trigger=trigger,
                reason=f"user_profile 文件已更新，但 reload 失败: {exc}",
                event="user_profile_refresh_reload_failed",
                path=profile_path,
                error=repr(exc),
                turns=len(turns),
            )

        self._logger.info(
            "user profile refresh succeeded",
            extra={
                "event": "user_profile_refresh_succeeded",
                "context": {
                    "trigger": trigger,
                    "path": str(profile_path),
                    "turns": len(turns),
                    "lookback_days": self._lookback_days,
                    "max_turns": self._max_turns,
                },
            },
        )
        return UserProfileRefreshResult(
            ok=True,
            reason=f"user_profile 刷新成功（使用 {len(turns)} 条对话）。",
            profile_content=latest_profile,
            used_turns=len(turns),
        )

    def _collect_chat_turns(self, *, now: datetime) -> list[ChatTurn]:
        since = now - timedelta(days=self._lookback_days)
        turns = self._db.recent_turns_since(since=since, limit=self._max_turns)
        return [item for item in turns if item.user_content.strip() or item.assistant_content.strip()]

    def _build_messages(self, *, current_profile: str, turns: list[ChatTurn], now: datetime) -> list[dict[str, str]]:
        payload = {
            "task": "refresh_user_profile",
            "time": {
                "now": now.strftime("%Y-%m-%d %H:%M"),
                "window_days": self._lookback_days,
            },
            "limits": {
                "max_turns": self._max_turns,
                "actual_turns": len(turns),
            },
            "current_user_profile": current_profile,
            "chat_turns": [
                {
                    "created_at": item.created_at,
                    "user_content": item.user_content,
                    "assistant_content": item.assistant_content,
                }
                for item in turns
            ],
            "output_requirements": [
                "输出完整新版 user_profile Markdown",
                "不要输出解释文本",
                "若信息不足，保留原有条目并标注待观察",
            ],
        }
        return [
            {"role": "system", "content": USER_PROFILE_REFRESH_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

    @staticmethod
    def _reply_with_temperature_zero(*, llm_client: LLMClient, messages: list[dict[str, str]]) -> str:
        reply_with_temperature = getattr(llm_client, "reply_with_temperature", None)
        if callable(reply_with_temperature):
            return str(reply_with_temperature(messages, temperature=0.0))
        return str(llm_client.reply(messages))

    def _skip(
        self,
        *,
        trigger: str,
        reason: str,
        event: str,
        path: Path | None = None,
    ) -> UserProfileRefreshResult:
        context: dict[str, object] = {"trigger": trigger}
        if path is not None:
            context["path"] = str(path)
        self._logger.warning(
            "user profile refresh skipped",
            extra={
                "event": event,
                "context": context,
            },
        )
        return UserProfileRefreshResult(ok=False, reason=reason)

    def _fail(
        self,
        *,
        trigger: str,
        reason: str,
        event: str,
        path: Path | None = None,
        error: str | None = None,
        turns: int | None = None,
    ) -> UserProfileRefreshResult:
        context: dict[str, object] = {"trigger": trigger}
        if path is not None:
            context["path"] = str(path)
        if error is not None:
            context["error"] = error
        if turns is not None:
            context["turns"] = turns
        self._logger.warning(
            "user profile refresh failed",
            extra={
                "event": event,
                "context": context,
            },
        )
        return UserProfileRefreshResult(ok=False, reason=reason)


def _resolve_profile_path(raw_path: str) -> Path | None:
    normalized = raw_path.strip()
    if not normalized:
        return None
    path = Path(normalized).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()
