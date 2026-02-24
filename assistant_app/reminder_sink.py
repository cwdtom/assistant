from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from typing import Protocol, TextIO


@dataclass(frozen=True)
class ReminderEvent:
    reminder_key: str
    source_type: str
    source_id: int
    remind_time: str
    content: str
    occurrence_time: str | None = None


class ReminderSink(Protocol):
    def emit(self, event: ReminderEvent) -> None: ...


class StdoutReminderSink:
    def __init__(self, stream: TextIO = sys.stdout, prompt: str = "你> ") -> None:
        self._stream = stream
        self._prompt = prompt
        self._write_lock = threading.Lock()

    def emit(self, event: ReminderEvent) -> None:
        with self._write_lock:
            self._stream.write(f"\n提醒> {event.content}\n{self._prompt}")
            self._stream.flush()
