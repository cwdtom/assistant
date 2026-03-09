from __future__ import annotations

import io
import json
import logging
import unittest
from types import SimpleNamespace

import lark_oapi.api.calendar.v4 as calendar_v4  # type: ignore[import-untyped]
from assistant_app.feishu_calendar_client import (
    FeishuCalendarClient,
    FeishuCalendarClientError,
)
from assistant_app.logging_setup import JsonLinesFormatter
from assistant_app.schemas.feishu import FeishuCalendarEvent
from pydantic import ValidationError


class _FakeResponse:
    def __init__(self, *, ok: bool, code: int = 0, msg: str = "success", data=None) -> None:
        self._ok = ok
        self.code = code
        self.msg = msg
        self.data = data

    def success(self) -> bool:
        return self._ok


class _FakeCalendarEventAPI:
    def __init__(self) -> None:
        self.create_requests = []
        self.delete_requests = []
        self.list_requests = []
        self.create_response = _FakeResponse(ok=True, data={"event": {"event_id": "evt_default"}})
        self.delete_response = _FakeResponse(ok=True, data={})
        self.list_responses: list[_FakeResponse] = []

    def create(self, request):  # type: ignore[no-untyped-def]
        self.create_requests.append(request)
        return self.create_response

    def delete(self, request):  # type: ignore[no-untyped-def]
        self.delete_requests.append(request)
        return self.delete_response

    def list(self, request):  # type: ignore[no-untyped-def]
        self.list_requests.append(request)
        if self.list_responses:
            return self.list_responses.pop(0)
        return _FakeResponse(ok=True, data={"has_more": False, "items": []})


class _FakeApiClient:
    def __init__(self, calendar_api: _FakeCalendarEventAPI) -> None:
        self.calendar = SimpleNamespace(v4=SimpleNamespace(calendar_event=calendar_api))


class FeishuCalendarClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calendar_api = _FakeCalendarEventAPI()
        self.client = FeishuCalendarClient(
            app_id="app_id",
            app_secret="app_secret",
            api_client=_FakeApiClient(self.calendar_api),
            calendar_module=calendar_v4,
        )

    def test_create_event_builds_request_and_returns_event_id(self) -> None:
        self.calendar_api.create_response = _FakeResponse(ok=True, data={"event": {"event_id": "evt_1"}})

        event_id = self.client.create_event(
            calendar_id="cal_1",
            summary="项目同步",
            description="work",
            start_timestamp=1700000000,
            end_timestamp=1700003600,
            timezone="Asia/Shanghai",
            need_notification=False,
        )

        self.assertEqual(event_id, "evt_1")
        self.assertEqual(len(self.calendar_api.create_requests), 1)
        request = self.calendar_api.create_requests[0]
        self.assertEqual(request.calendar_id, "cal_1")
        self.assertEqual(request.request_body.summary, "项目同步")
        self.assertEqual(request.request_body.description, "work")
        self.assertEqual(request.request_body.start_time.timestamp, "1700000000")
        self.assertEqual(request.request_body.start_time.timezone, "Asia/Shanghai")
        self.assertEqual(request.request_body.end_time.timestamp, "1700003600")
        self.assertEqual(request.request_body.end_time.timezone, "Asia/Shanghai")

    def test_delete_event_ignore_not_found_returns_false(self) -> None:
        self.calendar_api.delete_response = _FakeResponse(ok=False, code=193001, msg="event not found")

        deleted = self.client.delete_event(
            calendar_id="cal_1",
            event_id="evt_missing",
            need_notification=False,
            ignore_not_found=True,
        )

        self.assertFalse(deleted)

    def test_delete_event_raises_for_non_ignored_error(self) -> None:
        self.calendar_api.delete_response = _FakeResponse(ok=False, code=190002, msg="invalid parameters")

        with self.assertRaises(FeishuCalendarClientError):
            self.client.delete_event(
                calendar_id="cal_1",
                event_id="evt_1",
                need_notification=False,
                ignore_not_found=False,
            )

    def test_list_events_supports_pagination(self) -> None:
        self.calendar_api.list_responses = [
            _FakeResponse(
                ok=True,
                data={
                    "has_more": True,
                    "page_token": "token_1",
                    "items": [
                        {
                            "event_id": "evt_1",
                            "summary": "A",
                            "description": "work",
                            "create_time": "1700000000123",
                            "start_time": {"time_stamp": "1700000000", "timezone": "Asia/Shanghai"},
                            "end_time": {"time_stamp": "1700003600", "timezone": "Asia/Shanghai"},
                        }
                    ],
                },
            ),
            _FakeResponse(
                ok=True,
                data={
                    "has_more": False,
                    "items": [
                        {
                            "event_id": "evt_2",
                            "summary": "B",
                            "description": "life",
                            "start_time": {"time_stamp": "1700007200", "timezone": "Asia/Shanghai"},
                            "end_time": {"time_stamp": "1700010800", "timezone": "Asia/Shanghai"},
                        }
                    ],
                },
            ),
        ]

        items = self.client.list_events(
            calendar_id="cal_1",
            start_timestamp=1699990000,
            end_timestamp=1700100000,
            page_size=500,
        )

        self.assertEqual([item.event_id for item in items], ["evt_1", "evt_2"])
        self.assertEqual(items[0].create_timestamp, 1700000000)
        self.assertIsNone(items[1].create_timestamp)
        self.assertEqual(len(self.calendar_api.list_requests), 2)
        self.assertIsNone(self.calendar_api.list_requests[0].page_token)
        self.assertEqual(self.calendar_api.list_requests[1].page_token, "token_1")

    def test_list_events_accepts_timestamp_alias_and_default_timezone(self) -> None:
        self.calendar_api.list_responses = [
            _FakeResponse(
                ok=True,
                data={
                    "has_more": False,
                    "items": [
                        {
                            "event_id": "evt_alias",
                            "summary": 123,
                            "description": None,
                            "start_time": {"timestamp": "1700000000"},
                            "end_time": {"timestamp": "1700003600"},
                        }
                    ],
                },
            )
        ]

        items = self.client.list_events(
            calendar_id="cal_1",
            start_timestamp=1699990000,
            end_timestamp=1700100000,
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].event_id, "evt_alias")
        self.assertEqual(items[0].summary, "123")
        self.assertEqual(items[0].description, "")
        self.assertEqual(items[0].timezone, "Asia/Shanghai")

    def test_list_events_skips_invalid_item_schema(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("test.feishu_calendar_client.invalid_item")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLinesFormatter())
        logger.addHandler(handler)
        client = FeishuCalendarClient(
            app_id="app_id",
            app_secret="app_secret",
            api_client=_FakeApiClient(self.calendar_api),
            calendar_module=calendar_v4,
            logger=logger,
        )
        self.calendar_api.list_responses = [
            _FakeResponse(
                ok=True,
                data={
                    "has_more": False,
                    "items": [
                        {
                            "event_id": "evt_bad",
                            "start_time": {"time_stamp": True, "timezone": "Asia/Shanghai"},
                            "end_time": {"time_stamp": "1700003600", "timezone": "Asia/Shanghai"},
                        }
                    ],
                },
            )
        ]

        try:
            items = client.list_events(
                calendar_id="cal_1",
                start_timestamp=1699990000,
                end_timestamp=1700100000,
            )
        finally:
            logger.removeHandler(handler)
            handler.close()
            for original in original_handlers:
                logger.addHandler(original)
            logger.propagate = original_propagate

        self.assertEqual(items, [])
        records = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
        invalid_events = [item for item in records if item.get("event") == "feishu_calendar_event_schema_invalid"]
        self.assertEqual(len(invalid_events), 1)
        self.assertEqual(
            invalid_events[0].get("context"),
            {
                "reason": "missing_timestamp_range",
                "has_event_id": True,
                "has_start": True,
                "has_end": True,
            },
        )

    def test_list_events_valid_item_does_not_log_invalid_schema_event(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("test.feishu_calendar_client.valid_item")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLinesFormatter())
        logger.addHandler(handler)
        client = FeishuCalendarClient(
            app_id="app_id",
            app_secret="app_secret",
            api_client=_FakeApiClient(self.calendar_api),
            calendar_module=calendar_v4,
            logger=logger,
        )
        self.calendar_api.list_responses = [
            _FakeResponse(
                ok=True,
                data={
                    "has_more": False,
                    "items": [
                        {
                            "event_id": "evt_ok",
                            "summary": "正常事件",
                            "start_time": {"time_stamp": "1700000000", "timezone": "Asia/Shanghai"},
                            "end_time": {"time_stamp": "1700003600", "timezone": "Asia/Shanghai"},
                        }
                    ],
                },
            )
        ]

        try:
            items = client.list_events(
                calendar_id="cal_1",
                start_timestamp=1699990000,
                end_timestamp=1700100000,
            )
        finally:
            logger.removeHandler(handler)
            handler.close()
            for original in original_handlers:
                logger.addHandler(original)
            logger.propagate = original_propagate

        self.assertEqual(len(items), 1)
        records = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
        events = [item.get("event") for item in records]
        self.assertNotIn("feishu_calendar_event_schema_invalid", events)

    def test_feishu_calendar_event_requires_non_empty_identity_fields(self) -> None:
        with self.assertRaises(ValidationError):
            FeishuCalendarEvent(
                event_id=" ",
                summary="A",
                description="work",
                start_timestamp=1700000000,
                end_timestamp=1700003600,
                timezone="Asia/Shanghai",
            )


if __name__ == "__main__":
    unittest.main()
