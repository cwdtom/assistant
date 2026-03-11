from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any, cast

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

UNKNOWN_APP_VERSION = "unknown"
DEFAULT_TASK_CANCEL_COMMAND = "取消当前任务"
_PROJECT_VERSION_PATTERN = re.compile(r"""^version\s*=\s*["']([^"']+)["']\s*$""")
_REMOVED_CONFIG_FIELDS = {
    "proactive_reminder_score_threshold",
    "proactive_reminder_interval_minutes",
    "proactive_reminder_lookahead_hours",
    "proactive_reminder_night_quiet_hint",
    "feishu_calendar_reconcile_interval_minutes",
    "user_profile_refresh_enabled",
    "user_profile_refresh_hour",
    "user_profile_refresh_lookback_days",
    "user_profile_refresh_max_turns",
}


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    api_key: str | None = Field(default=None, validation_alias="DEEPSEEK_API_KEY")
    base_url: str = Field(default="https://api.deepseek.com", validation_alias="DEEPSEEK_BASE_URL")
    model: str = Field(default="deepseek-chat", validation_alias="DEEPSEEK_MODEL")
    llm_temperature: float = Field(default=1.3, ge=0.0, le=2.0, validation_alias="LLM_TEMPERATURE")
    db_path: str = Field(default="assistant.db", validation_alias="ASSISTANT_DB_PATH")
    user_profile_path: str = Field(default="", validation_alias="USER_PROFILE_PATH")
    llm_trace_log_path: str = Field(default="", validation_alias="LLM_TRACE_LOG_PATH")
    app_log_path: str = Field(default="logs/app.log", validation_alias="APP_LOG_PATH")
    app_log_retention_days: int = Field(default=7, ge=1, validation_alias="APP_LOG_RETENTION_DAYS")
    plan_replan_max_steps: int = Field(default=100, ge=1, validation_alias="PLAN_REPLAN_MAX_STEPS")
    plan_replan_retry_count: int = Field(default=3, ge=0, validation_alias="PLAN_REPLAN_RETRY_COUNT")
    plan_observation_char_limit: int = Field(default=10000, ge=1, validation_alias="PLAN_OBSERVATION_CHAR_LIMIT")
    plan_observation_history_limit: int = Field(
        default=100,
        ge=1,
        validation_alias="PLAN_OBSERVATION_HISTORY_LIMIT",
    )
    plan_continuous_failure_limit: int = Field(
        default=3,
        ge=1,
        validation_alias="PLAN_CONTINUOUS_FAILURE_LIMIT",
    )
    task_cancel_command: str = Field(default=DEFAULT_TASK_CANCEL_COMMAND, validation_alias="TASK_CANCEL_COMMAND")
    internet_search_top_k: int = Field(default=3, ge=1, validation_alias="INTERNET_SEARCH_TOP_K")
    search_provider: str = Field(default="bocha", validation_alias="SEARCH_PROVIDER")
    bocha_api_key: str | None = Field(default=None, validation_alias="BOCHA_API_KEY")
    bocha_search_summary: bool = Field(default=True, validation_alias="BOCHA_SEARCH_SUMMARY")
    schedule_max_window_days: int = Field(default=31, ge=1, validation_alias="SCHEDULE_MAX_WINDOW_DAYS")
    cli_progress_color: str = Field(default="gray", validation_alias="CLI_PROGRESS_COLOR")
    timer_enabled: bool = Field(default=True, validation_alias="TIMER_ENABLED")
    timer_poll_interval_seconds: int = Field(default=15, ge=1, validation_alias="TIMER_POLL_INTERVAL_SECONDS")
    timer_lookahead_seconds: int = Field(default=30, ge=1, validation_alias="TIMER_LOOKAHEAD_SECONDS")
    timer_batch_limit: int = Field(default=200, ge=1, validation_alias="TIMER_BATCH_LIMIT")
    persona_rewrite_enabled: bool = Field(default=True, validation_alias="PERSONA_REWRITE_ENABLED")
    assistant_persona: str = Field(default="", validation_alias="ASSISTANT_PERSONA")
    feishu_app_id: str = Field(default="", validation_alias="FEISHU_APP_ID")
    feishu_app_secret: str = Field(default="", validation_alias="FEISHU_APP_SECRET")
    feishu_allowed_open_ids: Annotated[tuple[str, ...], NoDecode] = Field(
        default=(),
        validation_alias="FEISHU_ALLOWED_OPEN_IDS",
    )
    feishu_send_retry_count: int = Field(default=3, ge=0, validation_alias="FEISHU_SEND_RETRY_COUNT")
    feishu_text_chunk_size: int = Field(default=5000, ge=1, validation_alias="FEISHU_TEXT_CHUNK_SIZE")
    feishu_dedup_ttl_seconds: int = Field(default=600, ge=1, validation_alias="FEISHU_DEDUP_TTL_SECONDS")
    feishu_log_path: str = Field(default="", validation_alias="FEISHU_LOG_PATH")
    feishu_log_retention_days: int = Field(default=7, ge=1, validation_alias="FEISHU_LOG_RETENTION_DAYS")
    feishu_ack_reaction_enabled: bool = Field(default=True, validation_alias="FEISHU_ACK_REACTION_ENABLED")
    feishu_ack_emoji_type: str = Field(default="Get", validation_alias="FEISHU_ACK_EMOJI_TYPE")
    feishu_done_emoji_type: str = Field(default="DONE", validation_alias="FEISHU_DONE_EMOJI_TYPE")
    feishu_calendar_id: str = Field(default="", validation_alias="FEISHU_CALENDAR_ID")
    feishu_calendar_bootstrap_past_days: int = Field(
        default=2,
        ge=0,
        validation_alias="FEISHU_CALENDAR_BOOTSTRAP_PAST_DAYS",
    )
    feishu_calendar_bootstrap_future_days: int = Field(
        default=5,
        ge=0,
        validation_alias="FEISHU_CALENDAR_BOOTSTRAP_FUTURE_DAYS",
    )
    proactive_reminder_target_open_id: str = Field(
        default="",
        validation_alias="PROACTIVE_REMINDER_TARGET_OPEN_ID",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        return (
            init_settings,
            _filter_removed_settings_source(dotenv_settings),
            _filter_removed_settings_source(env_settings),
            file_secret_settings,
        )

    @field_validator("api_key", "base_url", "model", mode="before")
    @classmethod
    def _default_blank_core_text(cls, value: Any, info: Any) -> Any:
        if not isinstance(value, str):
            return value
        if value.strip():
            return value
        defaults = {
            "api_key": None,
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
        }
        return defaults[info.field_name]

    @field_validator("task_cancel_command", mode="before")
    @classmethod
    def _default_blank_task_cancel_command(cls, value: Any) -> Any:
        if value is None:
            return DEFAULT_TASK_CANCEL_COMMAND
        if isinstance(value, str) and not value.strip():
            return DEFAULT_TASK_CANCEL_COMMAND
        return value

    @field_validator("search_provider", mode="before")
    @classmethod
    def _normalize_search_provider(cls, value: Any) -> Any:
        if value is None:
            return "bocha"
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        if not normalized:
            return "bocha"
        if normalized not in {"bing", "bocha", "bochaai"}:
            raise ValueError("SEARCH_PROVIDER must be one of: bing, bocha, bochaai")
        return normalized

    @field_validator("cli_progress_color", mode="before")
    @classmethod
    def _normalize_cli_progress_color(cls, value: Any) -> Any:
        if value is None:
            return "gray"
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower()
        return normalized or "gray"

    @field_validator("bocha_api_key", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("feishu_allowed_open_ids", mode="before")
    @classmethod
    def _parse_feishu_allowed_open_ids(cls, value: Any) -> Any:
        if value is None:
            return ()
        if isinstance(value, str):
            if not value.strip():
                return ()
            stripped = value.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                loaded = json.loads(stripped)
                if not isinstance(loaded, list):
                    raise ValueError("FEISHU_ALLOWED_OPEN_IDS JSON value must be a list")
                return tuple(str(item).strip() for item in loaded if str(item).strip())
            return tuple(item.strip() for item in stripped.split(",") if item.strip())
        if isinstance(value, (list, tuple, set)):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return value

    @model_validator(mode="after")
    def _inherit_log_paths(self) -> AppConfig:
        if "llm_trace_log_path" not in self.model_fields_set:
            object.__setattr__(self, "llm_trace_log_path", self.app_log_path)
        if "feishu_log_path" not in self.model_fields_set:
            object.__setattr__(self, "feishu_log_path", self.app_log_path)
        return self


def load_env_file(env_path: str = ".env") -> None:
    """Minimal .env loader kept for compatibility and tests.

    `.env` values intentionally override existing process env values so local
    project configuration is always deterministic when the file is present.
    """
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
        if key:
            os.environ[key] = value


def load_config(load_dotenv: bool = True) -> AppConfig:
    settings_factory = cast(Any, AppConfig)
    return settings_factory(_env_file=None if not load_dotenv else ".env")


def _filter_removed_settings_source(source: Any) -> Any:
    def _wrapped() -> Any:
        values = source()
        if not isinstance(values, dict):
            return values
        filtered = dict(values)
        for key in _REMOVED_CONFIG_FIELDS:
            filtered.pop(key, None)
        return filtered

    return _wrapped


def load_startup_app_version(
    *,
    pyproject_path: Any,
    logger: logging.Logger | None = None,
) -> str:
    path = Path(pyproject_path)
    try:
        return _read_project_version(path)
    except (OSError, ValueError) as exc:
        if logger is not None:
            logger.warning(
                "failed to load app version from pyproject",
                extra={
                    "event": "app_version_load_failed",
                    "context": {"pyproject_path": str(path), "error": repr(exc)},
                },
            )
        return UNKNOWN_APP_VERSION


def _read_project_version(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise ValueError(f"pyproject not found: {path}")

    current_section = ""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current_section = line[1:-1].strip()
            continue
        if current_section != "project":
            continue
        match = _PROJECT_VERSION_PATTERN.match(line)
        if match is None:
            continue
        version = match.group(1).strip()
        if version:
            return version
        break
    raise ValueError(f"project.version not found in {path}")
