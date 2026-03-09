from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from assistant_app.schemas.base import FrozenModel

EVENT_TIME_FORMAT = "%Y-%m-%d %H:%M"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _validate_datetime_text(value: str, *, field_name: str, formats: tuple[str, ...]) -> str:
    for fmt in formats:
        try:
            datetime.strptime(value, fmt)
            return value
        except ValueError:
            continue
    format_text = " or ".join(formats)
    raise ValueError(f"{field_name} must match {format_text}")
    return value


def _validate_http_url_text(value: str, *, field_name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid http/https URL")
    return value


class ScheduleItem(FrozenModel):
    id: int = Field(ge=1)
    title: str = Field(min_length=1)
    tag: str = Field(default="default")
    event_time: str
    duration_minutes: int = Field(ge=1)
    created_at: str
    remind_at: str | None = None
    repeat_interval_minutes: int | None = Field(default=None, ge=1)
    repeat_times: int | None = None
    repeat_enabled: bool | None = None
    repeat_remind_start_time: str | None = None

    @field_validator("tag")
    @classmethod
    def normalize_tag(cls, value: str) -> str:
        normalized = value.lower()
        if not normalized:
            return "default"
        return normalized

    @field_validator("event_time", "created_at")
    @classmethod
    def validate_required_datetime_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "datetime")
        return _validate_datetime_text(value, field_name=field_name, formats=(EVENT_TIME_FORMAT, TIMESTAMP_FORMAT))

    @field_validator("remind_at", "repeat_remind_start_time")
    @classmethod
    def validate_optional_datetime_fields(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "datetime")
        return _validate_datetime_text(value, field_name=field_name, formats=(EVENT_TIME_FORMAT,))

    @field_validator("event_time")
    @classmethod
    def validate_event_time(cls, value: str) -> str:
        return _validate_datetime_text(value, field_name="event_time", formats=(EVENT_TIME_FORMAT,))

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _validate_datetime_text(value, field_name="created_at", formats=(TIMESTAMP_FORMAT,))

    @field_validator("repeat_times")
    @classmethod
    def validate_repeat_times(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value == -1 or value >= 2:
            return value
        raise ValueError("repeat_times must be -1 or >= 2")

    @model_validator(mode="after")
    def validate_recurrence_fields(self) -> ScheduleItem:
        recurrence_required_fields = (
            self.repeat_interval_minutes,
            self.repeat_times,
            self.repeat_enabled,
        )
        has_any_recurrence = any(value is not None for value in recurrence_required_fields)
        if has_any_recurrence and any(value is None for value in recurrence_required_fields):
            raise ValueError("repeat_interval_minutes, repeat_times and repeat_enabled must be set together")
        return self


class RecurringScheduleRule(FrozenModel):
    id: int = Field(ge=1)
    schedule_id: int = Field(ge=1)
    start_time: str
    repeat_interval_minutes: int = Field(ge=1)
    repeat_times: int
    remind_start_time: str | None = None
    enabled: bool
    created_at: str

    @field_validator("start_time", "created_at")
    @classmethod
    def validate_required_datetime_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "datetime")
        return _validate_datetime_text(value, field_name=field_name, formats=(EVENT_TIME_FORMAT, TIMESTAMP_FORMAT))

    @field_validator("remind_start_time")
    @classmethod
    def validate_optional_datetime_fields(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        field_name = getattr(info, "field_name", "datetime")
        return _validate_datetime_text(value, field_name=field_name, formats=(EVENT_TIME_FORMAT,))

    @field_validator("start_time")
    @classmethod
    def validate_start_time(cls, value: str) -> str:
        return _validate_datetime_text(value, field_name="start_time", formats=(EVENT_TIME_FORMAT,))

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _validate_datetime_text(value, field_name="created_at", formats=(TIMESTAMP_FORMAT,))

    @field_validator("repeat_times")
    @classmethod
    def validate_repeat_times(cls, value: int) -> int:
        if value == -1 or value >= 2:
            return value
        raise ValueError("repeat_times must be -1 or >= 2")


class ChatMessage(FrozenModel):
    role: str = Field(min_length=1)
    content: str


class ChatTurn(FrozenModel):
    user_content: str
    assistant_content: str
    created_at: str

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        return _validate_datetime_text(value, field_name="created_at", formats=(TIMESTAMP_FORMAT,))


class ThoughtItem(FrozenModel):
    id: int = Field(ge=1)
    content: str = Field(min_length=1)
    status: Literal["未完成", "完成", "删除"]
    created_at: str
    updated_at: str

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_datetime_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "datetime")
        return _validate_datetime_text(value, field_name=field_name, formats=(TIMESTAMP_FORMAT,))


class ReminderDelivery(FrozenModel):
    reminder_key: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_id: int = Field(ge=1)
    occurrence_time: str | None = None
    remind_time: str
    delivered_at: str
    payload: str | None = None

    @field_validator("remind_time", "delivered_at")
    @classmethod
    def validate_required_datetime_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "datetime")
        formats = (TIMESTAMP_FORMAT,) if field_name == "delivered_at" else (EVENT_TIME_FORMAT,)
        return _validate_datetime_text(value, field_name=field_name, formats=formats)

    @field_validator("occurrence_time")
    @classmethod
    def validate_optional_datetime_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_datetime_text(value, field_name="occurrence_time", formats=(EVENT_TIME_FORMAT,))


class HttpUrlValue(FrozenModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_http_url_text(value, field_name="url")


class SearchResult(HttpUrlValue):
    title: str = Field(min_length=1)
    snippet: str = ""


class WebPageFetchResult(HttpUrlValue):
    main_text: str
