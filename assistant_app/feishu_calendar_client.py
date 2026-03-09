from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from assistant_app.schemas.feishu import FeishuCalendarEvent

_DEFAULT_TIMEZONE = "Asia/Shanghai"
_DEFAULT_LIST_PAGE_SIZE = 1000

class FeishuCalendarClientError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class FeishuCalendarClient:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        logger: logging.Logger | None = None,
        sdk_module: Any | None = None,
        api_client: Any | None = None,
        calendar_module: Any | None = None,
        default_timezone: str = _DEFAULT_TIMEZONE,
    ) -> None:
        self._app_id = app_id.strip()
        self._app_secret = app_secret.strip()
        self._logger = logger
        self._sdk_module = sdk_module
        self._api_client = api_client
        self._calendar_module = calendar_module
        self._default_timezone = default_timezone.strip() or _DEFAULT_TIMEZONE

    def create_event(
        self,
        *,
        calendar_id: str,
        summary: str,
        description: str,
        start_timestamp: int,
        end_timestamp: int,
        timezone: str,
        need_notification: bool = False,
    ) -> str:
        module = self._ensure_calendar_module()
        api_client = self._ensure_api_client()
        event = (
            module.CalendarEvent.builder()
            .summary(summary)
            .description(description)
            .need_notification(need_notification)
            .start_time(
                module.TimeInfo.builder().timestamp(str(start_timestamp)).timezone(timezone or self._default_timezone).build()
            )
            .end_time(
                module.TimeInfo.builder().timestamp(str(end_timestamp)).timezone(timezone or self._default_timezone).build()
            )
            .build()
        )
        request = module.CreateCalendarEventRequest.builder().calendar_id(calendar_id).request_body(event).build()
        response = api_client.calendar.v4.calendar_event.create(request)
        self._ensure_success(response=response, operation="create")
        event_id = _first_non_empty(_read_path(response, "data.event.event_id"))
        if event_id is None:
            raise FeishuCalendarClientError("create event succeeded but missing event_id")
        return event_id

    def delete_event(
        self,
        *,
        calendar_id: str,
        event_id: str,
        need_notification: bool = False,
        ignore_not_found: bool = True,
    ) -> bool:
        module = self._ensure_calendar_module()
        api_client = self._ensure_api_client()
        request = (
            module.DeleteCalendarEventRequest.builder()
            .calendar_id(calendar_id)
            .event_id(event_id)
            .need_notification("true" if need_notification else "false")
            .build()
        )
        response = api_client.calendar.v4.calendar_event.delete(request)
        if self._is_success(response):
            return True
        code = _parse_int(_read_path(response, "code"))
        if ignore_not_found and code in {193000, 193001, 193003}:
            return False
        self._ensure_success(response=response, operation="delete")
        return True

    def list_events(
        self,
        *,
        calendar_id: str,
        start_timestamp: int,
        end_timestamp: int,
        page_size: int = _DEFAULT_LIST_PAGE_SIZE,
    ) -> list[FeishuCalendarEvent]:
        module = self._ensure_calendar_module()
        api_client = self._ensure_api_client()
        normalized_page_size = max(min(page_size, 1000), 50)

        events: list[FeishuCalendarEvent] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        for _ in range(100):
            builder = (
                module.ListCalendarEventRequest.builder()
                .calendar_id(calendar_id)
                .start_time(str(start_timestamp))
                .end_time(str(end_timestamp))
                .page_size(normalized_page_size)
            )
            if page_token:
                builder = builder.page_token(page_token)
            request = builder.build()
            response = api_client.calendar.v4.calendar_event.list(request)
            self._ensure_success(response=response, operation="list")
            payload_items = _read_path(response, "data.items")
            if isinstance(payload_items, list):
                for raw_item in payload_items:
                    parsed = self._parse_event(raw_item)
                    if parsed is not None:
                        events.append(parsed)
            has_more = bool(_read_path(response, "data.has_more"))
            if not has_more:
                break
            next_token = _first_non_empty(_read_path(response, "data.page_token"))
            if not next_token:
                self._warn("list events has_more but missing page_token")
                break
            if next_token in seen_tokens:
                self._warn("list events encountered duplicate page_token")
                break
            seen_tokens.add(next_token)
            page_token = next_token
        return events

    def _parse_event(self, payload: Any) -> FeishuCalendarEvent | None:
        event_id = _first_non_empty(_read_path(payload, "event_id"))
        if event_id is None:
            self._warn("skip event without event_id")
            return None
        start_timestamp = _parse_int(_read_path(payload, "start_time.time_stamp"))
        if start_timestamp is None:
            start_timestamp = _parse_int(_read_path(payload, "start_time.timestamp"))
        end_timestamp = _parse_int(_read_path(payload, "end_time.time_stamp"))
        if end_timestamp is None:
            end_timestamp = _parse_int(_read_path(payload, "end_time.timestamp"))
        if start_timestamp is None or end_timestamp is None:
            self._warn("skip event without timestamp range")
            return None
        timezone = (
            _first_non_empty(_read_path(payload, "start_time.timezone"), _read_path(payload, "end_time.timezone"))
            or self._default_timezone
        )
        created_timestamp = _parse_unix_seconds(_read_path(payload, "create_time"))
        try:
            return FeishuCalendarEvent.model_validate(
                {
                    "event_id": event_id,
                    "summary": str(_read_path(payload, "summary") or ""),
                    "description": str(_read_path(payload, "description") or ""),
                    "start_timestamp": start_timestamp,
                    "end_timestamp": end_timestamp,
                    "timezone": timezone,
                    "create_timestamp": created_timestamp,
                }
            )
        except ValidationError:
            self._warn("skip event with invalid schema")
            return None

    def _ensure_api_client(self) -> Any:
        if self._api_client is not None:
            return self._api_client
        sdk_module = self._ensure_sdk_module()
        self._api_client = sdk_module.Client.builder().app_id(self._app_id).app_secret(self._app_secret).build()
        return self._api_client

    def _ensure_sdk_module(self) -> Any:
        if self._sdk_module is not None:
            return self._sdk_module
        import lark_oapi as lark_oapi_module  # type: ignore[import-untyped]

        self._sdk_module = lark_oapi_module
        return self._sdk_module

    def _ensure_calendar_module(self) -> Any:
        if self._calendar_module is not None:
            return self._calendar_module
        import lark_oapi.api.calendar.v4 as calendar_v4  # type: ignore[import-untyped]

        self._calendar_module = calendar_v4
        return self._calendar_module

    @staticmethod
    def _is_success(response: Any) -> bool:
        success = getattr(response, "success", None)
        if callable(success):
            try:
                return bool(success())
            except Exception:  # noqa: BLE001
                return False
        code = _parse_int(_read_path(response, "code"))
        return code in (None, 0)

    def _ensure_success(self, *, response: Any, operation: str) -> None:
        if self._is_success(response):
            return
        code = _parse_int(_read_path(response, "code"))
        msg = str(_read_path(response, "msg") or "")
        raise FeishuCalendarClientError(
            f"feishu calendar {operation} failed: code={code if code is not None else 'unknown'}, msg={msg}",
            code=code,
        )

    def _warn(self, message: str) -> None:
        logger = self._logger
        if logger is None:
            return
        logger.warning(message, extra={"event": "feishu_calendar_client_warning"})


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


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
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


def _parse_unix_seconds(value: Any) -> int | None:
    parsed = _parse_int(value)
    if parsed is None:
        return None
    # Feishu may return create_time in milliseconds.
    if abs(parsed) >= 10**12:
        return parsed // 1000
    return parsed
