from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import EVENT_TIME_FORMAT, _validate_datetime_text

DEFAULT_HISTORY_LIST_LIMIT = 20
MAX_HISTORY_LIST_LIMIT = 200


def _normalize_text(value: Any, *, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    if not isinstance(value, str):
        value = str(value)
    normalized = re.sub(r"\s+", " ", value).strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized or None


def _normalize_tag_text(value: Any, *, default: str | None) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized is None:
        return default
    collapsed = normalized.lower().lstrip("#")
    collapsed = re.sub(r"\s+", "-", collapsed)
    return collapsed or default


def _normalize_datetime_text(value: Any, *, field_name: str) -> str:
    normalized = _normalize_text(value, field_name=field_name)
    parsed = datetime.strptime(normalized, EVENT_TIME_FORMAT)
    return parsed.strftime(EVENT_TIME_FORMAT)


class DefaultTagValue(FrozenModel):
    tag: str = "default"

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str:
        return _normalize_tag_text(value, default="default") or "default"


class OptionalTagValue(FrozenModel):
    tag: str | None = None

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str | None:
        return _normalize_tag_text(value, default=None)


class ScheduleDateTimeValue(FrozenModel):
    value: str

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> str:
        return _normalize_datetime_text(value, field_name="value")


class OptionalScheduleDateTimeValue(FrozenModel):
    value: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> str | None:
        if value is None:
            return None
        normalized = _normalize_optional_text(value)
        if normalized is None:
            return None
        return _normalize_datetime_text(normalized, field_name="value")


class PositiveIntValue(FrozenModel):
    value: int = Field(ge=1)

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("value must be a positive integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError("value must be a positive integer")
            return int(value)
        text = _normalize_optional_text(value)
        if text is None or not text.isdigit():
            raise ValueError("value must be a positive integer")
        return int(text)


class HistoryListLimitValue(FrozenModel):
    limit: int = Field(default=DEFAULT_HISTORY_LIST_LIMIT, ge=1, le=MAX_HISTORY_LIST_LIMIT)

    @field_validator("limit", mode="before")
    @classmethod
    def normalize_limit(cls, value: Any) -> int:
        return PositiveIntValue.model_validate({"value": value}).value

    @field_validator("limit")
    @classmethod
    def cap_limit(cls, value: int) -> int:
        return min(value, MAX_HISTORY_LIST_LIMIT)


class ScheduleRepeatTimesValue(FrozenModel):
    value: int

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> int:
        if isinstance(value, bool):
            raise ValueError("value must be -1 or >= 2")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError("value must be -1 or >= 2")
            return int(value)
        text = _normalize_optional_text(value)
        if text is None or not re.fullmatch(r"-?\d+", text):
            raise ValueError("value must be -1 or >= 2")
        return int(text)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: int) -> int:
        if value == -1 or value >= 2:
            return value
        raise ValueError("value must be -1 or >= 2")


class ScheduleViewAnchorValue(FrozenModel):
    view: Literal["day", "week", "month"]
    anchor: str | None = None

    @field_validator("view", mode="before")
    @classmethod
    def normalize_view(cls, value: Any) -> str:
        return _normalize_text(value, field_name="view").lower()

    @field_validator("anchor", mode="before")
    @classmethod
    def normalize_anchor(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_anchor(self) -> ScheduleViewAnchorValue:
        if self.anchor is None:
            return self
        if self.view in {"day", "week"}:
            parsed = datetime.strptime(self.anchor, "%Y-%m-%d")
            object.__setattr__(self, "anchor", parsed.strftime("%Y-%m-%d"))
            return self
        parsed = datetime.strptime(self.anchor, "%Y-%m")
        object.__setattr__(self, "anchor", parsed.strftime("%Y-%m"))
        return self


__all__ = [
    "DEFAULT_HISTORY_LIST_LIMIT",
    "DefaultTagValue",
    "HistoryListLimitValue",
    "MAX_HISTORY_LIST_LIMIT",
    "OptionalScheduleDateTimeValue",
    "OptionalTagValue",
    "PositiveIntValue",
    "ScheduleDateTimeValue",
    "ScheduleRepeatTimesValue",
    "ScheduleViewAnchorValue",
]
