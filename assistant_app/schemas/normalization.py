from __future__ import annotations

import re
from datetime import datetime
from typing import Any

EVENT_TIME_FORMAT = "%Y-%m-%d %H:%M"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def normalize_required_text(value: Any, *, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    if not isinstance(value, str):
        value = str(value)
    normalized = re.sub(r"\s+", " ", value).strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized or None


def normalize_tag_text(value: Any, *, default: str | None) -> str | None:
    normalized = normalize_optional_text(value)
    if normalized is None:
        return default
    collapsed = normalized.lower().lstrip("#")
    collapsed = re.sub(r"\s+", "-", collapsed)
    return collapsed or default


def validate_datetime_text(value: str, *, field_name: str, formats: tuple[str, ...]) -> str:
    for fmt in formats:
        try:
            datetime.strptime(value, fmt)
            return value
        except ValueError:
            continue
    format_text = " or ".join(formats)
    raise ValueError(f"{field_name} must match {format_text}")


def normalize_datetime_text(
    value: Any,
    *,
    field_name: str,
    formats: tuple[str, ...] = (EVENT_TIME_FORMAT,),
    output_format: str | None = None,
) -> str:
    normalized = normalize_required_text(value, field_name=field_name)
    canonical_format = output_format or formats[0]
    for fmt in formats:
        try:
            parsed = datetime.strptime(normalized, fmt)
            return parsed.strftime(canonical_format)
        except ValueError:
            continue
    format_text = " or ".join(formats)
    raise ValueError(f"{field_name} must match {format_text}")


def normalize_optional_datetime_text(
    value: Any,
    *,
    field_name: str,
    formats: tuple[str, ...] = (EVENT_TIME_FORMAT,),
    output_format: str | None = None,
) -> str | None:
    normalized = normalize_optional_text(value)
    if normalized is None:
        return None
    return normalize_datetime_text(
        normalized,
        field_name=field_name,
        formats=formats,
        output_format=output_format,
    )


def normalize_repeat_times_value(value: Any, *, field_name: str) -> int:
    error_message = f"{field_name} must be -1 or >= 2"
    if isinstance(value, bool):
        raise ValueError(error_message)
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(error_message)
        parsed = int(value)
    else:
        text = normalize_optional_text(value)
        if text is None or not re.fullmatch(r"-?\d+", text):
            raise ValueError(error_message)
        parsed = int(text)
    if parsed == -1 or parsed >= 2:
        return parsed
    raise ValueError(error_message)


__all__ = [
    "EVENT_TIME_FORMAT",
    "TIMESTAMP_FORMAT",
    "normalize_datetime_text",
    "normalize_optional_datetime_text",
    "normalize_optional_text",
    "normalize_repeat_times_value",
    "normalize_required_text",
    "normalize_tag_text",
    "validate_datetime_text",
]
