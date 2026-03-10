from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel
from assistant_app.schemas.normalization import (
    EVENT_TIME_FORMAT,
    normalize_datetime_text,
    normalize_optional_datetime_text,
    normalize_repeat_times_value,
    normalize_tag_text,
)
from assistant_app.schemas.values import OptionalThoughtStatusValue, ThoughtContentValue, ThoughtStatusValue

THOUGHT_STATUS_TODO: Literal["未完成"] = "未完成"


class NormalizedTagValue(FrozenModel):
    tag: str = "default"

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: object) -> str:
        return normalize_tag_text(value, default="default") or "default"


class ScheduleDurationValue(FrozenModel):
    duration_minutes: int = Field(ge=1)


class ScheduleCreateInput(FrozenModel):
    title: str = Field(min_length=1)
    event_time: str
    duration_minutes: int = Field(default=60, ge=1)
    remind_at: str | None = None
    tag: str = "default"

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: object) -> str:
        return normalize_tag_text(value, default="default") or "default"

    @field_validator("event_time", mode="before")
    @classmethod
    def normalize_event_time(cls, value: object) -> str:
        return normalize_datetime_text(value, field_name="event_time", formats=(EVENT_TIME_FORMAT,))

    @field_validator("remind_at", mode="before")
    @classmethod
    def normalize_remind_at(cls, value: object) -> str | None:
        return normalize_optional_datetime_text(value, field_name="remind_at", formats=(EVENT_TIME_FORMAT,))


class ScheduleBatchCreateInput(FrozenModel):
    title: str = Field(min_length=1)
    event_times: list[str] = Field(default_factory=list)
    duration_minutes: int = Field(default=60, ge=1)
    remind_at: str | None = None
    tag: str = "default"

    @field_validator("tag", mode="before")
    @classmethod
    def normalize_tag(cls, value: object) -> str:
        return normalize_tag_text(value, default="default") or "default"

    @field_validator("event_times", mode="before")
    @classmethod
    def normalize_event_times(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        return [
            normalize_datetime_text(item, field_name="event_times", formats=(EVENT_TIME_FORMAT,))
            for item in value
        ]

    @field_validator("remind_at", mode="before")
    @classmethod
    def normalize_remind_at(cls, value: object) -> str | None:
        return normalize_optional_datetime_text(value, field_name="remind_at", formats=(EVENT_TIME_FORMAT,))


class ScheduleRecurrenceInput(FrozenModel):
    schedule_id: int = Field(ge=1)
    start_time: str
    repeat_interval_minutes: int = Field(ge=1)
    repeat_times: int
    remind_start_time: str | None = None
    enabled: bool = True

    @field_validator("start_time", mode="before")
    @classmethod
    def normalize_start_time(cls, value: object) -> str:
        return normalize_datetime_text(value, field_name="start_time", formats=(EVENT_TIME_FORMAT,))

    @field_validator("remind_start_time", mode="before")
    @classmethod
    def normalize_remind_start_time(cls, value: object) -> str | None:
        return normalize_optional_datetime_text(
            value,
            field_name="remind_start_time",
            formats=(EVENT_TIME_FORMAT,),
        )

    @field_validator("repeat_times", mode="before")
    @classmethod
    def normalize_repeat_times(cls, value: object) -> int:
        return normalize_repeat_times_value(value, field_name="repeat_times")


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
        return normalize_tag_text(value, default="default") or "default"

    @field_validator("event_time", mode="before")
    @classmethod
    def normalize_event_time(cls, value: object) -> str:
        return normalize_datetime_text(value, field_name="event_time", formats=(EVENT_TIME_FORMAT,))

    @field_validator("remind_at", "repeat_remind_start_time", mode="before")
    @classmethod
    def normalize_clearable_datetime(cls, value: object, info: object) -> str | None:
        field_name = getattr(info, "field_name", "datetime")
        return normalize_optional_datetime_text(value, field_name=field_name, formats=(EVENT_TIME_FORMAT,))

    @model_validator(mode="after")
    def validate_optional_updates(self) -> ScheduleUpdateInput:
        if "duration_minutes" in self.model_fields_set and self.duration_minutes is None:
            raise ValueError("duration_minutes must be >= 1")
        return self


class ThoughtCreateInput(FrozenModel):
    content: str = Field(min_length=1)
    status: Literal["未完成", "完成", "删除"] = THOUGHT_STATUS_TODO

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: object) -> str:
        return ThoughtContentValue.model_validate({"content": value}).content

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> str:
        return ThoughtStatusValue.model_validate({"status": value}).status


class ThoughtUpdateInput(FrozenModel):
    content: str = Field(min_length=1)
    status: Literal["未完成", "完成", "删除"] | None = None

    @field_validator("content", mode="before")
    @classmethod
    def normalize_content(cls, value: object) -> str:
        return ThoughtContentValue.model_validate({"content": value}).content

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: object) -> str | None:
        return OptionalThoughtStatusValue.model_validate({"status": value}).status

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
