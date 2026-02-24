from __future__ import annotations

import time
import unittest

from assistant_app.timer import TimerEngine


class _FakeReminderService:
    def __init__(self, raises: bool = False) -> None:
        self.raises = raises
        self.poll_count = 0

    def poll_once(self):  # type: ignore[no-untyped-def]
        self.poll_count += 1
        if self.raises:
            raise RuntimeError("boom")

        class _Stats:
            candidate_count = 0
            delivered_count = 0
            skipped_count = 0
            failed_count = 0

        return _Stats()


class TimerEngineTest(unittest.TestCase):
    def test_tick_once_calls_service(self) -> None:
        service = _FakeReminderService()
        engine = TimerEngine(reminder_service=service, poll_interval_seconds=1)

        engine.tick_once()

        self.assertEqual(service.poll_count, 1)

    def test_start_and_stop_loop(self) -> None:
        service = _FakeReminderService()
        engine = TimerEngine(reminder_service=service, poll_interval_seconds=1)

        engine.start()
        time.sleep(1.1)
        engine.stop(join_timeout=1.0)

        self.assertGreaterEqual(service.poll_count, 1)
        self.assertFalse(engine.running)

    def test_loop_survives_poll_exception(self) -> None:
        service = _FakeReminderService(raises=True)
        engine = TimerEngine(reminder_service=service, poll_interval_seconds=1)

        engine.start()
        time.sleep(1.1)
        engine.stop(join_timeout=1.0)

        self.assertGreaterEqual(service.poll_count, 1)
        self.assertFalse(engine.running)


if __name__ == "__main__":
    unittest.main()
