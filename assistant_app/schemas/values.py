from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, cast

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
THOUGHT_STATUS_VALUES = ("未完成", "完成", "删除")


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


class NormalizedTagValue(DefaultTagValue):
    pass


class ScheduleDurationValue(FrozenModel):
    duration_minutes: int = Field(ge=1)

    @field_validator("duration_minutes", mode="before")
    @classmethod
    def normalize_duration_minutes(cls, value: Any) -> int:
        return PositiveIntValue.model_validate({"value": value}).value


class ThoughtContentValue(FrozenModel):
    content: str = Field(min_length=1)

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: Any) -> str:
        return normalize_required_text(value, field_name="content")


class ThoughtStatusValue(FrozenModel):
    status: Literal["未完成", "完成", "删除"]

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> Literal["未完成", "完成", "删除"]:
        normalized = normalize_required_text(value, field_name="status")
        if normalized not in THOUGHT_STATUS_VALUES:
            raise ValueError("status must be one of 未完成, 完成, 删除")
        return cast(Literal["未完成", "完成", "删除"], normalized)


class OptionalThoughtStatusValue(FrozenModel):
    status: Literal["未完成", "完成", "删除"] | None = None

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> Literal["未完成", "完成", "删除"] | None:
        normalized = normalize_optional_text(value)
        if normalized is None:
            return None
        if normalized not in THOUGHT_STATUS_VALUES:
            raise ValueError("status must be one of 未完成, 完成, 删除")
        return cast(Literal["未完成", "完成", "删除"], normalized)


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
    "NormalizedTagValue",
    "OptionalScheduleDateTimeValue",
    "OptionalTagValue",
    "OptionalThoughtStatusValue",
    "PositiveIntValue",
    "ScheduleDateTimeValue",
    "ScheduleDurationValue",
    "ScheduleRepeatTimesValue",
    "ScheduleViewAnchorValue",
    "THOUGHT_STATUS_VALUES",
    "ThoughtContentValue",
    "ThoughtStatusValue",
]
