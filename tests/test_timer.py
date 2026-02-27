from __future__ import annotations

import threading
import time
import unittest

from assistant_app.timer import TimerEngine


class _FakeReminderService:
    def __init__(self, raises: bool = False, poll_event: threading.Event | None = None) -> None:
        self.raises = raises
        self.poll_event = poll_event
        self.poll_count = 0

    def poll_once(self):  # type: ignore[no-untyped-def]
        self.poll_count += 1
        if self.poll_event is not None:
            self.poll_event.set()
        if self.raises:
            raise RuntimeError("boom")

        class _Stats:
            candidate_count = 0
            delivered_count = 0
            skipped_count = 0
            failed_count = 0

        return _Stats()


class _BlockingReminderService:
    def __init__(self) -> None:
        self.poll_count = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def poll_once(self):  # type: ignore[no-untyped-def]
        self.poll_count += 1
        self.entered.set()
        self.release.wait(timeout=2.0)

        class _Stats:
            candidate_count = 0
            delivered_count = 0
            skipped_count = 0
            failed_count = 0

        return _Stats()


class _FakePeriodicTask:
    def __init__(self, raises: bool = False) -> None:
        self.raises = raises
        self.call_count = 0

    def __call__(self) -> None:
        self.call_count += 1
        if self.raises:
            raise RuntimeError("periodic task failed")


class TimerEngineTest(unittest.TestCase):
    @staticmethod
    def _wait_until(predicate, timeout: float = 2.0) -> bool:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())

    def test_tick_once_calls_service(self) -> None:
        service = _FakeReminderService()
        engine = TimerEngine(reminder_service=service, poll_interval_seconds=1)

        engine.tick_once()

        self.assertEqual(service.poll_count, 1)

    def test_start_and_stop_loop(self) -> None:
        first_poll = threading.Event()
        service = _FakeReminderService(poll_event=first_poll)
        engine = TimerEngine(reminder_service=service, poll_interval_seconds=1)

        engine.start()
        self.assertTrue(first_poll.wait(timeout=2.0))
        engine.stop(join_timeout=1.0)

        self.assertGreaterEqual(service.poll_count, 1)
        self.assertFalse(engine.running)

    def test_loop_survives_poll_exception(self) -> None:
        first_poll = threading.Event()
        service = _FakeReminderService(raises=True, poll_event=first_poll)
        engine = TimerEngine(reminder_service=service, poll_interval_seconds=1)

        engine.start()
        self.assertTrue(first_poll.wait(timeout=2.0))
        engine.stop(join_timeout=1.0)

        self.assertGreaterEqual(service.poll_count, 1)
        self.assertFalse(engine.running)

    def test_stop_timeout_keeps_running_until_worker_exits(self) -> None:
        service = _BlockingReminderService()
        engine = TimerEngine(reminder_service=service, poll_interval_seconds=1)

        engine.start()
        self.assertTrue(service.entered.wait(timeout=1.0))
        engine.stop(join_timeout=0.01)
        self.assertTrue(engine.running)

        engine.start()
        self.assertEqual(service.poll_count, 1)

        service.release.set()
        self.assertTrue(self._wait_until(lambda: not engine.running, timeout=2.0))

    def test_tick_once_runs_periodic_tasks(self) -> None:
        service = _FakeReminderService()
        periodic = _FakePeriodicTask()
        engine = TimerEngine(
            reminder_service=service,
            periodic_tasks=[periodic],
            poll_interval_seconds=1,
        )

        engine.tick_once()

        self.assertEqual(service.poll_count, 1)
        self.assertEqual(periodic.call_count, 1)

    def test_tick_once_continues_when_periodic_task_fails(self) -> None:
        service = _FakeReminderService()
        failing = _FakePeriodicTask(raises=True)
        succeeding = _FakePeriodicTask()
        engine = TimerEngine(
            reminder_service=service,
            periodic_tasks=[failing, succeeding],
            poll_interval_seconds=1,
        )

        engine.tick_once()

        self.assertEqual(service.poll_count, 1)
        self.assertEqual(failing.call_count, 1)
        self.assertEqual(succeeding.call_count, 1)


if __name__ == "__main__":
    unittest.main()
