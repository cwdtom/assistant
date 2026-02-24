from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from assistant_app.db import AssistantDB


class AssistantDBTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "assistant_test.db")
        self.db = AssistantDB(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_add_and_list_todos(self) -> None:
        todo_id = self.db.add_todo("写单元测试")
        self.assertEqual(todo_id, 1)

        todos = self.db.list_todos()
        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0].content, "写单元测试")
        self.assertEqual(todos[0].tag, "default")
        self.assertEqual(todos[0].priority, 0)
        self.assertFalse(todos[0].done)
        self.assertIsNone(todos[0].completed_at)
        self.assertIsNone(todos[0].due_at)
        self.assertIsNone(todos[0].remind_at)

    def test_mark_todo_done(self) -> None:
        todo_id = self.db.add_todo("完成第一个版本")
        updated = self.db.mark_todo_done(todo_id)

        self.assertTrue(updated)
        todos = self.db.list_todos()
        self.assertTrue(todos[0].done)
        self.assertIsNotNone(todos[0].completed_at)

    def test_todo_crud(self) -> None:
        todo_id = self.db.add_todo("写周报", tag="work", priority=2)

        item = self.db.get_todo(todo_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.content, "写周报")
        self.assertEqual(item.tag, "work")
        self.assertEqual(item.priority, 2)

        updated = self.db.update_todo(todo_id, content="写周报v2", tag="review", priority=1, done=True)
        self.assertTrue(updated)

        changed = self.db.get_todo(todo_id)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertEqual(changed.content, "写周报v2")
        self.assertEqual(changed.tag, "review")
        self.assertEqual(changed.priority, 1)
        self.assertTrue(changed.done)

        deleted = self.db.delete_todo(todo_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_todo(todo_id))

    def test_todo_due_and_remind(self) -> None:
        todo_id = self.db.add_todo(
            "准备复盘",
            tag="work",
            due_at="2026-02-25 18:00",
            remind_at="2026-02-25 17:00",
        )

        item = self.db.get_todo(todo_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.due_at, "2026-02-25 18:00")
        self.assertEqual(item.remind_at, "2026-02-25 17:00")

        self.assertTrue(
            self.db.update_todo(
                todo_id,
                due_at="2026-02-25 20:00",
                remind_at="2026-02-25 19:00",
            )
        )
        changed = self.db.get_todo(todo_id)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertEqual(changed.due_at, "2026-02-25 20:00")
        self.assertEqual(changed.remind_at, "2026-02-25 19:00")

    def test_remind_requires_due(self) -> None:
        with self.assertRaises(ValueError):
            self.db.add_todo("只设置提醒", remind_at="2026-02-25 09:00")

        todo_id = self.db.add_todo("有截止时间", due_at="2026-02-25 10:00")
        # Try to clear due while keeping remind -> invalid
        ok = self.db.update_todo(todo_id, remind_at="2026-02-25 09:00", due_at=None)
        self.assertFalse(ok)

    def test_todo_tag_filter(self) -> None:
        self.db.add_todo("修复 bug", tag="work")
        self.db.add_todo("买牛奶", tag="life")

        work_todos = self.db.list_todos(tag="work")
        self.assertEqual(len(work_todos), 1)
        self.assertEqual(work_todos[0].content, "修复 bug")
        self.assertEqual(work_todos[0].tag, "work")

    def test_search_todos(self) -> None:
        self.db.add_todo("修复登录 bug", tag="work", priority=1)
        self.db.add_todo("购买牛奶", tag="life", priority=0)
        self.db.add_todo("修复支付 bug", tag="work", priority=0)

        results = self.db.search_todos("bug")
        self.assertEqual([item.content for item in results], ["修复支付 bug", "修复登录 bug"])

    def test_search_todos_with_tag(self) -> None:
        self.db.add_todo("修复登录 bug", tag="work")
        self.db.add_todo("修复支付 bug", tag="work")
        self.db.add_todo("修理台灯", tag="life")

        results = self.db.search_todos("修复", tag="work")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item.tag == "work" for item in results))

    def test_todo_priority_sort(self) -> None:
        self.db.add_todo("低优先级", priority=3)
        self.db.add_todo("最高优先级", priority=0)
        self.db.add_todo("中优先级", priority=1)

        todos = self.db.list_todos()
        self.assertEqual([item.content for item in todos], ["最高优先级", "中优先级", "低优先级"])
        self.assertEqual([item.priority for item in todos], [0, 1, 3])

    def test_todo_priority_must_be_non_negative(self) -> None:
        with self.assertRaises(ValueError):
            self.db.add_todo("非法优先级", priority=-1)

        todo_id = self.db.add_todo("合法优先级", priority=1)
        self.assertFalse(self.db.update_todo(todo_id, priority=-1))

    def test_schedule_order(self) -> None:
        self.db.add_schedule("晚上的会", "2026-02-20 20:00")
        self.db.add_schedule("早上的会", "2026-02-20 09:00")

        items = self.db.list_schedules()
        self.assertEqual(items[0].title, "早上的会")
        self.assertEqual(items[1].title, "晚上的会")
        self.assertEqual(items[0].duration_minutes, 60)
        self.assertEqual(items[1].duration_minutes, 60)

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

    def test_find_schedule_conflicts(self) -> None:
        self.db.add_schedule("晨会", "2026-02-20 09:00", duration_minutes=60)
        second_id = self.db.add_schedule("周会", "2026-02-21 09:00", duration_minutes=60)

        conflicts = self.db.find_schedule_conflicts(
            ["2026-02-20 09:30", "2026-02-21 09:30"],
            duration_minutes=30,
        )
        self.assertEqual([item.title for item in conflicts], ["晨会", "周会"])

        excluded = self.db.find_schedule_conflicts(
            ["2026-02-21 09:30"],
            duration_minutes=30,
            exclude_schedule_id=second_id,
        )
        self.assertEqual(excluded, [])

    def test_find_schedule_conflicts_boundary_non_overlap(self) -> None:
        self.db.add_schedule("晨会", "2026-02-20 09:00", duration_minutes=60)
        conflicts = self.db.find_schedule_conflicts(["2026-02-20 10:00"], duration_minutes=30)
        self.assertEqual(conflicts, [])

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

    def test_find_schedule_conflicts_with_recurring_rules(self) -> None:
        schedule_id = self.db.add_schedule("周会", "2026-02-20 09:00", duration_minutes=60)
        self.assertTrue(
            self.db.set_schedule_recurrence(
                schedule_id,
                start_time="2026-02-20 09:00",
                repeat_interval_minutes=10080,
                repeat_times=4,
            )
        )

        conflicts = self.db.find_schedule_conflicts(["2026-02-27 09:30"], duration_minutes=30)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].event_time, "2026-02-27 09:00")

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
        disabled_conflicts = self.db.find_schedule_conflicts(["2026-02-27 09:30"], duration_minutes=30)
        self.assertEqual(disabled_conflicts, [])

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
            reminder_key="todo:1:2026-02-25 09:00",
            source_type="todo",
            source_id=1,
            occurrence_time=None,
            remind_time="2026-02-25 09:00",
        )
        second = self.db.save_reminder_delivery(
            reminder_key="todo:1:2026-02-25 09:00",
            source_type="todo",
            source_id=1,
            occurrence_time=None,
            remind_time="2026-02-25 09:00",
        )

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertTrue(self.db.has_reminder_delivery("todo:1:2026-02-25 09:00"))
        deliveries = self.db.list_reminder_deliveries()
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].source_type, "todo")

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

    def test_recent_messages_in_chronological_order(self) -> None:
        self.db.save_message("user", "hello")
        self.db.save_message("assistant", "world")

        messages = self.db.recent_messages(limit=2)
        self.assertEqual(messages[0].content, "hello")
        self.assertEqual(messages[1].content, "world")


if __name__ == "__main__":
    unittest.main()
