from __future__ import annotations

import logging
import tempfile
import time
import unittest
from datetime import datetime, timedelta
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

    def queue_created_event_ids(self, event_ids: list[str]) -> None:
        self._create_event_ids.extend(event_ids)

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
            reconcile_interval_minutes=10,
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

    def test_on_local_schedule_added_creates_feishu_mapping_async(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-03-05 13:00", tag="work")
        self.client.queue_created_event_ids(["evt_add_1"])
        self.service.start()

        self.service.on_local_schedule_added(schedule_id=schedule_id)

        self.assertTrue(self._wait_until(lambda: self.db.get_schedule_feishu_mapping(schedule_id) is not None))
        mapping = self.db.get_schedule_feishu_mapping(schedule_id)
        self.assertIsNotNone(mapping)
        assert mapping is not None
        self.assertEqual(mapping.feishu_event_id, "evt_add_1")
        self.assertEqual(mapping.calendar_id, "cal_1")

    def test_on_local_schedule_updated_deletes_old_event_and_creates_new_one(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-03-05 13:00", tag="work")
        self.db.upsert_schedule_feishu_mapping(
            schedule_id=schedule_id,
            feishu_event_id="evt_old",
            calendar_id="cal_1",
        )
        self.client.queue_created_event_ids(["evt_new"])
        self.service.start()

        self.service.on_local_schedule_updated(schedule_id=schedule_id)

        self.assertTrue(
            self._wait_until(
                lambda: (self.db.get_schedule_feishu_mapping(schedule_id) or object()).feishu_event_id == "evt_new"
            )
        )
        self.assertTrue(any(call.get("event_id") == "evt_old" for call in self.client.delete_calls))

    def test_on_local_schedule_deleted_uses_provided_event_id(self) -> None:
        self.service.start()

        self.service.on_local_schedule_deleted(schedule_id=42, feishu_event_id="evt_del")

        self.assertTrue(self._wait_until(lambda: len(self.client.delete_calls) == 1))
        self.assertEqual(self.client.delete_calls[0].get("event_id"), "evt_del")

    def test_run_startup_bootstrap_sync_clears_and_rebuilds_window(self) -> None:
        schedule_id = self.db.add_schedule("本地会议", "2026-03-05 14:00", tag="work")
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
        mapping = self.db.get_schedule_feishu_mapping(schedule_id)
        self.assertIsNotNone(mapping)
        assert mapping is not None
        self.assertEqual(mapping.feishu_event_id, "evt_rebuilt")

    def test_window_bounds_are_day_aligned(self) -> None:
        start, end = self.service._window_bounds(datetime(2026, 3, 5, 12, 34, 56))

        self.assertEqual(start, datetime(2026, 3, 3, 0, 0, 0))
        self.assertEqual(end, datetime(2026, 3, 10, 23, 59, 59))

    def test_startup_bootstrap_delays_first_reconcile_pull(self) -> None:
        self.service.run_startup_bootstrap_sync()
        list_calls_after_bootstrap = len(self.client.list_calls)

        self.service.poll_scheduled_reconcile()
        self.assertEqual(len(self.client.list_calls), list_calls_after_bootstrap)

        self.clock.now = self.clock.now + timedelta(minutes=10)
        self.service.poll_scheduled_reconcile()
        self.assertEqual(len(self.client.list_calls), list_calls_after_bootstrap + 1)

    def test_poll_scheduled_reconcile_converges_local_to_feishu(self) -> None:
        keep_id = self.db.add_schedule("旧标题", "2026-03-05 13:00", duration_minutes=60, tag="old")
        self.db.upsert_schedule_feishu_mapping(
            schedule_id=keep_id,
            feishu_event_id="evt_keep",
            calendar_id="cal_1",
        )
        delete_mapped_id = self.db.add_schedule("待删除映射", "2026-03-05 15:00", duration_minutes=30, tag="tmp")
        self.db.upsert_schedule_feishu_mapping(
            schedule_id=delete_mapped_id,
            feishu_event_id="evt_gone",
            calendar_id="cal_1",
        )
        delete_unmapped_id = self.db.add_schedule("待删除未映射", "2026-03-05 16:00", duration_minutes=30, tag="tmp")

        self.client.list_events_result = [
            FeishuCalendarEvent(
                event_id="evt_keep",
                summary="新标题",
                description="new_tag",
                start_timestamp=int(datetime(2026, 3, 5, 13, 30).timestamp()),
                end_timestamp=int(datetime(2026, 3, 5, 14, 45).timestamp()),
                timezone="Asia/Shanghai",
            ),
            FeishuCalendarEvent(
                event_id="evt_new",
                summary="飞书新增",
                description="from_feishu",
                start_timestamp=int(datetime(2026, 3, 6, 9, 0).timestamp()),
                end_timestamp=int(datetime(2026, 3, 6, 10, 0).timestamp()),
                timezone="Asia/Shanghai",
            ),
        ]

        self.service.poll_scheduled_reconcile()

        updated = self.db.get_schedule(keep_id)
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.title, "新标题")
        self.assertEqual(updated.tag, "new_tag")
        self.assertEqual(updated.event_time, "2026-03-05 13:30")
        self.assertEqual(updated.duration_minutes, 75)
        self.assertIsNone(updated.remind_at)

        self.assertIsNone(self.db.get_schedule(delete_mapped_id))
        self.assertIsNone(self.db.get_schedule(delete_unmapped_id))

        new_mapping = self.db.get_schedule_feishu_mapping_by_event_id("evt_new", calendar_id="cal_1")
        self.assertIsNotNone(new_mapping)
        assert new_mapping is not None
        new_schedule = self.db.get_schedule(new_mapping.schedule_id)
        self.assertIsNotNone(new_schedule)
        assert new_schedule is not None
        self.assertEqual(new_schedule.title, "飞书新增")
        self.assertEqual(new_schedule.tag, "from_feishu")


if __name__ == "__main__":
    unittest.main()
