from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import ValidationError

from assistant_app.schemas.feishu import (
    FeishuPendingTaskInput,
    FeishuProactiveTextRequest,
    FeishuSubtaskResultUpdate,
    FeishuTextMessage,
    inspect_feishu_text_message_payload,
    parse_feishu_message_text,
    parse_feishu_response_status,
    parse_feishu_text_message,
)
from assistant_app.schemas.validation_errors import first_validation_issue

DEFAULT_FEISHU_SEND_RETRY_COUNT = 3
DEFAULT_FEISHU_SEND_RETRY_BACKOFF_SECONDS = 0.5
DEFAULT_FEISHU_TEXT_CHUNK_SIZE = 5000
DEFAULT_FEISHU_DEDUP_TTL_SECONDS = 600
DEFAULT_FEISHU_ACK_REACTION_ENABLED = True
DEFAULT_FEISHU_ACK_EMOJI_TYPE = "Get"
DEFAULT_FEISHU_DONE_EMOJI_TYPE = "DONE"
REACTION_SKIP_HTTP_STATUS_CODE = 400


class AgentLike(Protocol):
    def handle_input(self, user_input: str) -> str: ...

    def set_subtask_result_callback(self, callback: Callable[[str], None] | None) -> None: ...


_PendingTaskInput = FeishuPendingTaskInput
_SubtaskResultUpdate = FeishuSubtaskResultUpdate


class MessageDeduplicator:
    def __init__(self, ttl_seconds: int = DEFAULT_FEISHU_DEDUP_TTL_SECONDS) -> None:
        self._ttl_seconds = max(ttl_seconds, 1)
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def seen(self, message_id: str) -> bool:
        now = time.monotonic()
        expire_before = now - self._ttl_seconds
        with self._lock:
            stale_keys = [key for key, ts in self._seen.items() if ts < expire_before]
            for key in stale_keys:
                self._seen.pop(key, None)

            if message_id in self._seen:
                return True

            self._seen[message_id] = now
            return False


def split_text_chunks(text: str, *, chunk_size: int) -> list[str]:
    size = max(chunk_size, 1)
    if not text:
        return [""]
    return [text[index : index + size] for index in range(0, len(text), size)]


def split_semantic_messages(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n")
    segments = [segment.strip() for segment in re.split(r"\n{2,}", normalized)]
    result = [segment for segment in segments if segment]
    if result:
        return result
    return [text]


def parse_message_text(raw_content: str) -> str:
    return parse_feishu_message_text(raw_content)


def extract_text_message(event_payload: Any) -> FeishuTextMessage | None:
    return parse_feishu_text_message(event_payload)


def convert_message_to_text(*, message_type: str, raw_content: str) -> str:
    if message_type == "text":
        return parse_message_text(raw_content)
    if message_type == "post":
        return raw_content.strip()
    return ""


def _mask_open_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 3:
        return "*" * len(text)
    return f"{text[:2]}***{text[-1:]}"


def _mask_log_text(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\n", "\\n").strip()
    if not text:
        return ""
    if len(text) <= 3:
        masked = "*" * len(text)
    else:
        masked = f"{text[:2]}***{text[-1:]}"
    return f"{masked}(len={len(text)})"


def _coerce_optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return int(normalized)
        except ValueError:
            return None
    return None


def _read_attr_or_key(raw: Any, key: str) -> Any:
    if isinstance(raw, dict):
        return raw.get(key)
    return getattr(raw, key, None)


def _extract_http_status_code(raw: Any) -> int | None:
    candidates = [raw]
    visited: set[int] = set()
    status_keys = ("status_code", "http_status_code", "http_status")
    method_keys = ("get_status_code", "get_http_status_code", "get_http_status")
    nested_keys = ("raw", "response", "raw_response", "http_response")

    while candidates:
        current = candidates.pop(0)
        if current is None:
            continue
        current_id = id(current)
        if current_id in visited:
            continue
        visited.add(current_id)

        for key in status_keys:
            parsed = _coerce_optional_int(_read_attr_or_key(current, key))
            if parsed is not None:
                return parsed

        for key in method_keys:
            reader = _read_attr_or_key(current, key)
            if not callable(reader):
                continue
            try:
                parsed = _coerce_optional_int(reader())
            except Exception:  # noqa: BLE001
                parsed = None
            if parsed is not None:
                return parsed

        for key in nested_keys:
            nested = _read_attr_or_key(current, key)
            if nested is not None:
                candidates.append(nested)
    return None


class FeishuSendError(RuntimeError):
    def __init__(
        self,
        *,
        action: str,
        code: Any,
        msg: str,
        http_status_code: int | None,
    ) -> None:
        super().__init__(f"{action} failed: code={code}, msg={msg}")
        self.action = action
        self.code = code
        self.msg = msg
        self.http_status_code = http_status_code


class FeishuEventProcessor:
    def __init__(
        self,
        *,
        agent: AgentLike,
        send_text: Callable[[str, str], None],
        send_reaction: Callable[[str, str], None],
        logger: logging.Logger,
        progress_content_rewriter: Callable[[str], str] | None = None,
        allowed_open_ids: set[str] | None = None,
        deduplicator: MessageDeduplicator | None = None,
        send_retry_count: int = DEFAULT_FEISHU_SEND_RETRY_COUNT,
        send_retry_backoff_seconds: float = DEFAULT_FEISHU_SEND_RETRY_BACKOFF_SECONDS,
        text_chunk_size: int = DEFAULT_FEISHU_TEXT_CHUNK_SIZE,
        ack_reaction_enabled: bool = DEFAULT_FEISHU_ACK_REACTION_ENABLED,
        ack_emoji_type: str = DEFAULT_FEISHU_ACK_EMOJI_TYPE,
        done_emoji_type: str = DEFAULT_FEISHU_DONE_EMOJI_TYPE,
    ) -> None:
        self._agent = agent
        self._send_text = send_text
        self._send_reaction = send_reaction
        self._logger = logger
        self._allowed_open_ids = set(allowed_open_ids or set())
        self._deduplicator = deduplicator or MessageDeduplicator()
        self._send_retry_count = max(send_retry_count, 0)
        self._send_retry_backoff_seconds = max(send_retry_backoff_seconds, 0.0)
        self._text_chunk_size = max(text_chunk_size, 1)
        self._ack_reaction_enabled = ack_reaction_enabled
        self._ack_emoji_type = ack_emoji_type.strip() or DEFAULT_FEISHU_ACK_EMOJI_TYPE
        self._done_emoji_type = done_emoji_type.strip() or DEFAULT_FEISHU_DONE_EMOJI_TYPE
        self._progress_content_rewriter = progress_content_rewriter
        self._progress_queue: queue.Queue[_SubtaskResultUpdate] = queue.Queue()
        self._progress_worker_lock = threading.Lock()
        self._progress_worker: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._state_condition = threading.Condition(self._state_lock)
        self._active_task: _PendingTaskInput | None = None
        self._pending_task: _PendingTaskInput | None = None
        self._worker_thread: threading.Thread | None = None
        self._bind_subtask_result_callback()

    def set_send_text(self, send_text: Callable[[str, str], None]) -> None:
        self._send_text = send_text

    def set_send_reaction(self, send_reaction: Callable[[str, str], None]) -> None:
        self._send_reaction = send_reaction

    def _bind_subtask_result_callback(self) -> None:
        setter = getattr(self._agent, "set_subtask_result_callback", None)
        if not callable(setter):
            return
        try:
            setter(self._on_subtask_result_update)
        except Exception:  # noqa: BLE001
            self._logger.warning("failed to bind subtask result callback", exc_info=True)

    def _on_subtask_result_update(self, result: str) -> None:
        normalized_result = result.strip()
        if not normalized_result:
            return
        with self._state_lock:
            active_task = self._active_task
            if active_task is None:
                return
            update = _SubtaskResultUpdate(
                chat_id=active_task.chat_id,
                message_id=active_task.latest_message_id,
                result=normalized_result,
            )
        self._ensure_progress_worker_started()
        self._progress_queue.put(update)

    def _ensure_progress_worker_started(self) -> None:
        with self._progress_worker_lock:
            worker = self._progress_worker
            if worker is not None and worker.is_alive():
                return
            self._progress_worker = threading.Thread(
                target=self._process_subtask_result_queue,
                name="feishu-subtask-result-worker",
                daemon=True,
            )
            self._progress_worker.start()

    def _process_subtask_result_queue(self) -> None:
        while True:
            update = self._progress_queue.get()
            message = update.result
            rewriter = self._progress_content_rewriter
            if rewriter is not None:
                try:
                    rewritten = rewriter(message)
                    normalized_rewritten = rewritten.strip()
                    if normalized_rewritten:
                        message = normalized_rewritten
                except Exception:  # noqa: BLE001
                    self._logger.warning(
                        "feishu subtask progress rewrite failed: message_id=%s",
                        update.message_id,
                        exc_info=True,
                    )
            try:
                self._send_text(update.chat_id, message)
                self._logger.info(
                    "feishu subtask progress sent: message_id=%s chat_id=%s text=%s",
                    update.message_id,
                    update.chat_id,
                    _mask_log_text(message),
                )
            except Exception:  # noqa: BLE001
                self._logger.warning(
                    "feishu subtask progress dropped: message_id=%s chat_id=%s text=%s",
                    update.message_id,
                    update.chat_id,
                    _mask_log_text(message),
                    exc_info=True,
                )

    def handle_event(self, event_payload: Any) -> None:
        message, invalid_reason, message_type, chat_type = inspect_feishu_text_message_payload(event_payload)
        if message is None:
            self._logger.warning(
                "feishu event payload invalid",
                extra={
                    "event": "feishu_event_payload_invalid",
                    "context": {
                        "reason": invalid_reason or "unknown",
                        "message_type": message_type,
                        "chat_type": chat_type,
                    },
                },
            )
            return
        self._logger.info(
            "feishu inbound message received: message_id=%s chat_id=%s open_id=%s text=%s",
            message.message_id,
            message.chat_id,
            _mask_open_id(message.open_id),
            _mask_log_text(message.text),
        )

        if self._allowed_open_ids and message.open_id not in self._allowed_open_ids:
            self._logger.info("feishu event dropped: open_id not allowed open_id=%s", _mask_open_id(message.open_id))
            return

        if self._deduplicator.seen(message.message_id):
            self._logger.info("feishu event dropped: duplicate message_id=%s", message.message_id)
            return

        should_start_processing = False
        with self._state_lock:
            if self._active_task is None:
                self._active_task = _PendingTaskInput(
                    chat_id=message.chat_id,
                    text=message.text,
                    latest_message_id=message.message_id,
                )
            else:
                self._enqueue_interrupting_message(message)
            should_start_processing = self._ensure_worker_started_locked()
            self._state_condition.notify()

        if not should_start_processing:
            return

    def _ensure_worker_started_locked(self) -> bool:
        worker = self._worker_thread
        if worker is not None and worker.is_alive():
            return True
        self._worker_thread = threading.Thread(
            target=self._process_task_queue,
            name="feishu-event-worker",
            daemon=True,
        )
        self._worker_thread.start()
        return True

    def _process_task_queue(self) -> None:
        while True:
            try:
                with self._state_condition:
                    while self._active_task is None:
                        self._state_condition.wait(timeout=1.0)
                        if self._active_task is None:
                            self._worker_thread = None
                            return
                    active_task = self._active_task

                self._send_ack_for_task_start(active_task)

                task_interrupted = False
                try:
                    response_text, task_completed = self._run_agent(active_task.text)
                except Exception as exc:  # noqa: BLE001
                    self._logger.exception("feishu event handle failed: %s", exc)
                    response_text = "处理失败，请稍后重试。"
                    task_completed = False

                with self._state_lock:
                    task_interrupted = self._pending_task is not None

                if task_interrupted:
                    self._logger.info(
                        "feishu response skipped: message_id=%s interrupted_by_newer_input",
                        active_task.latest_message_id,
                    )
                else:
                    if task_completed:
                        if self._has_pending_task():
                            self._logger.info(
                                "feishu done reaction skipped: message_id=%s interrupted_before_done",
                                active_task.latest_message_id,
                            )
                        else:
                            try:
                                skipped = self._send_reaction_with_retry(
                                    message_id=active_task.latest_message_id,
                                    emoji_type=self._done_emoji_type,
                                )
                                if skipped:
                                    self._logger.info(
                                        "feishu done reaction skipped: message_id=%s emoji=%s reason=http_status_400",
                                        active_task.latest_message_id,
                                        self._done_emoji_type,
                                    )
                                else:
                                    self._logger.info(
                                        "feishu done reaction sent: message_id=%s emoji=%s",
                                        active_task.latest_message_id,
                                        self._done_emoji_type,
                                    )
                            except Exception:  # noqa: BLE001
                                self._logger.warning(
                                    "feishu done reaction failed: message_id=%s emoji=%s",
                                    active_task.latest_message_id,
                                    self._done_emoji_type,
                                    exc_info=True,
                                )

                    normalized_response = (response_text or "").strip()
                    if task_completed and not normalized_response:
                        self._logger.info(
                            "feishu response skipped: message_id=%s completed_without_text",
                            active_task.latest_message_id,
                            extra={
                                "event": "feishu_response_skipped_completed_without_text",
                                "context": {"message_id": active_task.latest_message_id},
                            },
                        )
                    else:
                        payload_text = normalized_response or "收到。"
                        semantic_messages = split_semantic_messages(payload_text)
                        interrupted_while_sending = False
                        for message_index, semantic_message in enumerate(semantic_messages, start=1):
                            chunks = split_text_chunks(semantic_message, chunk_size=self._text_chunk_size)
                            for chunk_index, chunk in enumerate(chunks, start=1):
                                if self._has_pending_task():
                                    interrupted_while_sending = True
                                    break
                                self._send_with_retry(chat_id=active_task.chat_id, text=chunk)
                                self._logger.info(
                                    "feishu response sent: message_id=%s message=%s/%s chunk=%s/%s text=%s",
                                    active_task.latest_message_id,
                                    message_index,
                                    len(semantic_messages),
                                    chunk_index,
                                    len(chunks),
                                    _mask_log_text(chunk),
                                )
                            if interrupted_while_sending:
                                break
                        if interrupted_while_sending:
                            self._logger.info(
                                "feishu response aborted: message_id=%s interrupted_during_send",
                                active_task.latest_message_id,
                            )

                with self._state_lock:
                    if self._pending_task is None:
                        self._active_task = None
                    else:
                        self._active_task = self._pending_task
                        self._pending_task = None
            except Exception:  # noqa: BLE001
                self._logger.exception("feishu worker loop failed unexpectedly")

    def _send_ack_for_task_start(self, task: _PendingTaskInput) -> None:
        if not self._ack_reaction_enabled:
            return
        try:
            skipped = self._send_reaction_with_retry(message_id=task.latest_message_id, emoji_type=self._ack_emoji_type)
            if skipped:
                self._logger.info(
                    "feishu ack reaction skipped: message_id=%s emoji=%s reason=http_status_400",
                    task.latest_message_id,
                    self._ack_emoji_type,
                )
            else:
                self._logger.info(
                    "feishu ack reaction sent: message_id=%s emoji=%s",
                    task.latest_message_id,
                    self._ack_emoji_type,
                )
        except Exception:  # noqa: BLE001
            self._logger.warning(
                "feishu ack reaction failed: message_id=%s emoji=%s",
                task.latest_message_id,
                self._ack_emoji_type,
                exc_info=True,
            )

    def _enqueue_interrupting_message(self, message: FeishuTextMessage) -> None:
        active_task = self._active_task
        if active_task is None:
            self._active_task = _PendingTaskInput(
                chat_id=message.chat_id,
                text=message.text,
                latest_message_id=message.message_id,
            )
            return

        if self._pending_task is None:
            if active_task.chat_id == message.chat_id:
                merged_text = self._merge_task_text(active_task.text, message.text)
                self._pending_task = _PendingTaskInput(
                    chat_id=active_task.chat_id,
                    text=merged_text,
                    latest_message_id=message.message_id,
                )
            else:
                self._pending_task = _PendingTaskInput(
                    chat_id=message.chat_id,
                    text=message.text,
                    latest_message_id=message.message_id,
                )
        else:
            if self._pending_task.chat_id == message.chat_id:
                self._pending_task.text = self._merge_task_text(self._pending_task.text, message.text)
            else:
                # Pending task always tracks the latest chat context to avoid cross-chat text leakage.
                self._pending_task.chat_id = message.chat_id
                self._pending_task.text = message.text
            self._pending_task.latest_message_id = message.message_id

        self._request_agent_interrupt()
        self._logger.info(
            "feishu task interrupted and queued: current_message_id=%s queued_message_id=%s",
            active_task.latest_message_id,
            message.message_id,
        )

    @staticmethod
    def _merge_task_text(current_text: str, new_text: str) -> str:
        left = current_text.strip()
        right = new_text.strip()
        if not left:
            return right
        if not right:
            return left
        return f"{left}\n{right}"

    def _request_agent_interrupt(self) -> None:
        interrupt = getattr(self._agent, "interrupt_current_task", None)
        if not callable(interrupt):
            return
        try:
            interrupt()
        except Exception:  # noqa: BLE001
            self._logger.warning("feishu failed to interrupt active task", exc_info=True)

    def _has_pending_task(self) -> bool:
        with self._state_lock:
            return self._pending_task is not None

    def _send_with_retry(self, *, chat_id: str, text: str) -> None:
        self._run_with_retry(lambda: self._send_text(chat_id, text))

    def _send_reaction_with_retry(self, *, message_id: str, emoji_type: str) -> bool:
        attempts = self._send_retry_count + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                self._send_reaction(message_id, emoji_type)
                return False
            except Exception as exc:  # noqa: BLE001
                if _extract_http_status_code(exc) == REACTION_SKIP_HTTP_STATUS_CODE:
                    return True
                last_error = exc
                if attempt >= attempts:
                    break
                sleep_seconds = self._send_retry_backoff_seconds * (2 ** (attempt - 1))
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
        if last_error is not None:
            raise last_error
        return False

    def _run_agent(self, user_input: str) -> tuple[str, bool]:
        maybe_task_aware = getattr(self._agent, "handle_input_with_task_status", None)
        if callable(maybe_task_aware):
            result = maybe_task_aware(user_input)
            if isinstance(result, tuple) and len(result) == 2:
                return str(result[0]), bool(result[1])
            return str(result), False
        return self._agent.handle_input(user_input), False

    def _run_with_retry(self, operation: Callable[[], None]) -> None:
        attempts = self._send_retry_count + 1
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                operation()
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= attempts:
                    break
                sleep_seconds = self._send_retry_backoff_seconds * (2 ** (attempt - 1))
                if sleep_seconds > 0:
                    time.sleep(sleep_seconds)
        if last_error is not None:
            raise last_error


class FeishuLongConnectionRunner:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        event_processor: FeishuEventProcessor,
        logger: logging.Logger,
        sdk_module: Any | None = None,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._event_processor = event_processor
        self._logger = logger
        self._sdk_module = sdk_module
        self._ws_client: Any | None = None
        self._thread: threading.Thread | None = None
        self._open_id_sender_lock = threading.Lock()
        self._send_text_to_open_id: Callable[[str, str], None] | None = None

    def start_background(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="feishu-long-connection", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        client = self._ws_client
        if client is None:
            return
        stop = getattr(client, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:  # noqa: BLE001
                self._logger.warning("failed to stop feishu ws client", exc_info=True)

    def send_open_id_text(self, *, open_id: str, text: str) -> None:
        try:
            request = FeishuProactiveTextRequest.model_validate(
                {
                    "open_id": open_id,
                    "text": text,
                }
            )
        except ValidationError as exc:
            field_name = first_validation_issue(exc).field
            if field_name == "open_id":
                raise ValueError("open_id is required") from exc
            if field_name == "text":
                raise ValueError("text is required") from exc
            raise ValueError("invalid open_id text request") from exc
        with self._open_id_sender_lock:
            send_text_to_open_id = self._send_text_to_open_id
        if send_text_to_open_id is None:
            raise RuntimeError("feishu open_id sender not ready")
        try:
            send_text_to_open_id(request.open_id, request.text)
            self._logger.info(
                "feishu open_id response sent: open_id=%s text=%s",
                _mask_open_id(request.open_id),
                _mask_log_text(request.text),
            )
        except Exception:  # noqa: BLE001
            self._logger.warning(
                "feishu open_id response failed: open_id=%s text=%s",
                _mask_open_id(request.open_id),
                _mask_log_text(request.text),
                exc_info=True,
            )
            raise

    def _run(self) -> None:
        try:
            lark_module: Any = self._sdk_module
            if lark_module is None:
                import lark_oapi as lark_oapi_module  # type: ignore[import-untyped]

                lark_module = lark_oapi_module

            api_client = lark_module.Client.builder().app_id(self._app_id).app_secret(self._app_secret).build()

            def send_text(chat_id: str, text: str) -> None:
                self._send_text_message(api_client=api_client, chat_id=chat_id, text=text)

            def send_reaction(message_id: str, emoji_type: str) -> None:
                self._send_ack_reaction(api_client=api_client, message_id=message_id, emoji_type=emoji_type)

            def send_text_to_open_id(open_id: str, text: str) -> None:
                self._send_text_message_by_open_id(api_client=api_client, open_id=open_id, text=text)

            self._event_processor.set_send_text(send_text)
            self._event_processor.set_send_reaction(send_reaction)
            with self._open_id_sender_lock:
                self._send_text_to_open_id = send_text_to_open_id

            event_handler = (
                lark_module.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(self._event_processor.handle_event)
                .build()
            )

            self._ws_client = lark_module.ws.Client(
                self._app_id,
                self._app_secret,
                event_handler=event_handler,
                log_level=lark_module.LogLevel.DEBUG,
            )
            self._logger.info("feishu long connection started")
            self._ws_client.start()
        except ImportError:
            self._logger.exception("lark_oapi 未安装，无法启动飞书长连接")
        except Exception:  # noqa: BLE001
            self._logger.exception("飞书长连接运行失败")
        finally:
            with self._open_id_sender_lock:
                self._send_text_to_open_id = None

    @staticmethod
    def _send_text_message(*, api_client: Any, chat_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import (  # type: ignore[import-untyped]
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = api_client.im.v1.message.create(request)
        FeishuLongConnectionRunner._ensure_send_response_success(response=response, action="send message")

    @staticmethod
    def _send_text_message_by_open_id(*, api_client: Any, open_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("open_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(open_id)
                .msg_type("text")
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .build()
            )
            .build()
        )
        response = api_client.im.v1.message.create(request)
        FeishuLongConnectionRunner._ensure_send_response_success(response=response, action="send message")

    @staticmethod
    def _send_ack_reaction(*, api_client: Any, message_id: str, emoji_type: str) -> None:
        from lark_oapi.api.im.v1 import (
            CreateMessageReactionRequest,
            CreateMessageReactionRequestBody,
            Emoji,
        )

        request = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(
                CreateMessageReactionRequestBody.builder()
                .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                .build()
            )
            .build()
        )
        response = api_client.im.v1.message_reaction.create(request)
        FeishuLongConnectionRunner._ensure_send_response_success(response=response, action="send reaction")

    @staticmethod
    def _ensure_send_response_success(*, response: Any, action: str) -> None:
        http_status_code = _extract_http_status_code(response)
        success = getattr(response, "success", None)
        if callable(success):
            if success():
                return
            status = parse_feishu_response_status(response)
            raw_code = (
                status.code
                if status is not None and status.code is not None
                else getattr(response, "code", None)
            )
            code = raw_code if raw_code is not None else "unknown"
            msg = (
                status.msg
                if status is not None and status.msg is not None
                else str(getattr(response, "msg", "") or "")
            )
            raise FeishuSendError(
                action=action,
                code=code,
                msg=msg,
                http_status_code=http_status_code,
            )

        status = parse_feishu_response_status(response)
        if status is None or status.is_success():
            return
        raise FeishuSendError(
            action=action,
            code=status.code,
            msg=status.msg or "",
            http_status_code=http_status_code,
        )


def create_feishu_runner(
    *,
    app_id: str,
    app_secret: str,
    agent: AgentLike,
    logger: logging.Logger,
    progress_content_rewriter: Callable[[str], str] | None,
    allowed_open_ids: set[str] | None,
    send_retry_count: int,
    text_chunk_size: int,
    dedup_ttl_seconds: int,
    ack_reaction_enabled: bool,
    ack_emoji_type: str,
    done_emoji_type: str,
) -> FeishuLongConnectionRunner:
    # The send function is replaced once SDK client is ready in FeishuLongConnectionRunner._run().
    processor = FeishuEventProcessor(
        agent=agent,
        send_text=lambda _chat_id, _text: None,
        send_reaction=lambda _message_id, _emoji_type: None,
        logger=logger,
        progress_content_rewriter=progress_content_rewriter,
        allowed_open_ids=allowed_open_ids,
        deduplicator=MessageDeduplicator(ttl_seconds=dedup_ttl_seconds),
        send_retry_count=send_retry_count,
        text_chunk_size=text_chunk_size,
        ack_reaction_enabled=ack_reaction_enabled,
        ack_emoji_type=ack_emoji_type,
        done_emoji_type=done_emoji_type,
    )
    return FeishuLongConnectionRunner(
        app_id=app_id,
        app_secret=app_secret,
        event_processor=processor,
        logger=logger,
    )
