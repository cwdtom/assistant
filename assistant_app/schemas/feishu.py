from __future__ import annotations

from pydantic import Field, field_validator

from assistant_app.schemas.base import FrozenModel, StrictModel


class FeishuTextMessage(FrozenModel):
    message_id: str = Field(min_length=1)
    chat_id: str = Field(min_length=1)
    open_id: str | None = None
    text: str = Field(min_length=1)

    @field_validator("open_id", mode="before")
    @classmethod
    def normalize_open_id(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


class FeishuPendingTaskInput(StrictModel):
    chat_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    latest_message_id: str = Field(min_length=1)


class FeishuSubtaskResultUpdate(FrozenModel):
    chat_id: str = Field(min_length=1)
    message_id: str = Field(min_length=1)
    result: str = Field(min_length=1)


class FeishuProactiveTextRequest(FrozenModel):
    open_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class FeishuCalendarEvent(FrozenModel):
    event_id: str = Field(min_length=1)
    summary: str = ""
    description: str = ""
    start_timestamp: int
    end_timestamp: int
    timezone: str = Field(min_length=1)
    create_timestamp: int | None = None

