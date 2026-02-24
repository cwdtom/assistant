from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from assistant_app.db import AssistantDB
from assistant_app.reminder_service import ReminderService
from assistant_app.reminder_sink import ReminderEvent


class _FakeSink:
    def __init__(self, raise_for_key: str | None = None) -> None:
        self.raise_for_key = raise_for_key
        self.events: list[ReminderEvent] = []

    def emit(self, event: ReminderEvent) -> None:
        if self.raise_for_key and event.reminder_key == self.raise_for_key:
            raise RuntimeError("sink failed")
        self.events.append(event)


class ReminderServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        db_path = str(Path(self.tmp.name) / "assistant_test.db")
        self.db = AssistantDB(db_path)
        self.fixed_now = datetime(2026, 2, 24, 10, 0, 0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_poll_once_delivers_todo_and_schedule_reminders_once(self) -> None:
        todo_id = self.db.add_todo(
            "准备发布",
            due_at="2026-02-24 18:00",
            remind_at="2026-02-24 10:00",
        )
        schedule_id = self.db.add_schedule(
            "项目同步",
            "2026-02-24 11:00",
            remind_at="2026-02-24 10:00",
        )
        sink = _FakeSink()
        service = ReminderService(
            db=self.db,
            sink=sink,
            clock=lambda: self.fixed_now,
            lookahead_seconds=0,
            batch_limit=20,
        )

        first = service.poll_once()
        second = service.poll_once()

        self.assertEqual(first.candidate_count, 2)
        self.assertEqual(first.delivered_count, 2)
        self.assertEqual(first.failed_count, 0)
        self.assertEqual(second.candidate_count, 2)
        self.assertEqual(second.delivered_count, 0)
        self.assertEqual(second.skipped_count, 2)
        self.assertEqual(len(sink.events), 2)
        self.assertTrue(any(event.source_type == "todo" and event.source_id == todo_id for event in sink.events))
        self.assertTrue(
            any(event.source_type == "schedule" and event.source_id == schedule_id for event in sink.events)
        )

    def test_poll_once_rewrites_reminder_content_before_emit(self) -> None:
        self.db.add_todo(
            "准备发布",
            due_at="2026-02-24 18:00",
            remind_at="2026-02-24 10:00",
        )
        sink = _FakeSink()
        service = ReminderService(
            db=self.db,
            sink=sink,
            clock=lambda: self.fixed_now,
            lookahead_seconds=0,
            content_rewriter=lambda text: f"【提醒管家】{text}",
        )

        stats = service.poll_once()

        self.assertEqual(stats.candidate_count, 1)
        self.assertEqual(stats.delivered_count, 1)
        self.assertEqual(len(sink.events), 1)
        self.assertTrue(sink.events[0].content.startswith("【提醒管家】待办提醒 #1"))

    def test_poll_once_rewrite_failure_falls_back_to_original_content(self) -> None:
        self.db.add_todo(
            "准备发布",
            due_at="2026-02-24 18:00",
            remind_at="2026-02-24 10:00",
        )
        sink = _FakeSink()

        def _rewrite(_: str) -> str:
            raise RuntimeError("rewrite failed")

        service = ReminderService(
            db=self.db,
            sink=sink,
            clock=lambda: self.fixed_now,
            lookahead_seconds=0,
            content_rewriter=_rewrite,
        )

        stats = service.poll_once()

        self.assertEqual(stats.candidate_count, 1)
        self.assertEqual(stats.delivered_count, 1)
        self.assertEqual(len(sink.events), 1)
        self.assertEqual(sink.events[0].content, "待办提醒 #1: 准备发布（提醒时间 2026-02-24 10:00）")

    def test_poll_once_skips_done_todo(self) -> None:
        todo_id = self.db.add_todo(
            "已完成事项",
            due_at="2026-02-24 18:00",
            remind_at="2026-02-24 10:00",
        )
        self.db.mark_todo_done(todo_id)
        sink = _FakeSink()
        service = ReminderService(
            db=self.db,
            sink=sink,
            clock=lambda: self.fixed_now,
            lookahead_seconds=0,
        )

        stats = service.poll_once()
        self.assertEqual(stats.candidate_count, 0)
        self.assertEqual(stats.delivered_count, 0)
        self.assertEqual(len(sink.events), 0)

    def test_poll_once_sink_failure_does_not_mark_delivery(self) -> None:
        self.db.add_todo(
            "需要失败重试",
            due_at="2026-02-24 18:00",
            remind_at="2026-02-24 10:00",
        )
        reminder_key = "todo:1:2026-02-24 10:00"
        sink = _FakeSink(raise_for_key=reminder_key)
        service = ReminderService(
            db=self.db,
            sink=sink,
            clock=lambda: self.fixed_now,
            lookahead_seconds=0,
        )

        stats = service.poll_once()
        self.assertEqual(stats.candidate_count, 1)
        self.assertEqual(stats.delivered_count, 0)
        self.assertEqual(stats.failed_count, 1)
        self.assertFalse(self.db.has_reminder_delivery(reminder_key))

    def test_poll_once_delivers_recurring_schedule_with_remind_start(self) -> None:
        schedule_id = self.db.add_schedule(
            "晨会",
            "2026-02-24 10:00",
            remind_at="2026-02-24 09:40",
        )
        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-24 10:00",
                repeat_interval_minutes=1440,
                repeat_times=3,
                remind_start_time="2026-02-24 09:30",
            )
        )
        sink = _FakeSink()
        service = ReminderService(
            db=self.db,
            sink=sink,
            clock=lambda: datetime(2026, 2, 25, 9, 30, 0),
            lookahead_seconds=0,
        )

        stats = service.poll_once()

        self.assertEqual(stats.candidate_count, 1)
        self.assertEqual(stats.delivered_count, 1)
        self.assertEqual(len(sink.events), 1)
        self.assertEqual(
            sink.events[0].reminder_key,
            "schedule:1:2026-02-25 10:00:2026-02-25 09:30",
        )
        self.assertEqual(sink.events[0].occurrence_time, "2026-02-25 10:00")

    def test_poll_once_recurring_schedule_falls_back_to_base_remind_delta(self) -> None:
        schedule_id = self.db.add_schedule(
            "晚会",
            "2026-02-24 10:00",
            remind_at="2026-02-24 09:40",
        )
        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-24 10:00",
                repeat_interval_minutes=1440,
                repeat_times=3,
            )
        )
        sink = _FakeSink()
        service = ReminderService(
            db=self.db,
            sink=sink,
            clock=lambda: datetime(2026, 2, 25, 9, 40, 0),
            lookahead_seconds=0,
        )

        stats = service.poll_once()

        self.assertEqual(stats.candidate_count, 1)
        self.assertEqual(stats.delivered_count, 1)
        self.assertEqual(len(sink.events), 1)
        self.assertEqual(
            sink.events[0].reminder_key,
            "schedule:1:2026-02-25 10:00:2026-02-25 09:40",
        )
        self.assertEqual(sink.events[0].occurrence_time, "2026-02-25 10:00")


if __name__ == "__main__":
    unittest.main()
