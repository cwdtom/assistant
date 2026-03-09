from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.domain import EVENT_TIME_FORMAT, _validate_datetime_text

THOUGHT_STATUS_TODO = "未完成"


def _normalize_tag_value(value: object) -> str:
    if value is None:
        return "default"
    if not isinstance(value, str):
        raise TypeError("tag must be a string")
    normalized = value.strip().lower()
    if not normalized:
        return "default"
    return normalized


def _validate_schedule_datetime_text(value: str, *, field_name: str) -> str:
    return _validate_datetime_text(value, field_name=field_name, formats=(EVENT_TIME_FORMAT,))


def _normalize_clearable_datetime_value(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("datetime field must be a string")
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


class NormalizedTagValue(FrozenModel):
    tag: str = "default"

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: object) -> str:
        return _normalize_tag_value(value)


class ScheduleDurationValue(FrozenModel):
    duration_minutes: int = Field(ge=1)


class ThoughtContentValue(FrozenModel):
    content: str = Field(min_length=1)


class ThoughtStatusValue(FrozenModel):
    status: Literal["未完成", "完成", "删除"]


class ScheduleCreateInput(FrozenModel):
    title: str = Field(min_length=1)
    event_time: str
    duration_minutes: int = Field(default=60, ge=1)
    remind_at: str | None = None
    tag: str = "default"

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: object) -> str:
        return _normalize_tag_value(value)

    @field_validator("event_time")
    @classmethod
    def validate_event_time(cls, value: str) -> str:
        return _validate_schedule_datetime_text(value, field_name="event_time")

    @field_validator("remind_at")
    @classmethod
    def validate_remind_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_schedule_datetime_text(value, field_name="remind_at")


class ScheduleBatchCreateInput(FrozenModel):
    title: str = Field(min_length=1)
    event_times: list[str] = Field(default_factory=list)
    duration_minutes: int = Field(default=60, ge=1)
    remind_at: str | None = None
    tag: str = "default"

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: object) -> str:
        return _normalize_tag_value(value)

    @field_validator("event_times")
    @classmethod
    def validate_event_times(cls, value: list[str]) -> list[str]:
        return [_validate_schedule_datetime_text(item, field_name="event_times") for item in value]

    @field_validator("remind_at")
    @classmethod
    def validate_remind_at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_schedule_datetime_text(value, field_name="remind_at")


class ScheduleRecurrenceInput(FrozenModel):
    schedule_id: int = Field(ge=1)
    start_time: str
    repeat_interval_minutes: int = Field(ge=1)
    repeat_times: int
    remind_start_time: str | None = None
    enabled: bool = True

    @field_validator("start_time")
    @classmethod
    def validate_start_time(cls, value: str) -> str:
        return _validate_schedule_datetime_text(value, field_name="start_time")

    @field_validator("remind_start_time")
    @classmethod
    def validate_remind_start_time(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_schedule_datetime_text(value, field_name="remind_start_time")

    @field_validator("repeat_times")
    @classmethod
    def validate_repeat_times(cls, value: int) -> int:
        if value == -1 or value >= 2:
            return value
        raise ValueError("repeat_times must be -1 or >= 2")


class ScheduleUpdateInput(FrozenModel):
    schedule_id: int = Field(ge=1)
    title: str = Field(min_length=1)
    event_time: str
    tag: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    remind_at: str | None = None
    repeat_remind_start_time: str | None = None

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: object) -> str:
        return _normalize_tag_value(value)

    @field_validator("event_time")
    @classmethod
    def validate_event_time(cls, value: str) -> str:
        return _validate_schedule_datetime_text(value, field_name="event_time")

    @field_validator("remind_at", "repeat_remind_start_time", mode="before")
    @classmethod
    def normalize_clearable_datetime(cls, value: object) -> str | None:
        return _normalize_clearable_datetime_value(value)

    @field_validator("remind_at", "repeat_remind_start_time")
    @classmethod
    def validate_optional_datetime(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "datetime")
        return _validate_schedule_datetime_text(value, field_name=field_name)

    @model_validator(mode="after")
    def validate_optional_updates(self) -> ScheduleUpdateInput:
        if "duration_minutes" in self.model_fields_set and self.duration_minutes is None:
            raise ValueError("duration_minutes must be >= 1")
        return self


class ThoughtCreateInput(FrozenModel):
    content: str = Field(min_length=1)
    status: Literal["未完成", "完成", "删除"] = THOUGHT_STATUS_TODO


class ThoughtUpdateInput(FrozenModel):
    content: str = Field(min_length=1)
    status: Literal["未完成", "完成", "删除"] | None = None

    @model_validator(mode="after")
    def validate_optional_status(self) -> ThoughtUpdateInput:
        if "status" in self.model_fields_set and self.status is None:
            raise ValueError("status must be one of 未完成, 完成, 删除")
        return self


__all__ = [
    "NormalizedTagValue",
    "ScheduleBatchCreateInput",
    "ScheduleCreateInput",
    "ScheduleDurationValue",
    "ScheduleRecurrenceInput",
    "ScheduleUpdateInput",
    "ThoughtContentValue",
    "ThoughtCreateInput",
    "ThoughtStatusValue",
    "ThoughtUpdateInput",
]
