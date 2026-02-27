from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from assistant_app.reminder_service import ReminderService


class TimerEngine:
    def __init__(
        self,
        *,
        reminder_service: ReminderService,
        periodic_tasks: list[Callable[[], None]] | None = None,
        poll_interval_seconds: int = 15,
        logger: logging.Logger | None = None,
    ) -> None:
        self._reminder_service = reminder_service
        self._periodic_tasks = list(periodic_tasks or [])
        self._poll_interval_seconds = max(poll_interval_seconds, 1)
        self._logger = logger or logging.getLogger("assistant_app.timer")
        if logger is None:
            self._logger.propagate = False
            if not self._logger.handlers:
                self._logger.addHandler(logging.NullHandler())
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        with self._state_lock:
            thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._run_loop,
                name="assistant-timer",
                daemon=True,
            )
            self._thread = thread
        thread.start()

    def stop(self, *, join_timeout: float = 2.0) -> None:
        self._stop_event.set()
        with self._state_lock:
            thread = self._thread
        if thread is None:
            return
        thread.join(timeout=max(join_timeout, 0.0))
        with self._state_lock:
            if self._thread is not thread:
                return
            if thread.is_alive():
                self._logger.warning(
                    "timer thread stop timeout",
                    extra={
                        "event": "timer_stop_timeout",
                        "context": {"join_timeout_seconds": join_timeout},
                    },
                )
                return
            self._thread = None

    def tick_once(self) -> None:
        stats = self._reminder_service.poll_once()
        if stats.candidate_count > 0:
            self._logger.info(
                "timer tick completed",
                extra={
                    "event": "timer_tick",
                    "context": {
                        "candidates": stats.candidate_count,
                        "delivered": stats.delivered_count,
                        "skipped": stats.skipped_count,
                        "failed": stats.failed_count,
                    },
                },
            )
        for index, task in enumerate(self._periodic_tasks):
            try:
                task()
            except Exception:  # noqa: BLE001
                self._logger.exception(
                    "timer periodic task failed",
                    extra={
                        "event": "timer_periodic_task_failed",
                        "context": {"task_index": index},
                    },
                )

    def _run_loop(self) -> None:
        current_thread = threading.current_thread()
        try:
            while not self._stop_event.is_set():
                try:
                    self.tick_once()
                except Exception:  # noqa: BLE001
                    self._logger.exception(
                        "timer loop tick failed",
                        extra={"event": "timer_tick_failed"},
                    )
                self._stop_event.wait(self._poll_interval_seconds)
        finally:
            with self._state_lock:
                if self._thread is current_thread:
                    self._thread = None
