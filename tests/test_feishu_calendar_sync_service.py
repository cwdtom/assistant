from __future__ import annotations

import logging
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from assistant_app.db import AssistantDB
from assistant_app.feishu_calendar_client import FeishuCalendarEvent
from assistant_app.feishu_calendar_sync_service import FeishuCalendarSyncService


class _FakeCalendarClient:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self._created_index = 0
        self._create_event_ids: list[str] = []
        self.list_events_result: list[FeishuCalendarEvent] = []
        self._queued_list_events_results: list[list[FeishuCalendarEvent]] = []

    def queue_created_event_ids(self, event_ids: list[str]) -> None:
        self._create_event_ids.extend(event_ids)

    def queue_list_events_results(self, payloads: list[list[FeishuCalendarEvent]]) -> None:
        self._queued_list_events_results.extend(payloads)

    def create_event(self, **kwargs):  # type: ignore[no-untyped-def]
        self.create_calls.append(kwargs)
        if self._created_index < len(self._create_event_ids):
            event_id = self._create_event_ids[self._created_index]
        else:
            event_id = f"evt_auto_{self._created_index + 1}"
        self._created_index += 1
        return event_id

    def delete_event(self, **kwargs):  # type: ignore[no-untyped-def]
        self.delete_calls.append(kwargs)
        return True

    def list_events(self, **kwargs):  # type: ignore[no-untyped-def]
        self.list_calls.append(kwargs)
        if self._queued_list_events_results:
            return list(self._queued_list_events_results.pop(0))
        return list(self.list_events_result)


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class FeishuCalendarSyncServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = AssistantDB(str(Path(self.tmp.name) / "assistant.db"))
        self.client = _FakeCalendarClient()
        self.clock = _Clock(datetime(2026, 3, 5, 12, 0, 0))
        self.service = FeishuCalendarSyncService(
            db=self.db,
            client=self.client,
            logger=logging.getLogger("test.feishu_calendar_sync"),
            calendar_id="cal_1",
            bootstrap_past_days=2,
            bootstrap_future_days=5,
            clock=self.clock,
        )

    def tearDown(self) -> None:
        self.service.stop()
        self.tmp.cleanup()

    @staticmethod
    def _wait_until(predicate, timeout: float = 2.0) -> bool:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def test_on_local_schedule_added_creates_feishu_event_async(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-03-05 13:00", tag="work")
        self.client.queue_created_event_ids(["evt_add_1"])
        self.service.start()

        self.service.on_local_schedule_added(schedule_id=schedule_id)

        self.assertTrue(self._wait_until(lambda: len(self.client.create_calls) == 1))
        self.assertEqual(self.client.create_calls[0].get("summary"), "项目同步")
        self.assertEqual(self.client.create_calls[0].get("description"), "work")

    def test_on_local_schedule_added_skips_when_identity_exists(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-03-05 13:00", tag="work")
        self.client.list_events_result = [
            FeishuCalendarEvent(
                event_id="evt_existing",
                summary="项目同步",
                description="work",
                start_timestamp=int(datetime(2026, 3, 5, 13, 0).timestamp()),
                end_timestamp=int(datetime(2026, 3, 5, 14, 0).timestamp()),
                timezone="Asia/Shanghai",
                create_timestamp=int(datetime(2026, 3, 1, 9, 0).timestamp()),
            )
        ]
        self.service.start()

        self.service.on_local_schedule_added(schedule_id=schedule_id)

        self.assertTrue(self._wait_until(lambda: len(self.client.list_calls) >= 1))
        self.assertEqual(self.client.create_calls, [])

    def test_on_local_schedule_updated_deletes_old_identity_and_creates_new_event(self) -> None:
        schedule_id = self.db.add_schedule("旧标题", "2026-03-05 13:00", duration_minutes=60, tag="old")
        old_schedule = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(old_schedule)
        assert old_schedule is not None

        self.assertTrue(
            self.db.update_schedule(
                schedule_id,
                title="新标题",
                event_time="2026-03-05 13:30",
                duration_minutes=75,
                tag="new_tag",
            )
        )

        self.client.queue_list_events_results(
            [
                [
                    FeishuCalendarEvent(
                        event_id="evt_old",
                        summary="旧标题",
                        description="old",
                        start_timestamp=int(datetime(2026, 3, 5, 13, 0).timestamp()),
                        end_timestamp=int(datetime(2026, 3, 5, 14, 0).timestamp()),
                        timezone="Asia/Shanghai",
                        create_timestamp=int(datetime(2026, 3, 1, 8, 0).timestamp()),
                    )
                ],
                [],
            ]
        )
        self.client.queue_created_event_ids(["evt_new"])
        self.service.start()

        self.service.on_local_schedule_updated(schedule_id=schedule_id, old_schedule=old_schedule)

        self.assertTrue(self._wait_until(lambda: len(self.client.create_calls) == 1))
        self.assertTrue(any(call.get("event_id") == "evt_old" for call in self.client.delete_calls))
        self.assertEqual(self.client.create_calls[0].get("summary"), "新标题")
        self.assertEqual(self.client.create_calls[0].get("description"), "new_tag")

    def test_on_local_schedule_deleted_uses_identity_and_picks_earliest_created(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-03-05 13:00", tag="work")
        deleted_schedule = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(deleted_schedule)
        assert deleted_schedule is not None

        self.client.list_events_result = [
            FeishuCalendarEvent(
                event_id="evt_late",
                summary="项目同步",
                description="work",
                start_timestamp=int(datetime(2026, 3, 5, 13, 0).timestamp()),
                end_timestamp=int(datetime(2026, 3, 5, 14, 0).timestamp()),
                timezone="Asia/Shanghai",
                create_timestamp=int(datetime(2026, 3, 2, 10, 0).timestamp()),
            ),
            FeishuCalendarEvent(
                event_id="evt_early",
                summary="项目同步",
                description="work",
                start_timestamp=int(datetime(2026, 3, 5, 13, 0).timestamp()),
                end_timestamp=int(datetime(2026, 3, 5, 14, 0).timestamp()),
                timezone="Asia/Shanghai",
                create_timestamp=int(datetime(2026, 3, 1, 10, 0).timestamp()),
            ),
        ]
        self.service.start()

        self.service.on_local_schedule_deleted(schedule_id=schedule_id, deleted_schedule=deleted_schedule)

        self.assertTrue(self._wait_until(lambda: len(self.client.delete_calls) == 1))
        self.assertEqual(self.client.delete_calls[0].get("event_id"), "evt_early")

    def test_run_startup_bootstrap_sync_clears_and_rebuilds_window(self) -> None:
        self.db.add_schedule("本地会议", "2026-03-05 14:00", tag="work")
        self.client.list_events_result = [
            FeishuCalendarEvent(
                event_id="evt_existing",
                summary="旧日程",
                description="old",
                start_timestamp=1741150800,
                end_timestamp=1741154400,
                timezone="Asia/Shanghai",
            )
        ]
        self.client.queue_created_event_ids(["evt_rebuilt"])

        self.service.run_startup_bootstrap_sync()

        self.assertEqual(len(self.client.delete_calls), 1)
        self.assertEqual(self.client.delete_calls[0].get("event_id"), "evt_existing")
        self.assertEqual(len(self.client.create_calls), 1)
        self.assertEqual(self.client.create_calls[0].get("summary"), "本地会议")

    def test_window_bounds_are_day_aligned(self) -> None:
        start, end = self.service._window_bounds(datetime(2026, 3, 5, 12, 34, 56))

        self.assertEqual(start, datetime(2026, 3, 3, 0, 0, 0))
        self.assertEqual(end, datetime(2026, 3, 10, 23, 59, 59))


if __name__ == "__main__":
    unittest.main()
