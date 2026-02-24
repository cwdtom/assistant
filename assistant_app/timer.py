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
                self._logger.warning("timer thread did not stop within %.2f seconds", join_timeout)
                return
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
        current_thread = threading.current_thread()
        try:
            while not self._stop_event.is_set():
                try:
                    self.tick_once()
                except Exception:  # noqa: BLE001
                    self._logger.exception("timer loop tick failed")
                self._stop_event.wait(self._poll_interval_seconds)
        finally:
            with self._state_lock:
                if self._thread is current_thread:
                    self._thread = None
