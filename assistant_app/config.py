from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    api_key: str | None
    base_url: str
    model: str
    db_path: str
    llm_trace_log_path: str
    plan_replan_max_steps: int
    plan_replan_retry_count: int
    plan_observation_char_limit: int
    plan_continuous_failure_limit: int
    task_cancel_command: str
    internet_search_top_k: int
    schedule_max_window_days: int
    infinite_repeat_conflict_preview_days: int
    cli_progress_color: str
    timer_enabled: bool
    timer_poll_interval_seconds: int
    timer_lookahead_seconds: int
    timer_catchup_seconds: int
    timer_batch_limit: int
    reminder_delivery_retention_days: int


def load_env_file(env_path: str = ".env") -> None:
    """Minimal .env loader to avoid extra dependency for MVP."""
    path = Path(env_path)
    if not path.exists() or not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(load_dotenv: bool = True) -> AppConfig:
    if load_dotenv:
        load_env_file()
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("DEEPSEEK_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com"
    model = os.getenv("DEEPSEEK_MODEL") or os.getenv("OPENAI_MODEL") or "deepseek-chat"
    task_cancel_command = (os.getenv("TASK_CANCEL_COMMAND") or "取消当前任务").strip() or "取消当前任务"
    return AppConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        db_path=os.getenv("ASSISTANT_DB_PATH", "assistant.db"),
        llm_trace_log_path=_read_env_text("LLM_TRACE_LOG_PATH", default="logs/llm_trace.log"),
        plan_replan_max_steps=_read_env_int("PLAN_REPLAN_MAX_STEPS", default=20, min_value=1),
        plan_replan_retry_count=_read_env_int("PLAN_REPLAN_RETRY_COUNT", default=2, min_value=0),
        plan_observation_char_limit=_read_env_int("PLAN_OBSERVATION_CHAR_LIMIT", default=10000, min_value=1),
        plan_continuous_failure_limit=_read_env_int("PLAN_CONTINUOUS_FAILURE_LIMIT", default=2, min_value=1),
        task_cancel_command=task_cancel_command,
        internet_search_top_k=_read_env_int("INTERNET_SEARCH_TOP_K", default=3, min_value=1),
        schedule_max_window_days=_read_env_int("SCHEDULE_MAX_WINDOW_DAYS", default=31, min_value=1),
        infinite_repeat_conflict_preview_days=_read_env_int(
            "INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS",
            default=31,
            min_value=1,
        ),
        cli_progress_color=(os.getenv("CLI_PROGRESS_COLOR") or "gray").strip().lower() or "gray",
        timer_enabled=_read_env_bool("TIMER_ENABLED", default=True),
        timer_poll_interval_seconds=_read_env_int("TIMER_POLL_INTERVAL_SECONDS", default=15, min_value=1),
        timer_lookahead_seconds=_read_env_int("TIMER_LOOKAHEAD_SECONDS", default=30, min_value=0),
        timer_catchup_seconds=0,
        timer_batch_limit=_read_env_int("TIMER_BATCH_LIMIT", default=200, min_value=1),
        reminder_delivery_retention_days=_read_env_int("REMINDER_DELIVERY_RETENTION_DAYS", default=30, min_value=1),
    )


def _read_env_int(name: str, *, default: int, min_value: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        return default
    if value < min_value:
        return default
    return value


def _read_env_text(name: str, *, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip()


def _read_env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default
