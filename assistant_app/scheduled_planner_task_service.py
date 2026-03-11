from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from assistant_app.agent import AssistantAgent
from assistant_app.db import AssistantDB
from assistant_app.feishu_adapter import split_semantic_messages
from assistant_app.scheduled_result_decision import ScheduledResultDecisionRunner
from assistant_app.scheduled_task_cron import CronIterator, build_cron_iterator, compute_next_run_at_from_cron
from assistant_app.schemas.scheduled_tasks import (
    ScheduledPlannerTask,
    ScheduledTaskResultDecisionPromptPayload,
)

DEFAULT_SCHEDULED_RESULT_DECISION_MAX_STEPS = 3

@dataclass(frozen=True)
class _ScheduledPlannerQueueItem:
    task_id: int
    task_name: str
    cron_expr: str
    prompt: str
    run_limit: int
    expected_next_run_at: str


class ScheduledPlannerTaskService:
    def __init__(
        self,
        *,
        db: AssistantDB,
        agent: AssistantAgent,
        llm_client: Any | None,
        logger: logging.Logger,
        target_open_id: str,
        send_text_to_open_id: Callable[[str, str], None],
        result_decision_max_steps: int = DEFAULT_SCHEDULED_RESULT_DECISION_MAX_STEPS,
        clock: Callable[[], datetime] | None = None,
        croniter_factory: Callable[[str, datetime], CronIterator] | None = None,
    ) -> None:
        self._db = db
        self._agent = agent
        self._logger = logger
        self._target_open_id = target_open_id.strip()
        self._send_text_to_open_id = send_text_to_open_id
        self._clock = clock or datetime.now
        self._croniter_factory = croniter_factory or _default_croniter_factory
        self._result_decision_runner = ScheduledResultDecisionRunner(
            llm_client=llm_client,
            max_steps=result_decision_max_steps,
            logger=logger,
        )
        self._scan_lock = threading.Lock()
        self._worker_lock = threading.Lock()
        self._queued_task_ids_lock = threading.Lock()
        self._queued_task_ids: set[int] = set()
        self._queue: queue.Queue[_ScheduledPlannerQueueItem | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None

    def poll_scheduled(self) -> None:
        if self._stop_event.is_set():
            return
        now = self._clock().replace(microsecond=0)
        with self._scan_lock:
            self._initialize_missing_next_runs(now=now)
            self._enqueue_due_tasks(now=now)

    def stop(self, *, join_timeout: float = 2.0) -> None:
        self._stop_event.set()
        self._queue.put(None)
        with self._worker_lock:
            worker = self._worker
        if worker is None:
            return
        worker.join(timeout=max(join_timeout, 0.0))
        with self._worker_lock:
            if self._worker is worker and not worker.is_alive():
                self._worker = None

    def _initialize_missing_next_runs(self, *, now: datetime) -> None:
        for task in self._db.list_uninitialized_scheduled_planner_tasks():
            next_run_at = self._compute_next_run_at(task=task, now=now)
            if next_run_at is None:
                continue
            updated = self._db.initialize_scheduled_planner_task_next_run(task.id, next_run_at=next_run_at)
            if not updated:
                continue
            self._logger.info(
                "scheduled task next run initialized",
                extra={
                    "event": "scheduled_task_next_run_initialized",
                    "context": {
                        "task_name": task.task_name,
                        "cron_expr": task.cron_expr,
                        "next_run_at": next_run_at,
                    },
                },
            )

    def _enqueue_due_tasks(self, *, now: datetime) -> None:
        for task in self._db.list_due_scheduled_planner_tasks(now=now):
            if task.next_run_at is None:
                continue
            if not self._mark_task_queued(task.id):
                continue
            self._ensure_worker_started()
            self._queue.put(
                _ScheduledPlannerQueueItem(
                    task_id=task.id,
                    task_name=task.task_name,
                    cron_expr=task.cron_expr,
                    prompt=task.prompt,
                    run_limit=task.run_limit,
                    expected_next_run_at=task.next_run_at,
                )
            )
            self._logger.info(
                "scheduled task enqueued",
                extra={
                    "event": "scheduled_task_enqueued",
                    "context": {
                        "task_name": task.task_name,
                        "expected_next_run_at": task.next_run_at,
                    },
                },
            )

    def _ensure_worker_started(self) -> None:
        with self._worker_lock:
            worker = self._worker
            if worker is not None and worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._run_worker,
                name="scheduled-planner-task-worker",
                daemon=True,
            )
            self._worker.start()

    def _run_worker(self) -> None:
        current_thread = threading.current_thread()
        try:
            while not self._stop_event.is_set():
                try:
                    item = self._queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                try:
                    self._execute_queue_item(item)
                finally:
                    self._unmark_task_queued(item.task_id)
        finally:
            with self._worker_lock:
                if self._worker is current_thread:
                    self._worker = None

    def _execute_queue_item(self, item: _ScheduledPlannerQueueItem) -> None:
        started_at_dt = self._clock().replace(microsecond=0)
        started_at = started_at_dt.strftime("%Y-%m-%d %H:%M:%S")
        next_run_at = self._compute_next_run_at_from_parts(
            task_name=item.task_name,
            cron_expr=item.cron_expr,
            now=started_at_dt,
        )
        if next_run_at is None:
            return
        updated = self._db.mark_scheduled_planner_task_started(
            item.task_id,
            expected_next_run_at=item.expected_next_run_at,
            started_at=started_at,
            next_run_at=next_run_at,
        )
        if not updated:
            self._logger.info(
                "scheduled task start skipped",
                extra={
                    "event": "scheduled_task_start_skipped",
                    "context": {
                        "task_name": item.task_name,
                        "expected_next_run_at": item.expected_next_run_at,
                        "reason": "stale_due_state",
                    },
                },
            )
            return
        run_limit_after_decrement = _run_limit_after_start(item.run_limit)
        self._logger.info(
            "scheduled task started",
            extra={
                "event": "scheduled_task_started",
                "context": {
                    "task_name": item.task_name,
                    "prompt_length": len(item.prompt),
                    "started_at": started_at,
                    "next_run_at": next_run_at,
                    "run_limit_after_decrement": run_limit_after_decrement,
                },
            },
        )
        try:
            response_text, task_completed = self._agent.handle_input_with_task_status(
                item.prompt,
                source="scheduled",
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "scheduled task failed",
                extra={
                    "event": "scheduled_task_failed",
                    "context": {
                        "task_name": item.task_name,
                        "started_at": started_at,
                        "error": repr(exc),
                    },
                },
            )
            return

        finished_at_dt = self._clock().replace(microsecond=0)
        finished_at = finished_at_dt.strftime("%Y-%m-%d %H:%M:%S")
        response = str(response_text or "")
        if not task_completed:
            self._logger.warning(
                "scheduled task failed: task not completed",
                extra={
                    "event": "scheduled_task_failed",
                    "context": {
                        "task_name": item.task_name,
                        "started_at": started_at,
                        "error": "task_not_completed",
                    },
                },
            )
            return

        self._logger.info(
            "scheduled task completed",
            extra={
                "event": "scheduled_task_completed",
                "context": {
                    "task_name": item.task_name,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "response_length": len(response),
                },
            },
        )
        self._maybe_send_result(
            task_name=item.task_name,
            prompt=item.prompt,
            final_response=response,
            started_at=started_at,
            finished_at=finished_at,
            finished_at_dt=finished_at_dt,
        )

    def _mark_task_queued(self, task_id: int) -> bool:
        with self._queued_task_ids_lock:
            if task_id in self._queued_task_ids:
                return False
            self._queued_task_ids.add(task_id)
            return True

    def _unmark_task_queued(self, task_id: int) -> None:
        with self._queued_task_ids_lock:
            self._queued_task_ids.discard(task_id)

    def _maybe_send_result(
        self,
        *,
        task_name: str,
        prompt: str,
        final_response: str,
        started_at: str,
        finished_at: str,
        finished_at_dt: datetime,
    ) -> None:
        started_at_dt = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
        duration_seconds = max(int((finished_at_dt - started_at_dt).total_seconds()), 0)
        try:
            decision = self._result_decision_runner.run_once(
                context_payload=ScheduledTaskResultDecisionPromptPayload(
                    result={
                        "task_name": task_name,
                        "prompt": prompt,
                        "final_response": final_response,
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "duration_seconds": duration_seconds,
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "scheduled result decision failed",
                extra={
                    "event": "scheduled_result_decision_failed",
                    "context": {
                        "task_name": task_name,
                        "reason": repr(exc),
                    },
                },
            )
            self._logger.info(
                "scheduled result send skipped",
                extra={
                    "event": "scheduled_result_send_skipped",
                    "context": {
                        "task_name": task_name,
                        "reason": "decision_runner_failed",
                    },
                },
            )
            return
        if decision is None:
            self._logger.info(
                "scheduled result send skipped",
                extra={
                    "event": "scheduled_result_send_skipped",
                    "context": {
                        "task_name": task_name,
                        "reason": "decision_unavailable",
                    },
                },
            )
            return
        if not decision.should_send:
            self._logger.info(
                "scheduled result send skipped",
                extra={
                    "event": "scheduled_result_send_skipped",
                    "context": {
                        "task_name": task_name,
                        "reason": "model_declined",
                    },
                },
            )
            return
        if not self._target_open_id:
            self._logger.info(
                "scheduled result send skipped",
                extra={
                    "event": "scheduled_result_send_skipped",
                    "context": {
                        "task_name": task_name,
                        "reason": "target_open_id_missing",
                    },
                },
            )
            return

        segments = split_semantic_messages(decision.message)
        try:
            for segment in segments:
                self._send_text_to_open_id(self._target_open_id, segment)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "scheduled result send failed",
                extra={
                    "event": "scheduled_result_send_failed",
                    "context": {
                        "task_name": task_name,
                        "target_open_id": self._target_open_id,
                        "error": repr(exc),
                    },
                },
            )
            return
        self._logger.info(
            "scheduled result send completed",
            extra={
                "event": "scheduled_result_send_done",
                "context": {
                    "task_name": task_name,
                    "target_open_id": self._target_open_id,
                    "segment_count": len(segments),
                },
            },
        )

    def _compute_next_run_at(self, *, task: ScheduledPlannerTask, now: datetime) -> str | None:
        return self._compute_next_run_at_from_parts(
            task_name=task.task_name,
            cron_expr=task.cron_expr,
            now=now,
        )

    def _compute_next_run_at_from_parts(
        self,
        *,
        task_name: str,
        cron_expr: str,
        now: datetime,
    ) -> str | None:
        try:
            return compute_next_run_at_from_cron(
                cron_expr=cron_expr,
                now=now,
                iterator_factory=self._croniter_factory,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "scheduled task cron parse failed",
                extra={
                    "event": "scheduled_task_cron_parse_failed",
                    "context": {
                        "task_name": task_name,
                        "cron_expr": cron_expr,
                        "error": repr(exc),
                    },
                },
            )
            return None


def _default_croniter_factory(expr: str, now: datetime) -> CronIterator:
    return build_cron_iterator(expr, now)


def _run_limit_after_start(run_limit: int) -> int:
    if run_limit == -1:
        return -1
    return max(run_limit - 1, 0)
