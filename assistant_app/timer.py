from __future__ import annotations

import logging
import threading

from assistant_app.reminder_service import ReminderService


class TimerEngine:
    def __init__(
        self,
        *,
        reminder_service: ReminderService,
        poll_interval_seconds: int = 15,
        logger: logging.Logger | None = None,
    ) -> None:
        self._reminder_service = reminder_service
        self._poll_interval_seconds = max(poll_interval_seconds, 1)
        self._logger = logger or logging.getLogger("assistant_app.timer")
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="assistant-timer",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, join_timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=max(join_timeout, 0.0))
        self._thread = None

    def tick_once(self) -> None:
        stats = self._reminder_service.poll_once()
        if stats.candidate_count > 0:
            self._logger.info(
                "timer tick candidates=%d delivered=%d skipped=%d failed=%d",
                stats.candidate_count,
                stats.delivered_count,
                stats.skipped_count,
                stats.failed_count,
            )

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick_once()
            except Exception:  # noqa: BLE001
                self._logger.exception("timer loop tick failed")
            self._stop_event.wait(self._poll_interval_seconds)
