from __future__ import annotations

import logging
from typing import Any

from assistant_app.schemas.feishu import (
    FeishuCalendarEvent,
    inspect_feishu_calendar_event_payload,
    parse_feishu_calendar_create_response,
    parse_feishu_calendar_event,
    parse_feishu_calendar_list_response,
    parse_feishu_response_status,
)

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
                module.TimeInfo.builder()
                .timestamp(str(start_timestamp))
                .timezone(timezone or self._default_timezone)
                .build()
            )
            .end_time(
                module.TimeInfo.builder()
                .timestamp(str(end_timestamp))
                .timezone(timezone or self._default_timezone)
                .build()
            )
            .build()
        )
        request = module.CreateCalendarEventRequest.builder().calendar_id(calendar_id).request_body(event).build()
        response = api_client.calendar.v4.calendar_event.create(request)
        self._ensure_success(response=response, operation="create")
        parsed_response = parse_feishu_calendar_create_response(response)
        event_id = parsed_response.event_id_value() if parsed_response is not None else None
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
        status = parse_feishu_response_status(response)
        code = status.code if status is not None else None
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
            parsed_response = parse_feishu_calendar_list_response(response)
            payload_items = parsed_response.raw_items() if parsed_response is not None else []
            for raw_item in payload_items:
                parsed = self._parse_event(raw_item)
                if parsed is not None:
                    events.append(parsed)
            if parsed_response is None or not parsed_response.has_more_items():
                break
            next_token = parsed_response.page_token_value()
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
        parsed = parse_feishu_calendar_event(payload, default_timezone=self._default_timezone)
        if parsed is not None:
            return parsed
        _, reason, has_event_id, has_start, has_end = inspect_feishu_calendar_event_payload(
            payload,
            default_timezone=self._default_timezone,
        )
        self._log_event_schema_invalid(
            reason=reason or "unknown",
            has_event_id=has_event_id,
            has_start=has_start,
            has_end=has_end,
        )
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
        status = parse_feishu_response_status(response)
        return status is None or status.is_success()

    def _ensure_success(self, *, response: Any, operation: str) -> None:
        if self._is_success(response):
            return
        status = parse_feishu_response_status(response)
        code = status.code if status is not None else None
        msg = status.msg or "" if status is not None else ""
        raise FeishuCalendarClientError(
            f"feishu calendar {operation} failed: code={code if code is not None else 'unknown'}, msg={msg}",
            code=code,
        )

    def _warn(self, message: str) -> None:
        logger = self._logger
        if logger is None:
            return
        logger.warning(message, extra={"event": "feishu_calendar_client_warning"})

    def _log_event_schema_invalid(
        self,
        *,
        reason: str,
        has_event_id: bool,
        has_start: bool,
        has_end: bool,
    ) -> None:
        logger = self._logger
        if logger is None:
            return
        logger.warning(
            "feishu calendar event schema invalid",
            extra={
                "event": "feishu_calendar_event_schema_invalid",
                "context": {
                    "reason": reason,
                    "has_event_id": has_event_id,
                    "has_start": has_start,
                    "has_end": has_end,
                },
            },
        )
