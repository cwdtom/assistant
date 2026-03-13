from __future__ import annotations

import unittest
from datetime import datetime

from assistant_app.scheduled_task_cron import compute_next_run_at_from_cron, validate_cron_expr


class _FakeCronIterator:
    def __init__(self, *, next_run_at: datetime) -> None:
        self._next_run_at = next_run_at
        self.requested_types: list[type[datetime]] = []

    def get_next(self, ret_type: type[datetime]) -> datetime:
        self.requested_types.append(ret_type)
        return self._next_run_at


class ScheduledTaskCronTest(unittest.TestCase):
    def test_compute_next_run_at_uses_iterator_factory_result(self) -> None:
        now = datetime(2026, 3, 13, 8, 0, 0)
        next_run_at = datetime(2026, 3, 13, 9, 30, 45, 123456)
        captured: dict[str, object] = {}
        fake_iterator = _FakeCronIterator(next_run_at=next_run_at)

        def _factory(expr: str, factory_now: datetime) -> _FakeCronIterator:
            captured["expr"] = expr
            captured["now"] = factory_now
            return fake_iterator

        computed = compute_next_run_at_from_cron(
            cron_expr="30 9 * * *",
            now=now,
            iterator_factory=_factory,
        )

        self.assertEqual(captured["expr"], "30 9 * * *")
        self.assertEqual(captured["now"], now)
        self.assertEqual(fake_iterator.requested_types, [datetime])
        self.assertEqual(computed, "2026-03-13 09:30:45")

    def test_validate_cron_expr_returns_original_expression(self) -> None:
        now = datetime(2026, 3, 13, 8, 0, 0)
        validated = validate_cron_expr(
            "0 * * * *",
            now=now,
            iterator_factory=lambda _expr, _now: _FakeCronIterator(next_run_at=now),
        )

        self.assertEqual(validated, "0 * * * *")


if __name__ == "__main__":
    unittest.main()
