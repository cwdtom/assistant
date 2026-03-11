from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Protocol


class CronIterator(Protocol):
    def get_next(self, ret_type: type[datetime]) -> datetime: ...


def build_cron_iterator(expr: str, now: datetime) -> CronIterator:
    try:
        from croniter import croniter
    except ImportError as exc:  # pragma: no cover - exercised in integration environments
        raise RuntimeError("croniter is required for scheduled planner tasks") from exc
    return croniter(expr, now)


def compute_next_run_at_from_cron(
    *,
    cron_expr: str,
    now: datetime,
    iterator_factory: Callable[[str, datetime], CronIterator] | None = None,
) -> str:
    iterator = (iterator_factory or build_cron_iterator)(cron_expr, now)
    next_run_at = iterator.get_next(datetime).replace(microsecond=0)
    return next_run_at.strftime("%Y-%m-%d %H:%M:%S")


def validate_cron_expr(
    cron_expr: str,
    *,
    now: datetime | None = None,
    iterator_factory: Callable[[str, datetime], CronIterator] | None = None,
) -> str:
    reference_now = (now or datetime.now()).replace(microsecond=0)
    compute_next_run_at_from_cron(
        cron_expr=cron_expr,
        now=reference_now,
        iterator_factory=iterator_factory,
    )
    return cron_expr


__all__ = [
    "CronIterator",
    "build_cron_iterator",
    "compute_next_run_at_from_cron",
    "validate_cron_expr",
]
