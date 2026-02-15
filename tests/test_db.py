from __future__ import annotations

import tempfile
import unittest
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
        self.db.add_schedule("晨会", "2026-02-20 09:00")
        second_id = self.db.add_schedule("周会", "2026-02-21 09:00")

        conflicts = self.db.find_schedule_conflicts(["2026-02-20 09:00", "2026-02-21 09:00"])
        self.assertEqual([item.title for item in conflicts], ["晨会", "周会"])

        excluded = self.db.find_schedule_conflicts(["2026-02-21 09:00"], exclude_schedule_id=second_id)
        self.assertEqual(excluded, [])

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
