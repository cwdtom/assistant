from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from assistant_app.db import AssistantDB
from assistant_app.scheduled_planner_task_service import ScheduledPlannerTaskService


class _MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class _FakeAgent:
    def __init__(
        self,
        *,
        response: tuple[str, bool] = ("任务已完成", True),
        raises: Exception | None = None,
        user_profile: str | None = None,
        plan_step_trace: dict[str, object] | None = None,
    ) -> None:
        self._response = response
        self._raises = raises
        self._user_profile = user_profile
        self._plan_step_trace = plan_step_trace
        self.calls: list[tuple[str, str]] = []

    def handle_input_with_task_status(self, user_input: str, *, source: str = "interactive"):  # type: ignore[no-untyped-def]
        self.calls.append((user_input, source))
        if self._raises is not None:
            raise self._raises
        return self._response

    def get_user_profile_snapshot(self) -> str | None:
        return self._user_profile

    def get_recent_plan_step_trace(self, *, source: str = "interactive") -> dict[str, object] | None:  # type: ignore[no-untyped-def]
        if source != "scheduled":
            return None
        return self._plan_step_trace


class _BlockingFakeAgent(_FakeAgent):
    def __init__(self, response: tuple[str, bool]) -> None:
        super().__init__(response=response)
        self.first_entered = threading.Event()
        self.release_first = threading.Event()

    def handle_input_with_task_status(self, user_input: str, *, source: str = "interactive"):  # type: ignore[no-untyped-def]
        self.calls.append((user_input, source))
        if len(self.calls) == 1:
            self.first_entered.set()
            self.release_first.wait(timeout=2.0)
        return self._response


class _FakeLLM:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, object]] = []

    def reply_with_tools(self, messages, *, tools, tool_choice="auto"):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        if not self._payloads:
            raise RuntimeError("no payload")
        return self._payloads.pop(0)


class _SingleNextCronIterator:
    def __init__(self, next_time: datetime) -> None:
        self._next_time = next_time

    def get_next(self, _ret_type):  # type: ignore[no-untyped-def]
        return self._next_time


class _CronFactory:
    def __init__(self, mapping: dict[str, list[datetime]]) -> None:
        self._mapping = {key: list(values) for key, values in mapping.items()}
        self.calls: list[tuple[str, datetime]] = []

    def __call__(self, expr: str, now: datetime) -> _SingleNextCronIterator:
        self.calls.append((expr, now))
        values = self._mapping.get(expr, [])
        if not values:
            raise RuntimeError(f"unexpected cron expr: {expr}")
        return _SingleNextCronIterator(values.pop(0))


def _done_payload(*, should_send: bool) -> dict[str, object]:
    return {
        "assistant_message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_done",
                    "type": "function",
                    "function": {
                        "name": "done",
                        "arguments": json.dumps(
                            {
                                "should_send": should_send,
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }
    }


class ScheduledPlannerTaskServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = AssistantDB(str(Path(self.tmp.name) / "assistant_test.db"))
        for task in self.db.list_scheduled_planner_tasks():
            self.db.delete_scheduled_planner_task(task.id)
        self.clock = _MutableClock(datetime(2026, 3, 11, 10, 0, 0))
        self.sent: list[tuple[str, str]] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _wait_until(predicate, timeout: float = 2.0) -> bool:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def test_poll_scheduled_initializes_missing_next_run_without_executing(self) -> None:
        self.db.add_scheduled_planner_task(
            task_name="daily-report",
            cron_expr="0 9 * * *",
            prompt="生成日报",
            run_limit=-1,
        )
        service = ScheduledPlannerTaskService(
            db=self.db,
            agent=_FakeAgent(),
            llm_client=_FakeLLM([]),
            logger=logging.getLogger("test.scheduled_task_service.init"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            clock=self.clock,
            croniter_factory=_CronFactory({"0 9 * * *": [datetime(2026, 3, 12, 9, 0, 0)]}),
        )

        service.poll_scheduled()

        stored = self.db.list_scheduled_planner_tasks()[0]
        self.assertEqual(stored.next_run_at, "2026-03-12 09:00:00")
        self.assertIsNone(stored.last_run_at)
        self.assertEqual(stored.run_limit, -1)
        self.assertEqual(self.sent, [])
        service.stop()

    def test_poll_scheduled_executes_due_task_and_sends_decided_result(self) -> None:
        self.db.add_scheduled_planner_task(
            task_name="daily-report",
            cron_expr="0 9 * * *",
            prompt="生成日报",
            run_limit=1,
            next_run_at="2026-03-11 09:59:00",
        )
        agent = _FakeAgent(response=("日报已生成", True))
        service = ScheduledPlannerTaskService(
            db=self.db,
            agent=agent,
            llm_client=_FakeLLM([_done_payload(should_send=True)]),
            logger=logging.getLogger("test.scheduled_task_service.send"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            clock=self.clock,
            croniter_factory=_CronFactory({"0 9 * * *": [datetime(2026, 3, 12, 9, 0, 0)]}),
        )

        service.poll_scheduled()
        self.assertTrue(self._wait_until(lambda: len(agent.calls) == 1 and len(self.sent) == 1))

        self.assertEqual(agent.calls, [("生成日报", "scheduled")])
        self.assertEqual(self.sent, [("ou_target", "日报已生成")])
        stored = self.db.list_scheduled_planner_tasks()[0]
        self.assertEqual(stored.last_run_at, "2026-03-11 10:00:00")
        self.assertEqual(stored.next_run_at, "2026-03-12 09:00:00")
        self.assertEqual(stored.run_limit, 0)

        service.poll_scheduled()
        time.sleep(0.05)
        self.assertEqual(len(agent.calls), 1)
        service.stop()

    def test_poll_scheduled_passes_profile_history_and_plan_trace_to_decision_context(self) -> None:
        self.db.save_turn(user_content="昨天日报有遗漏", assistant_content="收到，我会补全。")
        self.db.save_turn(user_content="今天生成日报", assistant_content="好的，正在处理。")
        self.db.add_scheduled_planner_task(
            task_name="daily-report-context",
            cron_expr="0 9 * * *",
            prompt="生成日报并判断是否发送",
            run_limit=1,
            next_run_at="2026-03-11 09:58:00",
        )
        llm = _FakeLLM([_done_payload(should_send=False)])
        agent = _FakeAgent(
            response=("日报已生成", True),
            user_profile="偏好：先结论后细节",
            plan_step_trace={
                "goal": "生成日报并发送",
                "step_count": 3,
                "latest_plan": [
                    {"task": "收集数据", "completed": True, "tools": ["history"]},
                    {"task": "整理输出", "completed": False, "tools": ["system"]},
                ],
                "completed_subtasks": [{"item": "收集数据", "result": "已汇总完成"}],
                "observations": [
                    {"tool": "history", "input": "list", "ok": True, "result": "ok"},
                ],
            },
        )
        service = ScheduledPlannerTaskService(
            db=self.db,
            agent=agent,
            llm_client=llm,
            logger=logging.getLogger("test.scheduled_task_service.context"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            clock=self.clock,
            croniter_factory=_CronFactory({"0 9 * * *": [datetime(2026, 3, 12, 9, 0, 0)]}),
        )

        service.poll_scheduled()
        self.assertTrue(self._wait_until(lambda: len(agent.calls) == 1 and len(llm.calls) == 1))

        first_call = llm.calls[0]
        messages = first_call["messages"]
        prompt_payload = json.loads(str(messages[1]["content"]))
        self.assertEqual(prompt_payload["user_profile"], "偏好：先结论后细节")
        self.assertEqual(len(prompt_payload["chat_history"]), 2)
        self.assertEqual(prompt_payload["chat_history"][-1]["assistant_content"], "好的，正在处理。")
        self.assertEqual(prompt_payload["plan_step_trace"]["goal"], "生成日报并发送")
        self.assertEqual(prompt_payload["plan_step_trace"]["step_count"], 3)
        self.assertEqual(self.sent, [])
        service.stop()

    def test_poll_scheduled_skips_send_when_decision_declines(self) -> None:
        self.db.add_scheduled_planner_task(
            task_name="declined-report",
            cron_expr="*/5 * * * *",
            prompt="生成简报",
            run_limit=-1,
            next_run_at="2026-03-11 09:55:00",
        )
        agent = _FakeAgent(response=("简报已生成", True))
        service = ScheduledPlannerTaskService(
            db=self.db,
            agent=agent,
            llm_client=_FakeLLM([_done_payload(should_send=False)]),
            logger=logging.getLogger("test.scheduled_task_service.skip"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            clock=self.clock,
            croniter_factory=_CronFactory(
                {
                    "*/5 * * * *": [datetime(2026, 3, 11, 10, 5, 0)],
                }
            ),
        )

        service.poll_scheduled()
        self.assertTrue(self._wait_until(lambda: len(agent.calls) == 1))
        self.assertEqual(self.sent, [])
        service.stop()

    def test_poll_scheduled_skips_send_when_task_execution_fails(self) -> None:
        self.db.add_scheduled_planner_task(
            task_name="failed-report",
            cron_expr="10 * * * *",
            prompt="生成失败任务",
            run_limit=2,
            next_run_at="2026-03-11 09:50:00",
        )
        agent = _FakeAgent(raises=RuntimeError("boom"))
        service = ScheduledPlannerTaskService(
            db=self.db,
            agent=agent,
            llm_client=_FakeLLM([_done_payload(should_send=True)]),
            logger=logging.getLogger("test.scheduled_task_service.fail"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            clock=self.clock,
            croniter_factory=_CronFactory({"10 * * * *": [datetime(2026, 3, 11, 10, 10, 0)]}),
        )

        service.poll_scheduled()
        self.assertTrue(self._wait_until(lambda: len(agent.calls) == 1))
        self.assertEqual(self.sent, [])
        stored = self.db.list_scheduled_planner_tasks()[0]
        self.assertEqual(stored.run_limit, 1)
        service.stop()

    def test_poll_scheduled_processes_queue_serially(self) -> None:
        self.db.add_scheduled_planner_task(
            task_name="first-task",
            cron_expr="1 * * * *",
            prompt="任务一",
            run_limit=1,
            next_run_at="2026-03-11 09:58:00",
        )
        self.db.add_scheduled_planner_task(
            task_name="second-task",
            cron_expr="2 * * * *",
            prompt="任务二",
            run_limit=1,
            next_run_at="2026-03-11 09:59:00",
        )
        agent = _BlockingFakeAgent(response=("完成", True))
        cron_factory = _CronFactory(
            {
                "1 * * * *": [datetime(2026, 3, 11, 11, 1, 0)],
                "2 * * * *": [datetime(2026, 3, 11, 11, 2, 0)],
            }
        )
        service = ScheduledPlannerTaskService(
            db=self.db,
            agent=agent,
            llm_client=_FakeLLM(
                [
                    _done_payload(should_send=False),
                    _done_payload(should_send=False),
                ]
            ),
            logger=logging.getLogger("test.scheduled_task_service.queue"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            clock=self.clock,
            croniter_factory=cron_factory,
        )

        service.poll_scheduled()
        self.assertTrue(agent.first_entered.wait(timeout=1.0))
        self.assertEqual(agent.calls, [("任务一", "scheduled")])
        stored = self.db.list_scheduled_planner_tasks()
        self.assertEqual(stored[0].run_limit, 0)
        self.assertEqual(stored[0].last_run_at, "2026-03-11 10:00:00")
        self.assertEqual(stored[1].run_limit, 1)
        self.assertIsNone(stored[1].last_run_at)

        service.poll_scheduled()
        self.clock.now = datetime(2026, 3, 11, 10, 2, 0)
        agent.release_first.set()
        self.assertTrue(self._wait_until(lambda: len(agent.calls) == 2))
        self.assertEqual(agent.calls[1], ("任务二", "scheduled"))
        self.assertTrue(self._wait_until(lambda: len(self.db.list_scheduled_planner_tasks()) == 2))
        time.sleep(0.05)
        self.assertEqual(len(agent.calls), 2)
        stored = self.db.list_scheduled_planner_tasks()
        self.assertEqual(stored[1].run_limit, 0)
        self.assertEqual(stored[1].last_run_at, "2026-03-11 10:02:00")
        self.assertEqual(
            cron_factory.calls,
            [
                ("1 * * * *", datetime(2026, 3, 11, 10, 0, 0)),
                ("2 * * * *", datetime(2026, 3, 11, 10, 2, 0)),
            ],
        )
        service.stop()

    def test_poll_scheduled_skips_tasks_with_zero_run_limit(self) -> None:
        self.db.add_scheduled_planner_task(
            task_name="zero-task",
            cron_expr="*/5 * * * *",
            prompt="不会执行",
            run_limit=0,
            next_run_at="2026-03-11 09:55:00",
        )
        agent = _FakeAgent(response=("不应执行", True))
        service = ScheduledPlannerTaskService(
            db=self.db,
            agent=agent,
            llm_client=_FakeLLM([]),
            logger=logging.getLogger("test.scheduled_task_service.zero_limit"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            clock=self.clock,
            croniter_factory=_CronFactory({}),
        )

        service.poll_scheduled()
        time.sleep(0.05)
        self.assertEqual(agent.calls, [])
        stored = self.db.list_scheduled_planner_tasks()[0]
        self.assertEqual(stored.run_limit, 0)
        service.stop()

    def test_poll_scheduled_keeps_negative_one_run_limit(self) -> None:
        self.db.add_scheduled_planner_task(
            task_name="infinite-task",
            cron_expr="*/5 * * * *",
            prompt="一直执行",
            run_limit=-1,
            next_run_at="2026-03-11 09:55:00",
        )
        agent = _FakeAgent(response=("已完成", True))
        service = ScheduledPlannerTaskService(
            db=self.db,
            agent=agent,
            llm_client=_FakeLLM([_done_payload(should_send=False)]),
            logger=logging.getLogger("test.scheduled_task_service.infinite"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            clock=self.clock,
            croniter_factory=_CronFactory({"*/5 * * * *": [datetime(2026, 3, 11, 10, 5, 0)]}),
        )

        service.poll_scheduled()
        self.assertTrue(self._wait_until(lambda: len(agent.calls) == 1))
        stored = self.db.list_scheduled_planner_tasks()[0]
        self.assertEqual(stored.run_limit, -1)
        service.stop()


if __name__ == "__main__":
    unittest.main()
