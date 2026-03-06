from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from assistant_app.agent_components.command_handlers import handle_command
from assistant_app.agent_components.tools.schedule import execute_schedule_system_action
from assistant_app.db import AssistantDB, ScheduleItem


class _AgentStub:
    def __init__(self, db: AssistantDB) -> None:
        self.db = db
        self._schedule_max_window_days = 31
        self.added: list[int] = []
        self.updated: list[tuple[int, ScheduleItem | None]] = []
        self.deleted: list[tuple[int, ScheduleItem | None]] = []

    def notify_schedule_added(self, schedule_id: int) -> None:
        self.added.append(schedule_id)

    def notify_schedule_updated(self, schedule_id: int, old_schedule: ScheduleItem | None = None) -> None:
        self.updated.append((schedule_id, old_schedule))

    def notify_schedule_deleted(self, schedule_id: int, deleted_schedule: ScheduleItem | None = None) -> None:
        self.deleted.append((schedule_id, deleted_schedule))


class ScheduleSyncHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = AssistantDB(str(Path(self.tmp.name) / "assistant.db"))
        self.agent = _AgentStub(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_command_path_triggers_sync_notifications(self) -> None:
        add_result = handle_command(self.agent, "/schedule add 2026-03-05 10:00 项目同步")
        self.assertIn("已添加日程 #", add_result)
        self.assertEqual(len(self.agent.added), 1)
        added_id = self.agent.added[0]

        update_result = handle_command(self.agent, f"/schedule update {added_id} 2026-03-05 11:00 项目复盘")
        self.assertIn("已更新日程 #", update_result)
        self.assertEqual(len(self.agent.updated), 1)
        update_schedule_id, old_schedule = self.agent.updated[0]
        self.assertEqual(update_schedule_id, added_id)
        self.assertIsNotNone(old_schedule)
        assert old_schedule is not None
        self.assertEqual(old_schedule.title, "项目同步")

        delete_result = handle_command(self.agent, f"/schedule delete {added_id}")
        self.assertEqual(delete_result, f"日程 #{added_id} 已删除。")
        self.assertEqual(len(self.agent.deleted), 1)
        delete_schedule_id, deleted_schedule = self.agent.deleted[0]
        self.assertEqual(delete_schedule_id, added_id)
        self.assertIsNotNone(deleted_schedule)
        assert deleted_schedule is not None
        self.assertEqual(deleted_schedule.title, "项目复盘")

    def test_planner_tool_path_triggers_sync_notifications(self) -> None:
        add_obs = execute_schedule_system_action(
            self.agent,
            payload={
                "action": "add",
                "event_time": "2026-03-05 10:00",
                "title": "工具新增",
            },
            raw_input='{"action":"add"}',
        )
        self.assertTrue(add_obs.ok)
        self.assertEqual(len(self.agent.added), 1)
        added_id = self.agent.added[0]

        update_obs = execute_schedule_system_action(
            self.agent,
            payload={
                "action": "update",
                "id": added_id,
                "event_time": "2026-03-05 12:00",
                "title": "工具更新",
            },
            raw_input='{"action":"update"}',
        )
        self.assertTrue(update_obs.ok)
        self.assertEqual(len(self.agent.updated), 1)
        update_schedule_id, old_schedule = self.agent.updated[0]
        self.assertEqual(update_schedule_id, added_id)
        self.assertIsNotNone(old_schedule)
        assert old_schedule is not None
        self.assertEqual(old_schedule.title, "工具新增")

        delete_obs = execute_schedule_system_action(
            self.agent,
            payload={"action": "delete", "id": added_id},
            raw_input='{"action":"delete"}',
        )
        self.assertTrue(delete_obs.ok)
        self.assertEqual(len(self.agent.deleted), 1)
        delete_schedule_id, deleted_schedule = self.agent.deleted[0]
        self.assertEqual(delete_schedule_id, added_id)
        self.assertIsNotNone(deleted_schedule)
        assert deleted_schedule is not None
        self.assertEqual(deleted_schedule.title, "工具更新")


if __name__ == "__main__":
    unittest.main()
