from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from assistant_app.db import AssistantDB
from assistant_app.schemas.proactive import ProactiveContextSnapshot

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_USER_PROFILE_MAX_CHARS = 6000


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
