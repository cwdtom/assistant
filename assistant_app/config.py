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
    plan_replan_max_steps: int
    plan_replan_retry_count: int
    plan_observation_char_limit: int
    plan_observation_history_limit: int
    plan_continuous_failure_limit: int
    task_cancel_command: str
    internet_search_top_k: int
    schedule_max_window_days: int
    infinite_repeat_conflict_preview_days: int
    cli_progress_color: str


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
        plan_replan_max_steps=_read_env_int("PLAN_REPLAN_MAX_STEPS", default=20, min_value=1),
        plan_replan_retry_count=_read_env_int("PLAN_REPLAN_RETRY_COUNT", default=2, min_value=0),
        plan_observation_char_limit=_read_env_int("PLAN_OBSERVATION_CHAR_LIMIT", default=10000, min_value=1),
        plan_observation_history_limit=_read_env_int("PLAN_OBSERVATION_HISTORY_LIMIT", default=100, min_value=1),
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
