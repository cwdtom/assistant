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


def load_config() -> AppConfig:
    load_env_file()
    return AppConfig(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL", "https://www.packyapi.com/v1"),
        model=os.getenv("OPENAI_MODEL", "codex5.3"),
        db_path=os.getenv("ASSISTANT_DB_PATH", "assistant.db"),
    )
