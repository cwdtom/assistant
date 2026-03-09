from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.normalization import (
    EVENT_TIME_FORMAT,
    normalize_datetime_text,
    normalize_optional_datetime_text,
    normalize_optional_text,
    normalize_repeat_times_value,
    normalize_required_text,
    normalize_tag_text,
)

DEFAULT_HISTORY_LIST_LIMIT = 20
MAX_HISTORY_LIST_LIMIT = 200


class DefaultTagValue(FrozenModel):
    tag: str = "default"

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str:
        return normalize_tag_text(value, default="default") or "default"


class OptionalTagValue(FrozenModel):
    tag: str | None = None

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: Any) -> str | None:
        return normalize_tag_text(value, default=None)


class ScheduleDateTimeValue(FrozenModel):
    value: str

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> str:
        return normalize_datetime_text(value, field_name="value", formats=(EVENT_TIME_FORMAT,))


class OptionalScheduleDateTimeValue(FrozenModel):
    value: str | None = None

    @field_validator("value", mode="before")
    @classmethod
    def normalize_value(cls, value: Any) -> str | None:
        return normalize_optional_datetime_text(value, field_name="value", formats=(EVENT_TIME_FORMAT,))


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
        text = normalize_optional_text(value)
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
        return normalize_repeat_times_value(value, field_name="value")


class ScheduleViewAnchorValue(FrozenModel):
    view: Literal["day", "week", "month"]
    anchor: str | None = None

    @field_validator("view", mode="before")
    @classmethod
    def normalize_view(cls, value: Any) -> str:
        return normalize_required_text(value, field_name="view").lower()

    @field_validator("anchor", mode="before")
    @classmethod
    def normalize_anchor(cls, value: Any) -> str | None:
        return normalize_optional_text(value)

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
