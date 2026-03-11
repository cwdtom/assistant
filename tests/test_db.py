from __future__ import annotations

import io
import json
import logging
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from assistant_app.db import AssistantDB
from assistant_app.logging_setup import JsonLinesFormatter
from assistant_app.schemas.tools import coerce_schedule_action_payload


class AssistantDBTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "assistant_test.db")
        self.db = AssistantDB(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_schedule_order(self) -> None:
        self.db.add_schedule("晚上的会", "2026-02-20 20:00")
        self.db.add_schedule("早上的会", "2026-02-20 09:00")

        items = self.db.list_schedules()
        self.assertEqual(items[0].title, "早上的会")
        self.assertEqual(items[1].title, "晚上的会")
        self.assertEqual(items[0].duration_minutes, 60)
        self.assertEqual(items[1].duration_minutes, 60)
        self.assertEqual(items[0].tag, "default")
        self.assertEqual(items[1].tag, "default")

    def test_schedule_tag_filter_and_update(self) -> None:
        first_id = self.db.add_schedule("项目站会", "2026-02-20 09:00", tag="work")
        second_id = self.db.add_schedule("生活采购", "2026-02-20 10:00", tag="life")
        self.assertNotEqual(first_id, second_id)

        work_items = self.db.list_schedules(tag="work")
        self.assertEqual(len(work_items), 1)
        self.assertEqual(work_items[0].title, "项目站会")
        self.assertEqual(work_items[0].tag, "work")

        updated = self.db.update_schedule(
            first_id,
            title="项目复盘",
            event_time="2026-02-20 11:00",
            tag="review",
        )
        self.assertTrue(updated)
        changed = self.db.get_schedule(first_id)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertEqual(changed.tag, "review")

    def test_schedule_tag_filter_keeps_recurring_occurrences(self) -> None:
        schedule_id = self.db.add_schedule("每周周会", "2026-02-20 09:00", tag="work")
        self.db.set_schedule_recurrence(
            schedule_id,
            start_time="2026-02-20 09:00",
            repeat_interval_minutes=10080,
            repeat_times=3,
        )
        self.db.add_schedule("生活安排", "2026-02-21 10:00", tag="life")

        work_items = self.db.list_schedules(tag="work")
        self.assertEqual(len(work_items), 3)
        self.assertTrue(all(item.tag == "work" for item in work_items))
        self.assertTrue(all(item.title == "每周周会" for item in work_items))

    def test_add_schedules_batch(self) -> None:
        ids = self.db.add_schedules(
            "晨会",
            ["2026-02-20 09:00", "2026-02-21 09:00", "2026-02-22 09:00"],
        )
        self.assertEqual(len(ids), 3)
        self.assertEqual(ids, [1, 2, 3])

        items = self.db.list_schedules()
        self.assertEqual(len(items), 3)
        self.assertEqual(
            [item.event_time for item in items], ["2026-02-20 09:00", "2026-02-21 09:00", "2026-02-22 09:00"]
        )
        self.assertEqual([item.duration_minutes for item in items], [60, 60, 60])

    def test_add_schedule_with_custom_duration(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-02-20 10:00", duration_minutes=45)
        item = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.duration_minutes, 45)

    def test_schedule_remind_fields_are_persisted(self) -> None:
        schedule_id = self.db.add_schedule(
            "项目同步",
            "2026-02-20 10:00",
            duration_minutes=45,
            remind_at="2026-02-20 09:45",
        )
        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 10:00",
                repeat_interval_minutes=1440,
                repeat_times=3,
                remind_start_time="2026-02-20 09:30",
            )
        )

        item = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.remind_at, "2026-02-20 09:45")
        self.assertEqual(item.repeat_remind_start_time, "2026-02-20 09:30")

        listed = self.db.list_schedules()
        self.assertTrue(all(x.remind_at == "2026-02-20 09:45" for x in listed))
        self.assertTrue(all(x.repeat_remind_start_time == "2026-02-20 09:30" for x in listed))

    def test_add_schedules_batch_with_custom_duration(self) -> None:
        ids = self.db.add_schedules(
            "晨会",
            ["2026-02-20 09:00", "2026-02-21 09:00"],
            duration_minutes=30,
        )
        self.assertEqual(ids, [1, 2])
        items = self.db.list_schedules()
        self.assertEqual([item.duration_minutes for item in items], [30, 30])

    def test_list_schedules_merges_recurring_rules(self) -> None:
        schedule_id = self.db.add_schedule("周会", "2026-02-20 09:00", duration_minutes=30)
        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 09:00",
                repeat_interval_minutes=10080,
                repeat_times=3,
            )
        )

        items = self.db.list_schedules()
        self.assertEqual(
            [item.event_time for item in items],
            ["2026-02-20 09:00", "2026-02-27 09:00", "2026-03-06 09:00"],
        )
        self.assertEqual([item.id for item in items], [schedule_id, schedule_id, schedule_id])

    def test_list_schedules_does_not_truncate_finite_recurrence_without_window(self) -> None:
        schedule_id = self.db.add_schedule("高频任务", "2026-01-01 00:00", duration_minutes=20)
        self.db.set_schedule_recurrence(
            schedule_id,
            start_time="2026-01-01 00:00",
            repeat_interval_minutes=60,
            repeat_times=3000,
        )

        items = self.db.list_schedules()
        self.assertEqual(len(items), 3000)
        self.assertEqual(items[0].event_time, "2026-01-01 00:00")
        self.assertEqual(items[-1].event_time, "2026-05-05 23:00")

    def test_recurring_schedule_can_be_disabled_and_enabled(self) -> None:
        schedule_id = self.db.add_schedule("周会", "2026-02-20 09:00", duration_minutes=60)
        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 09:00",
                repeat_interval_minutes=10080,
                repeat_times=3,
            )
        )

        self.assertTrue(self.db.set_schedule_recurrence_enabled(schedule_id, False))
        disabled_items = self.db.list_schedules()
        self.assertEqual([item.event_time for item in disabled_items], ["2026-02-20 09:00"])

        self.assertTrue(self.db.set_schedule_recurrence_enabled(schedule_id, True))
        enabled_items = self.db.list_schedules()
        self.assertIn("2026-02-27 09:00", [item.event_time for item in enabled_items])

    def test_set_schedule_recurrence_enabled_without_rule_returns_false(self) -> None:
        schedule_id = self.db.add_schedule("单次会", "2026-02-20 09:00", duration_minutes=60)
        self.assertFalse(self.db.set_schedule_recurrence_enabled(schedule_id, False))

    def test_set_schedule_recurrence_supports_infinite_repeat_times(self) -> None:
        schedule_id = self.db.add_schedule("循环站会", "2026-02-20 09:00", duration_minutes=30)
        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 09:00",
                repeat_interval_minutes=60,
                repeat_times=-1,
            )
        )
        item = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.repeat_times, -1)

    def test_set_schedule_recurrence_rejects_invalid_types_and_keeps_clear_semantics(self) -> None:
        schedule_id = self.db.add_schedule("循环站会", "2026-02-20 09:00", duration_minutes=30)
        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 09:00",
                repeat_interval_minutes=1440,
                repeat_times=3,
            )
        )

        self.assertFalse(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 09:00",
                repeat_interval_minutes=True,
                repeat_times=3,
            )
        )
        self.assertFalse(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 09:00",
                repeat_interval_minutes=1440,
                repeat_times=True,
            )
        )

        kept = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(kept)
        assert kept is not None
        self.assertEqual(kept.repeat_times, 3)

        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 09:00",
                repeat_interval_minutes=1440,
                repeat_times=1,
            )
        )
        cleared = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(cleared)
        assert cleared is not None
        self.assertIsNone(cleared.repeat_times)

    def test_list_schedules_respects_window_max_range(self) -> None:
        now = datetime.now()
        inside = (now + timedelta(days=5)).strftime("%Y-%m-%d 09:00")
        outside = (now + timedelta(days=45)).strftime("%Y-%m-%d 09:00")
        self.db.add_schedule("窗口内", inside, duration_minutes=30)
        self.db.add_schedule("窗口外", outside, duration_minutes=30)

        items = self.db.list_schedules(
            window_start=now,
            window_end=now + timedelta(days=90),
            max_window_days=31,
        )
        titles = [item.title for item in items]
        self.assertIn("窗口内", titles)
        self.assertNotIn("窗口外", titles)

    def test_infinite_recurrence_respects_window_bounds(self) -> None:
        now = datetime.now().replace(minute=0, second=0, microsecond=0)
        start_text = now.strftime("%Y-%m-%d %H:%M")
        schedule_id = self.db.add_schedule("每小时站会", start_text, duration_minutes=20)
        self.db.set_schedule_recurrence(
            schedule_id,
            start_time=start_text,
            repeat_interval_minutes=60,
            repeat_times=-1,
        )

        items = self.db.list_schedules(
            window_start=now,
            window_end=now + timedelta(hours=3),
            max_window_days=31,
        )
        self.assertGreaterEqual(len(items), 4)
        self.assertTrue(all(item.title == "每小时站会" for item in items))

    def test_delete_schedule_removes_recurring_rules(self) -> None:
        schedule_id = self.db.add_schedule("周会", "2026-02-20 09:00", duration_minutes=60)
        self.db.set_schedule_recurrence(
            schedule_id,
            start_time="2026-02-20 09:00",
            repeat_interval_minutes=10080,
            repeat_times=3,
        )
        self.assertTrue(self.db.delete_schedule(schedule_id))
        self.assertEqual(self.db.list_schedules(), [])

    def test_legacy_schedule_feishu_sync_table_is_dropped_on_init(self) -> None:
        legacy_path = Path(self.tmp.name) / "assistant_legacy.db"
        with sqlite3.connect(legacy_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schedule_feishu_sync (
                    schedule_id INTEGER PRIMARY KEY,
                    feishu_event_id TEXT NOT NULL UNIQUE,
                    calendar_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            exists_before = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'schedule_feishu_sync'
                """
            ).fetchone()
        self.assertIsNotNone(exists_before)

        AssistantDB(str(legacy_path))

        with sqlite3.connect(legacy_path) as conn:
            exists_after = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'schedule_feishu_sync'
                """
            ).fetchone()
        self.assertIsNone(exists_after)

    def test_list_base_schedules_in_window_excludes_recurring_expansion(self) -> None:
        schedule_id = self.db.add_schedule("周会", "2026-02-20 09:00")
        self.db.set_schedule_recurrence(
            schedule_id,
            start_time="2026-02-20 09:00",
            repeat_interval_minutes=1440,
            repeat_times=5,
        )
        start = datetime.strptime("2026-02-20 00:00", "%Y-%m-%d %H:%M")
        end = datetime.strptime("2026-02-23 23:59", "%Y-%m-%d %H:%M")
        items = self.db.list_base_schedules_in_window(window_start=start, window_end=end, max_window_days=31)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].event_time, "2026-02-20 09:00")

    def test_schedule_crud(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-02-20 10:00")

        item = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.title, "项目同步")
        self.assertEqual(item.duration_minutes, 60)

        updated = self.db.update_schedule(
            schedule_id,
            title="项目复盘",
            event_time="2026-02-21 11:30",
            duration_minutes=90,
        )
        self.assertTrue(updated)

        changed = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertEqual(changed.title, "项目复盘")
        self.assertEqual(changed.event_time, "2026-02-21 11:30")
        self.assertEqual(changed.duration_minutes, 90)

        deleted = self.db.delete_schedule(schedule_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_schedule(schedule_id))

    def test_update_schedule_can_update_remind_fields(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-02-20 10:00")
        self.db.set_schedule_recurrence(
            schedule_id,
            start_time="2026-02-20 10:00",
            repeat_interval_minutes=10080,
            repeat_times=3,
        )

        updated = self.db.update_schedule(
            schedule_id,
            title="项目同步-改",
            event_time="2026-02-21 11:00",
            remind_at="2026-02-21 10:40",
            repeat_remind_start_time="2026-02-21 10:20",
        )
        self.assertTrue(updated)

        changed = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertEqual(changed.remind_at, "2026-02-21 10:40")
        self.assertEqual(changed.repeat_remind_start_time, "2026-02-21 10:20")

    def test_update_schedule_without_duration_keeps_existing_duration(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-02-20 10:00", duration_minutes=45)
        updated = self.db.update_schedule(
            schedule_id,
            title="项目同步-改",
            event_time="2026-02-20 11:00",
        )
        self.assertTrue(updated)
        changed = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertEqual(changed.duration_minutes, 45)

    def test_update_schedule_can_reset_tag_and_clear_remind_fields(self) -> None:
        schedule_id = self.db.add_schedule(
            "项目同步",
            "2026-02-20 10:00",
            tag="work",
            remind_at="2026-02-20 09:45",
        )
        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 10:00",
                repeat_interval_minutes=1440,
                repeat_times=3,
                remind_start_time="2026-02-20 09:30",
            )
        )

        updated = self.db.update_schedule(
            schedule_id,
            title="项目同步-改",
            event_time="2026-02-20 11:00",
            tag=None,
            remind_at=None,
            repeat_remind_start_time="",
        )
        self.assertTrue(updated)

        changed = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertEqual(changed.tag, "default")
        self.assertIsNone(changed.remind_at)
        self.assertIsNone(changed.repeat_remind_start_time)

    def test_list_base_schedules_excludes_recurring_expansion(self) -> None:
        schedule_id = self.db.add_schedule("周会", "2026-02-20 10:00", duration_minutes=45)
        self.db.set_schedule_recurrence(
            schedule_id,
            start_time="2026-02-20 10:00",
            repeat_interval_minutes=10080,
            repeat_times=3,
        )

        all_items = self.db.list_schedules()
        base_items = self.db.list_base_schedules()
        self.assertEqual(len(all_items), 3)
        self.assertEqual(len(base_items), 1)
        self.assertEqual(base_items[0].event_time, "2026-02-20 10:00")

    def test_save_reminder_delivery_is_idempotent(self) -> None:
        first = self.db.save_reminder_delivery(
            reminder_key="schedule:1:2026-02-25 10:00:2026-02-25 09:00",
            source_type="schedule",
            source_id=1,
            occurrence_time="2026-02-25 10:00",
            remind_time="2026-02-25 09:00",
        )
        second = self.db.save_reminder_delivery(
            reminder_key="schedule:1:2026-02-25 10:00:2026-02-25 09:00",
            source_type="schedule",
            source_id=1,
            occurrence_time="2026-02-25 10:00",
            remind_time="2026-02-25 09:00",
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(self.db.has_reminder_delivery("schedule:1:2026-02-25 10:00:2026-02-25 09:00"))
        deliveries = self.db.list_reminder_deliveries()
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].source_type, "schedule")

    def test_list_recurring_rules_returns_saved_rule(self) -> None:
        schedule_id = self.db.add_schedule("周会", "2026-02-20 10:00")
        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 10:00",
                repeat_interval_minutes=10080,
                repeat_times=3,
                remind_start_time="2026-02-20 09:30",
            )
        )

        rules = self.db.list_recurring_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].schedule_id, schedule_id)
        self.assertEqual(rules[0].repeat_interval_minutes, 10080)
        self.assertEqual(rules[0].remind_start_time, "2026-02-20 09:30")

    def test_schedule_duration_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.db.add_schedule("非法时长", "2026-02-20 10:00", duration_minutes=0)
        with self.assertRaises(ValueError):
            self.db.add_schedules("非法时长", ["2026-02-20 10:00"], duration_minutes=0)

        schedule_id = self.db.add_schedule("正常时长", "2026-02-20 10:00", duration_minutes=30)
        self.assertFalse(
            self.db.update_schedule(
                schedule_id,
                title="正常时长",
                event_time="2026-02-20 10:30",
                duration_minutes=0,
            )
        )
        self.assertFalse(
            self.db.update_schedule(
                schedule_id,
                title="正常时长",
                event_time="2026-02-20 10:30",
                duration_minutes=None,
            )
        )

    def test_schedule_datetime_and_title_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.db.add_schedule("  ", "2026-02-20 10:00")
        with self.assertRaises(ValueError):
            self.db.add_schedule("非法时间", "2026-02-20")
        with self.assertRaises(ValueError):
            self.db.add_schedules("晨会", ["2026-02-20 09:00", "bad-time"])

        schedule_id = self.db.add_schedule("正常日程", "2026-02-20 10:00")
        self.assertFalse(
            self.db.update_schedule(
                schedule_id,
                title="  ",
                event_time="2026-02-20 10:30",
            )
        )
        self.assertFalse(
            self.db.update_schedule(
                schedule_id,
                title="正常日程",
                event_time="bad-time",
            )
        )

    def test_db_add_schedule_matches_tool_payload_normalization(self) -> None:
        compat_payload = coerce_schedule_action_payload(
            {
                "action": "add",
                "event_time": "2026-03-09 10:00",
                "title": "项目同步",
                "tag": " Work ",
                "duration_minutes": " 45 ",
                "remind_at": "2026-03-09 09:30",
            }
        )
        args = compat_payload.arguments

        create_kwargs: dict[str, Any] = {
            "title": "项目同步",
            "event_time": "2026-03-09 10:00",
            "tag": " Work ",
            "duration_minutes": " 45 ",
            "remind_at": "2026-03-09 09:30",
        }
        schedule_id = self.db.add_schedule(**create_kwargs)
        item = self.db.get_schedule(schedule_id)

        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.event_time, args.event_time)
        self.assertEqual(item.tag, args.tag or "default")
        self.assertEqual(item.duration_minutes, args.duration_minutes)
        self.assertEqual(item.remind_at, args.remind_at)

    def test_db_update_schedule_matches_tool_payload_normalization(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-03-09 10:00", tag="work", duration_minutes=30)
        compat_payload = coerce_schedule_action_payload(
            {
                "action": "update",
                "id": schedule_id,
                "event_time": "2026-03-09 11:00",
                "title": "项目复盘",
                "tag": " Review ",
                "duration_minutes": " 50 ",
                "remind_at": "2026-03-09 10:30",
            }
        )
        args = compat_payload.arguments

        update_kwargs: dict[str, Any] = {
            "title": "项目复盘",
            "event_time": "2026-03-09 11:00",
            "tag": " Review ",
            "duration_minutes": " 50 ",
            "remind_at": "2026-03-09 10:30",
        }
        updated = self.db.update_schedule(schedule_id, **update_kwargs)
        item = self.db.get_schedule(schedule_id)

        self.assertTrue(updated)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.event_time, args.event_time)
        self.assertEqual(item.title, args.title)
        self.assertEqual(item.tag, args.tag or "default")
        self.assertEqual(item.duration_minutes, args.duration_minutes)
        self.assertEqual(item.remind_at, args.remind_at)

    def test_db_logs_input_validation_failure_for_invalid_update_schedule(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("test.db.validation_failure")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            logger.handlers.clear()
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = logging.StreamHandler(stream)
            handler.setFormatter(JsonLinesFormatter())
            logger.addHandler(handler)
            db = AssistantDB(self.db_path, logger=logger)
            schedule_id = db.add_schedule("正常日程", "2026-02-20 10:00")

            self.assertFalse(
                db.update_schedule(
                    schedule_id,
                    title="正常日程",
                    event_time="2026-02-20 10:30",
                    duration_minutes=0,
                )
            )

            records = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
            invalid_events = [item for item in records if item.get("event") == "db_input_validation_failed"]
            self.assertEqual(len(invalid_events), 1)
            context = invalid_events[0].get("context")
            self.assertIsInstance(context, dict)
            assert isinstance(context, dict)
            self.assertEqual(context.get("method"), "update_schedule")
            self.assertEqual(context.get("code"), "greater_than_equal")
            self.assertEqual(context.get("field"), "duration_minutes")
            self.assertEqual(context.get("message"), "Input should be greater than or equal to 1")
            self.assertIn("duration_minutes", str(context.get("reason")))
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_db_valid_write_does_not_log_input_validation_failure(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("test.db.valid_write")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            logger.handlers.clear()
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = logging.StreamHandler(stream)
            handler.setFormatter(JsonLinesFormatter())
            logger.addHandler(handler)
            db = AssistantDB(self.db_path, logger=logger)

            schedule_id = db.add_schedule("正常日程", "2026-02-20 10:00")
            self.assertGreater(schedule_id, 0)

            records = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
            events = [item.get("event") for item in records]
            self.assertNotIn("db_input_validation_failed", events)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_thought_crud_and_soft_delete(self) -> None:
        thought_id = self.db.add_thought("记得买咖啡豆")
        item = self.db.get_thought(thought_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.content, "记得买咖啡豆")
        self.assertEqual(item.status, "未完成")

        updated = self.db.update_thought(thought_id, content="记得买咖啡豆和滤纸", status="完成")
        self.assertTrue(updated)
        changed = self.db.get_thought(thought_id)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertEqual(changed.content, "记得买咖啡豆和滤纸")
        self.assertEqual(changed.status, "完成")

        deleted = self.db.soft_delete_thought(thought_id)
        self.assertTrue(deleted)
        removed = self.db.get_thought(thought_id)
        self.assertIsNotNone(removed)
        assert removed is not None
        self.assertEqual(removed.status, "删除")

    def test_list_thoughts_default_excludes_deleted(self) -> None:
        a = self.db.add_thought("碎片想法A")
        b = self.db.add_thought("碎片想法B", status="完成")
        c = self.db.add_thought("碎片想法C")
        self.assertEqual([a, b, c], [1, 2, 3])
        self.assertTrue(self.db.soft_delete_thought(c))

        default_items = self.db.list_thoughts()
        self.assertEqual([item.content for item in default_items], ["碎片想法A", "碎片想法B"])
        self.assertTrue(all(item.status in {"未完成", "完成"} for item in default_items))

        deleted_only = self.db.list_thoughts(status="删除")
        self.assertEqual(len(deleted_only), 1)
        self.assertEqual(deleted_only[0].content, "碎片想法C")

    def test_thought_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.db.add_thought("  ")
        with self.assertRaises(ValueError):
            self.db.add_thought("合法内容", status="进行中")
        with self.assertRaises(ValueError):
            self.db.list_thoughts(status="进行中")

        thought_id = self.db.add_thought("只改内容")
        with self.assertRaises(ValueError):
            self.db.update_thought(thought_id, content="")
        with self.assertRaises(ValueError):
            self.db.update_thought(thought_id, content="更新", status="进行中")
        with self.assertRaises(ValueError):
            self.db.update_thought(thought_id, content="更新", status=None)

        self.assertFalse(self.db.update_thought(999, content="不存在"))
        self.assertFalse(self.db.soft_delete_thought(999))

    def test_recent_messages_in_chronological_order(self) -> None:
        self.db.save_message("user", "hello")
        self.db.save_message("assistant", "world")

        messages = self.db.recent_messages(limit=2)
        self.assertEqual(messages[0].content, "hello")
        self.assertEqual(messages[1].content, "world")

    def test_save_turn_persists_user_and_assistant_messages(self) -> None:
        self.db.save_turn(user_content="你好", assistant_content="你好，我可以帮你什么？")

        messages = self.db.recent_messages(limit=2)
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, "user")
        self.assertEqual(messages[0].content, "你好")
        self.assertEqual(messages[1].role, "assistant")
        self.assertEqual(messages[1].content, "你好，我可以帮你什么？")

    def test_recent_turns_returns_paired_user_and_assistant_fields(self) -> None:
        self.db.save_turn(user_content="用户问题", assistant_content="最终回答")

        turns = self.db.recent_turns(limit=1)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].user_content, "用户问题")
        self.assertEqual(turns[0].assistant_content, "最终回答")

    def test_search_turns_matches_user_or_assistant_content(self) -> None:
        self.db.save_turn(user_content="我要买牛奶", assistant_content="已帮你记录买牛奶")
        self.db.save_turn(user_content="今天日程", assistant_content="你今天 10:00 有会议")

        user_hits = self.db.search_turns("牛奶", limit=10)
        self.assertEqual(len(user_hits), 1)
        self.assertEqual(user_hits[0].user_content, "我要买牛奶")

        assistant_hits = self.db.search_turns("10:00", limit=10)
        self.assertEqual(len(assistant_hits), 1)
        self.assertEqual(assistant_hits[0].assistant_content, "你今天 10:00 有会议")

    def test_recent_turns_for_planner_applies_lookback_and_limit(self) -> None:
        self.db.save_turn(user_content="两天前的问题", assistant_content="两天前的回答")
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE chat_history SET created_at = ? WHERE id = 1",
                ((datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),),
            )
            conn.commit()
        finally:
            conn.close()

        for idx in range(2, 8):
            self.db.save_turn(user_content=f"最近问题{idx}", assistant_content=f"最近回答{idx}")

        turns = self.db.recent_turns_for_planner(lookback_hours=24, limit=3)
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[0].user_content, "最近问题5")
        self.assertEqual(turns[-1].assistant_content, "最近回答7")
        self.assertNotIn("两天前的问题", [item.user_content for item in turns])

    def test_recent_turns_since_applies_time_window_and_limit(self) -> None:
        self.db.save_turn(user_content="窗口外", assistant_content="窗口外回答")
        self.db.save_turn(user_content="窗口内1", assistant_content="窗口内回答1")
        self.db.save_turn(user_content="窗口内2", assistant_content="窗口内回答2")

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE chat_history SET created_at = ? WHERE id = 1",
                ("2026-01-01 09:00:00",),
            )
            conn.execute(
                "UPDATE chat_history SET created_at = ? WHERE id = 2",
                ("2026-02-20 10:00:00",),
            )
            conn.execute(
                "UPDATE chat_history SET created_at = ? WHERE id = 3",
                ("2026-02-20 11:00:00",),
            )
            conn.commit()
        finally:
            conn.close()

        turns = self.db.recent_turns_since(since=datetime(2026, 2, 20, 9, 30), limit=1)

        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].user_content, "窗口内2")

    def test_scheduled_planner_tasks_can_be_initialized_and_marked_started(self) -> None:
        task_id = self.db.add_scheduled_planner_task(
            task_name="morning-brief",
            cron_expr="0 9 * * *",
            prompt="生成晨报",
            run_limit=1,
        )

        uninitialized = self.db.list_uninitialized_scheduled_planner_tasks()
        self.assertEqual([item.id for item in uninitialized], [task_id])

        initialized = self.db.initialize_scheduled_planner_task_next_run(
            task_id,
            next_run_at="2026-03-11 09:00:00",
            updated_at="2026-03-11 08:00:00",
        )
        self.assertTrue(initialized)

        due_items = self.db.list_due_scheduled_planner_tasks(now=datetime(2026, 3, 11, 9, 0, 0))
        self.assertEqual([item.id for item in due_items], [task_id])
        self.assertEqual(due_items[0].next_run_at, "2026-03-11 09:00:00")

        marked = self.db.mark_scheduled_planner_task_started(
            task_id,
            expected_next_run_at="2026-03-11 09:00:00",
            started_at="2026-03-11 09:00:05",
            next_run_at="2026-03-12 09:00:00",
        )
        self.assertTrue(marked)

        stored = self.db.list_scheduled_planner_tasks()[0]
        self.assertEqual(stored.last_run_at, "2026-03-11 09:00:05")
        self.assertEqual(stored.next_run_at, "2026-03-12 09:00:00")
        self.assertEqual(stored.run_limit, 0)

    def test_scheduled_planner_task_mark_started_rejects_stale_expected_next_run(self) -> None:
        task_id = self.db.add_scheduled_planner_task(
            task_name="daily-summary",
            cron_expr="0 18 * * *",
            prompt="总结今日任务",
            run_limit=2,
            next_run_at="2026-03-11 18:00:00",
        )

        marked = self.db.mark_scheduled_planner_task_started(
            task_id,
            expected_next_run_at="2026-03-11 17:59:00",
            started_at="2026-03-11 18:00:01",
            next_run_at="2026-03-12 18:00:00",
        )

        self.assertFalse(marked)
        stored = self.db.list_scheduled_planner_tasks()[0]
        self.assertIsNone(stored.last_run_at)
        self.assertEqual(stored.next_run_at, "2026-03-11 18:00:00")
        self.assertEqual(stored.run_limit, 2)

    def test_scheduled_planner_task_run_limit_negative_one_does_not_decrement(self) -> None:
        task_id = self.db.add_scheduled_planner_task(
            task_name="infinite-summary",
            cron_expr="0 18 * * *",
            prompt="总结今日任务",
            run_limit=-1,
            next_run_at="2026-03-11 18:00:00",
        )

        marked = self.db.mark_scheduled_planner_task_started(
            task_id,
            expected_next_run_at="2026-03-11 18:00:00",
            started_at="2026-03-11 18:00:01",
            next_run_at="2026-03-12 18:00:00",
        )

        self.assertTrue(marked)
        stored = self.db.list_scheduled_planner_tasks()[0]
        self.assertEqual(stored.run_limit, -1)
        self.assertEqual(stored.last_run_at, "2026-03-11 18:00:01")

    def test_scheduled_planner_task_run_limit_zero_is_not_initialized_or_due(self) -> None:
        task_id = self.db.add_scheduled_planner_task(
            task_name="disabled-summary",
            cron_expr="0 18 * * *",
            prompt="总结今日任务",
            run_limit=0,
        )

        self.assertEqual(self.db.list_uninitialized_scheduled_planner_tasks(), [])
        self.assertEqual(self.db.list_due_scheduled_planner_tasks(now=datetime(2026, 3, 11, 18, 0, 0)), [])

        initialized = self.db.initialize_scheduled_planner_task_next_run(
            task_id,
            next_run_at="2026-03-12 18:00:00",
        )
        self.assertFalse(initialized)

    def test_get_update_and_delete_scheduled_planner_task(self) -> None:
        task_id = self.db.add_scheduled_planner_task(
            task_name="weekly-report",
            cron_expr="0 9 * * 1",
            prompt="生成周报",
            run_limit=2,
            next_run_at="2026-03-16 09:00:00",
        )

        item = self.db.get_scheduled_planner_task(task_id)
        assert item is not None
        self.assertEqual(item.task_name, "weekly-report")
        self.assertEqual(item.run_limit, 2)

        updated = self.db.update_scheduled_planner_task(
            task_id,
            task_name="weekly-report-v2",
            cron_expr="0 10 * * 1",
            prompt="生成新版周报",
            run_limit=0,
            next_run_at=None,
        )

        self.assertTrue(updated)
        stored = self.db.get_scheduled_planner_task(task_id)
        assert stored is not None
        self.assertEqual(stored.task_name, "weekly-report-v2")
        self.assertEqual(stored.cron_expr, "0 10 * * 1")
        self.assertEqual(stored.prompt, "生成新版周报")
        self.assertEqual(stored.run_limit, 0)
        self.assertIsNone(stored.next_run_at)

        deleted = self.db.delete_scheduled_planner_task(task_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_scheduled_planner_task(task_id))

    def test_update_scheduled_planner_task_rejects_invalid_cron_expr(self) -> None:
        task_id = self.db.add_scheduled_planner_task(
            task_name="daily-report",
            cron_expr="0 9 * * *",
            prompt="生成日报",
            run_limit=1,
            next_run_at="2026-03-11 09:00:00",
        )

        with self.assertRaises(ValueError):
            self.db.update_scheduled_planner_task(
                task_id,
                task_name="daily-report",
                cron_expr="bad cron",
                prompt="生成日报",
                run_limit=1,
                next_run_at="2026-03-12 09:00:00",
            )

    def test_list_scheduled_planner_tasks_tolerates_invalid_existing_cron_expr(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT INTO timer_tasks (
                    task_name, run_limit, cron_expr, prompt, next_run_at, last_run_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-invalid-cron",
                    -1,
                    "bad cron",
                    "旧任务",
                    None,
                    None,
                    "2026-03-10 09:00:00",
                    "2026-03-10 09:00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        tasks = self.db.list_scheduled_planner_tasks()

        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].cron_expr, "bad cron")

    def test_db_initializes_timer_tasks_table(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
        finally:
            conn.close()

        self.assertIn("timer_tasks", tables)
        self.assertNotIn("scheduled_planner_tasks", tables)

    def test_legacy_scheduled_planner_task_enabled_column_is_migrated_to_run_limit(self) -> None:
        legacy_path = Path(self.tmp.name) / "legacy_scheduled_task.db"
        conn = sqlite3.connect(str(legacy_path))
        try:
            conn.execute(
                """
                CREATE TABLE scheduled_planner_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    cron_expr TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    next_run_at TEXT,
                    last_run_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO scheduled_planner_tasks (
                    task_name, enabled, cron_expr, prompt, next_run_at, last_run_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-enabled",
                    1,
                    "0 9 * * *",
                    "生成晨报",
                    "2026-03-11 09:00:00",
                    None,
                    "2026-03-10 09:00:00",
                    "2026-03-10 09:00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO scheduled_planner_tasks (
                    task_name, enabled, cron_expr, prompt, next_run_at, last_run_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-disabled",
                    0,
                    "0 10 * * *",
                    "生成晚报",
                    None,
                    None,
                    "2026-03-10 10:00:00",
                    "2026-03-10 10:00:00",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        migrated_db = AssistantDB(str(legacy_path))
        tasks = migrated_db.list_scheduled_planner_tasks()
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].run_limit, -1)
        self.assertEqual(tasks[1].run_limit, 0)
        conn = sqlite3.connect(str(legacy_path))
        try:
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            }
        finally:
            conn.close()
        self.assertIn("timer_tasks", tables)
        self.assertNotIn("scheduled_planner_tasks", tables)

    def test_chat_history_legacy_schema_is_migrated_to_turn_schema(self) -> None:
        legacy_path = Path(self.tmp.name) / "legacy_chat_history.db"
        conn = sqlite3.connect(str(legacy_path))
        try:
            conn.execute(
                """
                CREATE TABLE chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO chat_history (role, content, created_at) VALUES (?, ?, ?)",
                ("user", "老问题", "2026-02-24 09:00:00"),
            )
            conn.execute(
                "INSERT INTO chat_history (role, content, created_at) VALUES (?, ?, ?)",
                ("assistant", "老回答", "2026-02-24 09:00:01"),
            )
            conn.commit()
        finally:
            conn.close()

        migrated_db = AssistantDB(str(legacy_path))
        turns = migrated_db.recent_turns(limit=5)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0].user_content, "老问题")
        self.assertEqual(turns[0].assistant_content, "老回答")


if __name__ == "__main__":
    unittest.main()
