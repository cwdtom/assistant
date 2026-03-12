from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from assistant_app.agent import AssistantAgent
from assistant_app.agent_components.tools.timer import execute_timer_system_action
from assistant_app.db import AssistantDB
from assistant_app.schemas.routing import RuntimePlannerActionPayload
from assistant_app.schemas.tools import TimerUpdateArgs


class TimerToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = AssistantDB(str(Path(self.tmp.name) / "assistant_test.db"))
        for task in self.db.list_scheduled_planner_tasks():
            self.db.delete_scheduled_planner_task(task.id)
        self.agent = AssistantAgent(db=self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_timer_tool_supports_crud_actions(self) -> None:
        add_observation = execute_timer_system_action(
            self.agent,
            {
                "action": "add",
                "task_name": "daily-report",
                "cron_expr": "0 9 * * *",
                "prompt": "生成日报",
                "run_limit": 3,
            },
            raw_input=json.dumps(
                {
                    "action": "add",
                    "task_name": "daily-report",
                    "cron_expr": "0 9 * * *",
                    "prompt": "生成日报",
                    "run_limit": 3,
                },
                ensure_ascii=False,
            ),
            clock=lambda: datetime(2026, 3, 11, 8, 0, 0),
        )

        self.assertTrue(add_observation.ok)
        stored = next(
            (task for task in self.db.list_scheduled_planner_tasks() if task.task_name == "daily-report"),
            None,
        )
        assert stored is not None
        task_id = stored.id
        self.assertIn(f"已创建定时任务 #{task_id}", add_observation.result)
        self.assertEqual(stored.next_run_at, "2026-03-11 09:00:00")

        list_observation = self.agent._execute_planner_tool(
            action_tool="timer",
            action_input='{"action":"list"}',
        )
        self.assertTrue(list_observation.ok)
        self.assertIn("定时任务列表", list_observation.result)
        self.assertIn("daily-report", list_observation.result)

        get_observation = self.agent._execute_planner_tool(
            action_tool="timer",
            action_input=json.dumps({"action": "get", "id": task_id}, ensure_ascii=False),
        )
        self.assertTrue(get_observation.ok)
        self.assertIn("定时任务详情", get_observation.result)
        self.assertIn("生成日报", get_observation.result)

        update_observation = self.agent._execute_planner_tool(
            action_tool="timer",
            action_input="not-json",
            action_payload=RuntimePlannerActionPayload(
                tool_name="timer_update",
                arguments=TimerUpdateArgs(id=task_id, prompt="生成新版日报"),
            ),
        )
        self.assertTrue(update_observation.ok)
        self.assertIn(f"已更新定时任务 #{task_id}", update_observation.result)
        updated = self.db.get_scheduled_planner_task(task_id)
        assert updated is not None
        self.assertEqual(updated.prompt, "生成新版日报")
        self.assertEqual(updated.next_run_at, "2026-03-11 09:00:00")

        delete_observation = self.agent._execute_planner_tool(
            action_tool="timer",
            action_input=json.dumps({"action": "delete", "id": task_id}, ensure_ascii=False),
        )
        self.assertTrue(delete_observation.ok)
        self.assertEqual(delete_observation.result, f"定时任务 #{task_id} 已删除。")
        self.assertIsNone(self.db.get_scheduled_planner_task(task_id))

    def test_timer_update_recomputes_next_run_at_when_reenabled(self) -> None:
        task_id = self.db.add_scheduled_planner_task(
            task_name="disabled-report",
            cron_expr="0 9 * * *",
            prompt="生成日报",
            run_limit=0,
            next_run_at=None,
        )

        observation = execute_timer_system_action(
            self.agent,
            {"action": "update", "id": task_id, "run_limit": 1},
            raw_input='{"action":"update","id":1,"run_limit":1}',
            clock=lambda: datetime(2026, 3, 11, 8, 0, 0),
        )

        self.assertTrue(observation.ok)
        stored = self.db.get_scheduled_planner_task(task_id)
        assert stored is not None
        self.assertEqual(stored.run_limit, 1)
        self.assertEqual(stored.next_run_at, "2026-03-11 09:00:00")

    def test_timer_update_rejects_explicit_null_run_limit(self) -> None:
        task_id = self.db.add_scheduled_planner_task(
            task_name="daily-report",
            cron_expr="0 9 * * *",
            prompt="生成日报",
            run_limit=1,
            next_run_at="2026-03-11 09:00:00",
        )

        observation = execute_timer_system_action(
            self.agent,
            {"action": "update", "id": task_id, "run_limit": None},
            raw_input='{"action":"update","id":1,"run_limit":null}',
            clock=lambda: datetime(2026, 3, 11, 8, 0, 0),
        )

        self.assertFalse(observation.ok)
        self.assertEqual(observation.result, "timer.update run_limit 必须为 -1 或 >= 0。")

    def test_timer_tool_logs_done_and_failed_events(self) -> None:
        with self.assertLogs("assistant_app.app", level="INFO") as captured_done:
            invalid = self.agent._execute_planner_tool(
                action_tool="timer",
                action_input='{"action":"add","task_name":"daily-report","cron_expr":"bad cron","prompt":"生成日报"}',
            )
        self.assertFalse(invalid.ok)
        self.assertIn("cron_expr 必须为合法 cron 表达式", invalid.result)
        merged_done = "\n".join(captured_done.output)
        self.assertIn("planner_tool_timer_start", merged_done)
        self.assertIn("planner_tool_timer_done", merged_done)

        with patch.object(self.agent.db, "add_scheduled_planner_task", side_effect=RuntimeError("boom")):
            with self.assertLogs("assistant_app.app", level="INFO") as captured_failed:
                failed = execute_timer_system_action(
                    self.agent,
                    {
                        "action": "add",
                        "task_name": "daily-report",
                        "cron_expr": "0 9 * * *",
                        "prompt": "生成日报",
                    },
                    raw_input='{"action":"add","task_name":"daily-report","cron_expr":"0 9 * * *","prompt":"生成日报"}',
                    clock=lambda: datetime(2026, 3, 11, 8, 0, 0),
                )
        self.assertFalse(failed.ok)
        self.assertIn("timer 工具执行失败", failed.result)
        merged_failed = "\n".join(captured_failed.output)
        self.assertIn("planner_tool_timer_start", merged_failed)
        self.assertIn("planner_tool_timer_failed", merged_failed)

    def test_timer_tool_rejects_duplicate_task_name(self) -> None:
        self.db.add_scheduled_planner_task(
            task_name="daily-report",
            cron_expr="0 9 * * *",
            prompt="生成日报",
            run_limit=1,
            next_run_at="2026-03-11 09:00:00",
        )

        observation = execute_timer_system_action(
            self.agent,
            {
                "action": "add",
                "task_name": "daily-report",
                "cron_expr": "0 10 * * *",
                "prompt": "生成晚报",
            },
            raw_input='{"action":"add","task_name":"daily-report","cron_expr":"0 10 * * *","prompt":"生成晚报"}',
            clock=lambda: datetime(2026, 3, 11, 8, 0, 0),
        )

        self.assertFalse(observation.ok)
        self.assertEqual(observation.result, "定时任务名称已存在: daily-report")


if __name__ == "__main__":
    unittest.main()
