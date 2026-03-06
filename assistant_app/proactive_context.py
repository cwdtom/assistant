from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import logging

from assistant_app.db import AssistantDB, ChatTurn, ScheduleItem

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_USER_PROFILE_MAX_CHARS = 6000


@dataclass(frozen=True)
class ProactiveContextSnapshot:
    now: datetime
    lookahead_hours: int
    chat_lookback_hours: int
    schedules: list[ScheduleItem]
    turns: list[ChatTurn]
    user_profile_path: str
    user_profile_content: str | None

    @property
    def has_user_profile(self) -> bool:
        return bool(self.user_profile_content)

    def to_prompt_payload(
        self,
        *,
        night_quiet_hint: str,
        score_threshold: int,
        max_steps: int,
        internet_search_allowed: bool,
    ) -> dict[str, object]:
        return {
            "task": "proactive_reminder_decision",
            "now": self.now.strftime("%Y-%m-%d %H:%M"),
            "timezone": _local_timezone_name(self.now),
            "policy": {
                "channel": "feishu",
                "target_type": "fixed_open_id",
                "night_quiet_hint": night_quiet_hint,
                "score_threshold": score_threshold,
                "max_steps": max_steps,
                "internet_search_allowed": internet_search_allowed,
            },
            "context_window": {
                "schedule_forward_hours": self.lookahead_hours,
                "chat_history_backward_hours": self.chat_lookback_hours,
            },
            "user_profile": {
                "path": self.user_profile_path,
                "loaded": self.has_user_profile,
                "content": self.user_profile_content or "",
            },
            "internal_context": {
                "schedules": [_schedule_to_payload(item) for item in self.schedules],
                "recent_chat_turns": [_turn_to_payload(item) for item in self.turns],
            },
            "output_contract": {
                "terminal_action": "done",
                "done_schema": {
                    "score": "integer (0~100)",
                    "message": "string",
                    "reason": "string",
                },
            },
        }


def build_proactive_context_snapshot(
    *,
    db: AssistantDB,
    now: datetime,
    lookahead_hours: int,
    chat_lookback_hours: int,
    user_profile_path: str,
    logger: logging.Logger,
    user_profile_max_chars: int = DEFAULT_USER_PROFILE_MAX_CHARS,
) -> ProactiveContextSnapshot:
    normalized_lookahead = max(lookahead_hours, 1)
    normalized_chat_lookback = max(chat_lookback_hours, 1)
    scan_end = now + timedelta(hours=normalized_lookahead)
    window_days = max((normalized_lookahead + 23) // 24, 1)

    schedules = db.list_schedules(
        window_start=now,
        window_end=scan_end,
        max_window_days=window_days,
    )
    turns = db.recent_turns_since(
        since=now - timedelta(hours=normalized_chat_lookback),
        limit=200,
    )
    resolved_profile_path, profile_content = _load_user_profile(
        user_profile_path=user_profile_path,
        logger=logger,
        max_chars=max(user_profile_max_chars, 1),
    )
    return ProactiveContextSnapshot(
        now=now,
        lookahead_hours=normalized_lookahead,
        chat_lookback_hours=normalized_chat_lookback,
        schedules=schedules,
        turns=turns,
        user_profile_path=resolved_profile_path,
        user_profile_content=profile_content,
    )


def _load_user_profile(*, user_profile_path: str, logger: logging.Logger, max_chars: int) -> tuple[str, str | None]:
    normalized_path = user_profile_path.strip()
    if not normalized_path:
        return "", None
    resolved_path = _resolve_profile_path(normalized_path)
    try:
        content = resolved_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        logger.warning(
            "failed to read proactive user profile",
            extra={
                "event": "proactive_user_profile_read_failed",
                "context": {"path": str(resolved_path), "error": repr(exc)},
            },
        )
        return str(resolved_path), None
    if not content:
        return str(resolved_path), None
    if len(content) > max_chars:
        content = content[:max_chars]
    return str(resolved_path), content


def _resolve_profile_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


def _schedule_to_payload(item: ScheduleItem) -> dict[str, object]:
    return {
        "id": item.id,
        "title": item.title,
        "tag": item.tag,
        "event_time": item.event_time,
        "duration_minutes": item.duration_minutes,
        "remind_at": item.remind_at,
        "repeat_interval_minutes": item.repeat_interval_minutes,
        "repeat_times": item.repeat_times,
        "repeat_enabled": item.repeat_enabled,
    }


def _turn_to_payload(item: ChatTurn) -> dict[str, str]:
    return {
        "created_at": item.created_at,
        "user_content": item.user_content,
        "assistant_content": item.assistant_content,
    }
def _local_timezone_name(now: datetime) -> str:
    tzinfo = now.astimezone().tzinfo
    if tzinfo is None:
        return "local"
    tz_name = tzinfo.tzname(now)
    if not tz_name:
        return "local"
    return tz_name
