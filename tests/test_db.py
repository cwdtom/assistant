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
        self.assertFalse(todos[0].done)

    def test_mark_todo_done(self) -> None:
        todo_id = self.db.add_todo("完成第一个版本")
        updated = self.db.mark_todo_done(todo_id)

        self.assertTrue(updated)
        todos = self.db.list_todos()
        self.assertTrue(todos[0].done)

    def test_todo_crud(self) -> None:
        todo_id = self.db.add_todo("写周报", tag="work")

        item = self.db.get_todo(todo_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.content, "写周报")
        self.assertEqual(item.tag, "work")

        updated = self.db.update_todo(todo_id, content="写周报v2", tag="review", done=True)
        self.assertTrue(updated)

        changed = self.db.get_todo(todo_id)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertEqual(changed.content, "写周报v2")
        self.assertEqual(changed.tag, "review")
        self.assertTrue(changed.done)

        deleted = self.db.delete_todo(todo_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_todo(todo_id))

    def test_todo_tag_filter(self) -> None:
        self.db.add_todo("修复 bug", tag="work")
        self.db.add_todo("买牛奶", tag="life")

        work_todos = self.db.list_todos(tag="work")
        self.assertEqual(len(work_todos), 1)
        self.assertEqual(work_todos[0].content, "修复 bug")
        self.assertEqual(work_todos[0].tag, "work")

    def test_schedule_order(self) -> None:
        self.db.add_schedule("晚上的会", "2026-02-20 20:00")
        self.db.add_schedule("早上的会", "2026-02-20 09:00")

        items = self.db.list_schedules()
        self.assertEqual(items[0].title, "早上的会")
        self.assertEqual(items[1].title, "晚上的会")

    def test_schedule_crud(self) -> None:
        schedule_id = self.db.add_schedule("项目同步", "2026-02-20 10:00")

        item = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.title, "项目同步")

        updated = self.db.update_schedule(
            schedule_id,
            title="项目复盘",
            event_time="2026-02-21 11:30",
        )
        self.assertTrue(updated)

        changed = self.db.get_schedule(schedule_id)
        self.assertIsNotNone(changed)
        assert changed is not None
        self.assertEqual(changed.title, "项目复盘")
        self.assertEqual(changed.event_time, "2026-02-21 11:30")

        deleted = self.db.delete_schedule(schedule_id)
        self.assertTrue(deleted)
        self.assertIsNone(self.db.get_schedule(schedule_id))

    def test_recent_messages_in_chronological_order(self) -> None:
        self.db.save_message("user", "hello")
        self.db.save_message("assistant", "world")

        messages = self.db.recent_messages(limit=2)
        self.assertEqual(messages[0].content, "hello")
        self.assertEqual(messages[1].content, "world")


if __name__ == "__main__":
    unittest.main()
