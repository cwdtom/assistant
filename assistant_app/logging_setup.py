from __future__ import annotations

import json
import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any


class JsonLinesFormatter(logging.Formatter):
    """Emit one JSON object per line for stable machine parsing."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
        }

        message = record.getMessage()
        parsed_message = _try_parse_json_object(message)
        if parsed_message is not None:
            payload.update(parsed_message)
        elif message:
            payload["message"] = message

        event = getattr(record, "event", None)
        if isinstance(event, str):
            normalized_event = event.strip()
            if normalized_event:
                payload["event"] = normalized_event

        context = getattr(record, "context", None)
        if context is not None:
            payload["context"] = context

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def configure_llm_trace_logger(log_path: str) -> logging.Logger:
    return _configure_json_file_logger(
        name="assistant_app.llm_trace",
        log_path=log_path,
        rotate_daily=False,
        retention_days=1,
    )


def configure_feishu_logger(log_path: str, retention_days: int) -> logging.Logger:
    return _configure_json_file_logger(
        name="assistant_app.feishu",
        log_path=log_path,
        rotate_daily=True,
        retention_days=retention_days,
    )


def configure_app_logger(log_path: str, retention_days: int) -> logging.Logger:
    return _configure_json_file_logger(
        name="assistant_app.app",
        log_path=log_path,
        rotate_daily=True,
        retention_days=retention_days,
    )


def _configure_json_file_logger(
    *,
    name: str,
    log_path: str,
    rotate_daily: bool,
    retention_days: int,
) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    path = log_path.strip()
    if not path:
        _reset_to_null_handler(logger)
        return logger

    abs_path = str(Path(path).expanduser().resolve())
    normalized_retention_days = max(retention_days, 1)

    reused_existing = False
    for handler in list(logger.handlers):
        if isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)
            continue

        existing_path = getattr(handler, "baseFilename", None)
        if not existing_path:
            _remove_and_close_handler(logger, handler)
            continue

        path_matches = str(Path(existing_path).resolve()) == abs_path
        if rotate_daily:
            rotation_matches = isinstance(handler, TimedRotatingFileHandler) and (
                getattr(handler, "backupCount", None) == normalized_retention_days
            )
            if path_matches and rotation_matches:
                handler.setFormatter(JsonLinesFormatter())
                reused_existing = True
                continue
        else:
            if path_matches and type(handler) is logging.FileHandler:
                handler.setFormatter(JsonLinesFormatter())
                reused_existing = True
                continue

        _remove_and_close_handler(logger, handler)

    if reused_existing:
        return logger

    Path(abs_path).parent.mkdir(parents=True, exist_ok=True)
    if rotate_daily:
        file_handler: logging.Handler = TimedRotatingFileHandler(
            abs_path,
            when="D",
            interval=1,
            backupCount=normalized_retention_days,
            encoding="utf-8",
        )
    else:
        file_handler = logging.FileHandler(abs_path, encoding="utf-8")
    file_handler.setFormatter(JsonLinesFormatter())
    logger.addHandler(file_handler)
    return logger


def _reset_to_null_handler(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        _remove_and_close_handler(logger, handler)
    logger.addHandler(logging.NullHandler())


def _remove_and_close_handler(logger: logging.Logger, handler: logging.Handler) -> None:
    logger.removeHandler(handler)
    close = getattr(handler, "close", None)
    if callable(close):
        close()


def _try_parse_json_object(text: str) -> dict[str, Any] | None:
    normalized = text.strip()
    if not normalized.startswith("{") or not normalized.endswith("}"):
        return None
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None
