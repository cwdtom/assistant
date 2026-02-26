from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any


@dataclass
class _SharedHandlerEntry:
    handler: logging.Handler
    ref_count: int = 0


_SHARED_HANDLER_LOCK = Lock()
_SHARED_HANDLERS: dict[tuple[str, bool, int], _SharedHandlerEntry] = {}
_SHARED_HANDLER_KEYS_BY_ID: dict[int, tuple[str, bool, int]] = {}


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


def configure_llm_trace_logger(log_path: str, retention_days: int = 7) -> logging.Logger:
    return _configure_json_file_logger(
        name="assistant_app.llm_trace",
        log_path=log_path,
        rotate_daily=True,
        retention_days=retention_days,
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

    handler_key = (abs_path, rotate_daily, normalized_retention_days)
    reused_existing = False
    for handler in list(logger.handlers):
        if isinstance(handler, logging.NullHandler):
            logger.removeHandler(handler)
            continue

        if _lookup_shared_handler_key(handler) == handler_key:
            handler.setFormatter(JsonLinesFormatter())
            reused_existing = True
            continue

        _remove_and_close_handler(logger, handler)

    if reused_existing:
        return logger

    file_handler = _acquire_shared_file_handler(handler_key=handler_key)
    logger.addHandler(file_handler)
    return logger


def _reset_to_null_handler(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        _remove_and_close_handler(logger, handler)
    logger.addHandler(logging.NullHandler())


def _remove_and_close_handler(logger: logging.Logger, handler: logging.Handler) -> None:
    logger.removeHandler(handler)
    if _release_shared_file_handler(handler):
        return
    close = getattr(handler, "close", None)
    if callable(close):
        close()


def _acquire_shared_file_handler(handler_key: tuple[str, bool, int]) -> logging.Handler:
    with _SHARED_HANDLER_LOCK:
        entry = _SHARED_HANDLERS.get(handler_key)
        if entry is not None and _is_handler_closed(entry.handler):
            _SHARED_HANDLERS.pop(handler_key, None)
            _SHARED_HANDLER_KEYS_BY_ID.pop(id(entry.handler), None)
            entry = None
        if entry is None:
            path, rotate_daily, retention_days = handler_key
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            if rotate_daily:
                handler: logging.Handler = TimedRotatingFileHandler(
                    path,
                    when="D",
                    interval=1,
                    backupCount=retention_days,
                    encoding="utf-8",
                )
            else:
                handler = logging.FileHandler(path, encoding="utf-8")
            handler.setFormatter(JsonLinesFormatter())
            entry = _SharedHandlerEntry(handler=handler)
            _SHARED_HANDLERS[handler_key] = entry
            _SHARED_HANDLER_KEYS_BY_ID[id(handler)] = handler_key

        entry.ref_count += 1
        return entry.handler


def _lookup_shared_handler_key(handler: logging.Handler) -> tuple[str, bool, int] | None:
    with _SHARED_HANDLER_LOCK:
        return _SHARED_HANDLER_KEYS_BY_ID.get(id(handler))


def _release_shared_file_handler(handler: logging.Handler) -> bool:
    with _SHARED_HANDLER_LOCK:
        key = _SHARED_HANDLER_KEYS_BY_ID.get(id(handler))
        if key is None:
            return False

        entry = _SHARED_HANDLERS.get(key)
        if entry is None:
            _SHARED_HANDLER_KEYS_BY_ID.pop(id(handler), None)
            return False

        entry.ref_count -= 1
        if entry.ref_count <= 0:
            _SHARED_HANDLERS.pop(key, None)
            _SHARED_HANDLER_KEYS_BY_ID.pop(id(handler), None)
            close = getattr(handler, "close", None)
            if callable(close):
                close()
        return True


def _is_handler_closed(handler: logging.Handler) -> bool:
    return bool(getattr(handler, "_closed", False))


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
