from __future__ import annotations

import json
from typing import Any

from pydantic import ConfigDict, Field, ValidationError, field_validator

from assistant_app.schemas.base import FrozenModel, StrictModel


class FeishuCompatModel(FrozenModel):
    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        from_attributes=True,
        str_strip_whitespace=True,
        strict=True,
    )


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


class FeishuTextContentPayload(FeishuCompatModel):
    text: str


class FeishuSenderIdPayload(FeishuCompatModel):
    open_id: str | None = None

    @field_validator("open_id", mode="before")
    @classmethod
    def normalize_open_id(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value


class FeishuSenderPayload(FeishuCompatModel):
    sender_type: str | None = None
    sender_id: FeishuSenderIdPayload | None = None


class FeishuInboundMessagePayload(FeishuCompatModel):
    message_type: str | None = None
    chat_type: str | None = None
    message_id: str | None = None
    chat_id: str | None = None
    content: str | None = None

    @field_validator("message_type", "chat_type", "message_id", "chat_id", "content", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("field must be a string")
        normalized = value.strip()
        return normalized or None


class FeishuInboundEventBody(FeishuCompatModel):
    sender: FeishuSenderPayload | None = None
    message: FeishuInboundMessagePayload | None = None


class FeishuInboundEventEnvelope(FeishuCompatModel):
    event: FeishuInboundEventBody | None = None
    sender: FeishuSenderPayload | None = None
    message: FeishuInboundMessagePayload | None = None

    def message_payload(self) -> FeishuInboundMessagePayload | None:
        if self.event is not None and self.event.message is not None:
            return self.event.message
        return self.message

    def sender_payload(self) -> FeishuSenderPayload | None:
        if self.event is not None and self.event.sender is not None:
            return self.event.sender
        return self.sender


class FeishuCalendarTimeInfoPayload(FeishuCompatModel):
    time_stamp: int | None = None
    timestamp: int | None = None
    timezone: str | None = None

    @field_validator("time_stamp", "timestamp", mode="before")
    @classmethod
    def normalize_optional_timestamp(cls, value: Any) -> int | None:
        return _parse_optional_int(value)

    @field_validator("timezone", mode="before")
    @classmethod
    def normalize_optional_timezone(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("timezone must be a string")
        normalized = value.strip()
        return normalized or None

    def resolved_timestamp(self) -> int | None:
        if self.time_stamp is not None:
            return self.time_stamp
        return self.timestamp


class FeishuCalendarEventPayload(FeishuCompatModel):
    event_id: str | None = None
    summary: str = ""
    description: str = ""
    start_time: FeishuCalendarTimeInfoPayload | None = None
    end_time: FeishuCalendarTimeInfoPayload | None = None
    create_time: int | None = None

    @field_validator("event_id", mode="before")
    @classmethod
    def normalize_event_id(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("event_id must be a string")
        normalized = value.strip()
        return normalized or None

    @field_validator("summary", "description", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    @field_validator("create_time", mode="before")
    @classmethod
    def normalize_create_time(cls, value: Any) -> int | None:
        return _parse_optional_unix_seconds(value)


class FeishuApiResponseStatus(FeishuCompatModel):
    code: int | None = None
    msg: str | None = None

    @field_validator("code", mode="before")
    @classmethod
    def normalize_code(cls, value: Any) -> int | None:
        return _parse_optional_int(value)

    @field_validator("msg", mode="before")
    @classmethod
    def normalize_msg(cls, value: Any) -> str | None:
        return _normalize_optional_text(value, allow_non_string=True)

    def is_success(self) -> bool:
        return self.code in (None, 0)


class FeishuCalendarEventIdPayload(FeishuCompatModel):
    event_id: str | None = None

    @field_validator("event_id", mode="before")
    @classmethod
    def normalize_event_id(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)


class FeishuCalendarCreateResponseData(FeishuCompatModel):
    event: FeishuCalendarEventIdPayload | None = None


class FeishuCalendarCreateResponse(FeishuApiResponseStatus):
    data: FeishuCalendarCreateResponseData | None = None

    def event_id_value(self) -> str | None:
        if self.data is None or self.data.event is None:
            return None
        return self.data.event.event_id


class FeishuCalendarListResponseData(FeishuCompatModel):
    items: list[object] = Field(default_factory=list)
    has_more: bool = False
    page_token: str | None = None

    @field_validator("items", mode="before")
    @classmethod
    def normalize_items(cls, value: Any) -> list[object]:
        if isinstance(value, list):
            return value
        return []

    @field_validator("has_more", mode="before")
    @classmethod
    def normalize_has_more(cls, value: Any) -> bool:
        return _parse_optional_bool(value)

    @field_validator("page_token", mode="before")
    @classmethod
    def normalize_page_token(cls, value: Any) -> str | None:
        return _normalize_optional_text(value)


class FeishuCalendarListResponse(FeishuApiResponseStatus):
    data: FeishuCalendarListResponseData | None = None

    def raw_items(self) -> list[object]:
        if self.data is None:
            return []
        return list(self.data.items)

    def has_more_items(self) -> bool:
        if self.data is None:
            return False
        return self.data.has_more

    def page_token_value(self) -> str | None:
        if self.data is None:
            return None
        return self.data.page_token


def parse_feishu_message_text(raw_content: str) -> str:
    content = raw_content.strip()
    if not content:
        return ""
    try:
        payload = FeishuTextContentPayload.model_validate_json(content)
        return payload.text
    except (ValidationError, json.JSONDecodeError, ValueError):
        return content


def inspect_feishu_text_message_payload(
    raw_payload: Any,
) -> tuple[FeishuTextMessage | None, str | None, str | None, str | None]:
    try:
        envelope = FeishuInboundEventEnvelope.model_validate(raw_payload)
    except ValidationError:
        return None, "invalid_event_envelope", None, None

    message = envelope.message_payload()
    sender = envelope.sender_payload()
    message_type = message.message_type if message is not None else None
    chat_type = message.chat_type if message is not None else None
    if message is None:
        return None, "missing_message_payload", message_type, chat_type
    if message.message_type not in {"text", "post"}:
        return None, "unsupported_message_type", message_type, chat_type
    if message.chat_type and message.chat_type != "p2p":
        return None, "unsupported_chat_type", message_type, chat_type
    if sender is not None and sender.sender_type and sender.sender_type != "user":
        return None, "unsupported_sender_type", message_type, chat_type
    if message.message_id is None or message.chat_id is None or message.content is None:
        return None, "missing_required_fields", message_type, chat_type

    text = message.content.strip()
    if message.message_type == "text":
        text = parse_feishu_message_text(message.content).strip()
    if not text:
        return None, "blank_text", message_type, chat_type

    try:
        parsed = FeishuTextMessage.model_validate(
            {
                "message_id": message.message_id,
                "chat_id": message.chat_id,
                "open_id": sender.sender_id.open_id if sender and sender.sender_id else None,
                "text": text,
            }
        )
    except ValidationError:
        return None, "message_schema_invalid", message_type, chat_type
    return parsed, None, message_type, chat_type


def parse_feishu_text_message(raw_payload: Any) -> FeishuTextMessage | None:
    parsed, _, _, _ = inspect_feishu_text_message_payload(raw_payload)
    return parsed


def inspect_feishu_calendar_event_payload(
    raw_payload: Any,
    *,
    default_timezone: str,
) -> tuple[FeishuCalendarEvent | None, str | None, bool, bool, bool]:
    has_event_id = _has_non_empty_string(_read_path(raw_payload, "event_id"))
    has_start = _read_path(raw_payload, "start_time") is not None
    has_end = _read_path(raw_payload, "end_time") is not None
    try:
        payload = FeishuCalendarEventPayload.model_validate(raw_payload)
    except ValidationError:
        return None, "invalid_calendar_event_payload", has_event_id, has_start, has_end

    has_event_id = payload.event_id is not None
    has_start = payload.start_time is not None
    has_end = payload.end_time is not None
    if payload.event_id is None:
        return None, "missing_event_id", has_event_id, has_start, has_end
    start_timestamp = payload.start_time.resolved_timestamp() if payload.start_time is not None else None
    end_timestamp = payload.end_time.resolved_timestamp() if payload.end_time is not None else None
    if start_timestamp is None or end_timestamp is None:
        return None, "missing_timestamp_range", has_event_id, has_start, has_end
    timezone = (
        payload.start_time.timezone if payload.start_time and payload.start_time.timezone
        else payload.end_time.timezone if payload.end_time and payload.end_time.timezone
        else default_timezone.strip() or "Asia/Shanghai"
    )
    try:
        parsed = FeishuCalendarEvent.model_validate(
            {
                "event_id": payload.event_id,
                "summary": payload.summary,
                "description": payload.description,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
                "timezone": timezone,
                "create_timestamp": payload.create_time,
            }
        )
    except ValidationError:
        return None, "calendar_event_schema_invalid", has_event_id, has_start, has_end
    return parsed, None, has_event_id, has_start, has_end


def parse_feishu_calendar_event(raw_payload: Any, *, default_timezone: str) -> FeishuCalendarEvent | None:
    parsed, _, _, _, _ = inspect_feishu_calendar_event_payload(
        raw_payload,
        default_timezone=default_timezone,
    )
    return parsed


def parse_feishu_response_status(raw_payload: Any) -> FeishuApiResponseStatus | None:
    try:
        return FeishuApiResponseStatus.model_validate(raw_payload)
    except ValidationError:
        return None


def parse_feishu_calendar_create_response(raw_payload: Any) -> FeishuCalendarCreateResponse | None:
    try:
        return FeishuCalendarCreateResponse.model_validate(raw_payload)
    except ValidationError:
        return None


def parse_feishu_calendar_list_response(raw_payload: Any) -> FeishuCalendarListResponse | None:
    try:
        return FeishuCalendarListResponse.model_validate(raw_payload)
    except ValidationError:
        return None


def _parse_optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _parse_optional_unix_seconds(value: Any) -> int | None:
    parsed = _parse_optional_int(value)
    if parsed is None:
        return None
    if abs(parsed) >= 10**12:
        return parsed // 1000
    return parsed


def _parse_optional_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return False
        if normalized in {"false", "0", "no", "off"}:
            return False
        if normalized in {"true", "1", "yes", "on"}:
            return True
    return bool(value)


def _normalize_optional_text(value: Any, *, allow_non_string: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if allow_non_string:
        return str(value)
    return None


def _read_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
            continue
        current = getattr(current, part, None)
    return current


def _has_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


__all__ = [
    "FeishuApiResponseStatus",
    "FeishuCalendarEvent",
    "FeishuCalendarCreateResponse",
    "FeishuCalendarListResponse",
    "FeishuPendingTaskInput",
    "FeishuProactiveTextRequest",
    "FeishuSubtaskResultUpdate",
    "FeishuTextMessage",
    "parse_feishu_calendar_create_response",
    "inspect_feishu_calendar_event_payload",
    "inspect_feishu_text_message_payload",
    "parse_feishu_calendar_event",
    "parse_feishu_calendar_list_response",
    "parse_feishu_response_status",
    "parse_feishu_message_text",
    "parse_feishu_text_message",
]
