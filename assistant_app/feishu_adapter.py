from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

DEFAULT_FEISHU_SEND_RETRY_COUNT = 3
DEFAULT_FEISHU_SEND_RETRY_BACKOFF_SECONDS = 0.5
DEFAULT_FEISHU_TEXT_CHUNK_SIZE = 1500
DEFAULT_FEISHU_DEDUP_TTL_SECONDS = 600
DEFAULT_FEISHU_ACK_REACTION_ENABLED = True
DEFAULT_FEISHU_ACK_EMOJI_TYPE = "OK"


class AgentLike(Protocol):
    def handle_input(self, user_input: str) -> str: ...


@dataclass(frozen=True)
class FeishuTextMessage:
    message_id: str
    chat_id: str
    open_id: str | None
    text: str


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
    content = raw_content.strip()
    if not content:
        return ""

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content

    if not isinstance(payload, dict):
        return content

    text = payload.get("text")
    if isinstance(text, str):
        return text
    return content


def extract_text_message(event_payload: Any) -> FeishuTextMessage | None:
    message_type = _first_non_empty(
        _read_path(event_payload, "event.message.message_type"),
        _read_path(event_payload, "message.message_type"),
    )
    if message_type != "text":
        return None

    chat_type = _first_non_empty(
        _read_path(event_payload, "event.message.chat_type"),
        _read_path(event_payload, "message.chat_type"),
    )
    if chat_type and chat_type != "p2p":
        return None

    sender_type = _first_non_empty(
        _read_path(event_payload, "event.sender.sender_type"),
        _read_path(event_payload, "sender.sender_type"),
    )
    if sender_type and sender_type != "user":
        return None

    message_id = _first_non_empty(
        _read_path(event_payload, "event.message.message_id"),
        _read_path(event_payload, "message.message_id"),
    )
    chat_id = _first_non_empty(
        _read_path(event_payload, "event.message.chat_id"),
        _read_path(event_payload, "message.chat_id"),
    )
    if not message_id or not chat_id:
        return None

    raw_content = _first_non_empty(
        _read_path(event_payload, "event.message.content"),
        _read_path(event_payload, "message.content"),
    )
    if not isinstance(raw_content, str):
        return None

    text = parse_message_text(raw_content).strip()
    if not text:
        return None

    open_id = _first_non_empty(
        _read_path(event_payload, "event.sender.sender_id.open_id"),
        _read_path(event_payload, "sender.sender_id.open_id"),
    )

    return FeishuTextMessage(
        message_id=message_id,
        chat_id=chat_id,
        open_id=open_id,
        text=text,
    )


class FeishuEventProcessor:
    def __init__(
        self,
        *,
        agent: AgentLike,
        send_text: Callable[[str, str], None],
        send_reaction: Callable[[str, str], None],
        logger: logging.Logger,
        allowed_open_ids: set[str] | None = None,
        deduplicator: MessageDeduplicator | None = None,
        send_retry_count: int = DEFAULT_FEISHU_SEND_RETRY_COUNT,
        send_retry_backoff_seconds: float = DEFAULT_FEISHU_SEND_RETRY_BACKOFF_SECONDS,
        text_chunk_size: int = DEFAULT_FEISHU_TEXT_CHUNK_SIZE,
        ack_reaction_enabled: bool = DEFAULT_FEISHU_ACK_REACTION_ENABLED,
        ack_emoji_type: str = DEFAULT_FEISHU_ACK_EMOJI_TYPE,
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

    def set_send_text(self, send_text: Callable[[str, str], None]) -> None:
        self._send_text = send_text

    def set_send_reaction(self, send_reaction: Callable[[str, str], None]) -> None:
        self._send_reaction = send_reaction

    def handle_event(self, event_payload: Any) -> None:
        message = extract_text_message(event_payload)
        if message is None:
            return

        if self._allowed_open_ids and message.open_id not in self._allowed_open_ids:
            self._logger.info("feishu event dropped: open_id not allowed")
            return

        if self._deduplicator.seen(message.message_id):
            self._logger.info("feishu event dropped: duplicate message_id=%s", message.message_id)
            return

        if self._ack_reaction_enabled:
            try:
                self._send_reaction_with_retry(message_id=message.message_id, emoji_type=self._ack_emoji_type)
                self._logger.info(
                    "feishu ack reaction sent: message_id=%s emoji=%s",
                    message.message_id,
                    self._ack_emoji_type,
                )
            except Exception:  # noqa: BLE001
                self._logger.warning(
                    "feishu ack reaction failed: message_id=%s emoji=%s",
                    message.message_id,
                    self._ack_emoji_type,
                    exc_info=True,
                )

        try:
            response_text = self._agent.handle_input(message.text)
        except Exception as exc:  # noqa: BLE001
            self._logger.exception("feishu event handle failed: %s", exc)
            response_text = "处理失败，请稍后重试。"

        payload_text = (response_text or "").strip() or "收到。"
        semantic_messages = split_semantic_messages(payload_text)

        for message_index, semantic_message in enumerate(semantic_messages, start=1):
            chunks = split_text_chunks(semantic_message, chunk_size=self._text_chunk_size)
            for chunk_index, chunk in enumerate(chunks, start=1):
                self._send_with_retry(chat_id=message.chat_id, text=chunk)
                self._logger.info(
                    "feishu response sent: message_id=%s message=%s/%s chunk=%s/%s",
                    message.message_id,
                    message_index,
                    len(semantic_messages),
                    chunk_index,
                    len(chunks),
                )

    def _send_with_retry(self, *, chat_id: str, text: str) -> None:
        self._run_with_retry(lambda: self._send_text(chat_id, text))

    def _send_reaction_with_retry(self, *, message_id: str, emoji_type: str) -> None:
        self._run_with_retry(lambda: self._send_reaction(message_id, emoji_type))

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

    def _run(self) -> None:
        try:
            lark = self._sdk_module
            if lark is None:
                import lark_oapi as lark  # type: ignore[import-not-found]

            api_client = lark.Client.builder().app_id(self._app_id).app_secret(self._app_secret).build()

            def send_text(chat_id: str, text: str) -> None:
                self._send_text_message(api_client=api_client, chat_id=chat_id, text=text)

            def send_reaction(message_id: str, emoji_type: str) -> None:
                self._send_ack_reaction(api_client=api_client, message_id=message_id, emoji_type=emoji_type)

            self._event_processor.set_send_text(send_text)
            self._event_processor.set_send_reaction(send_reaction)

            event_handler = (
                lark.EventDispatcherHandler.builder("", "")
                .register_p2_im_message_receive_v1(self._event_processor.handle_event)
                .build()
            )

            self._ws_client = lark.ws.Client(
                self._app_id,
                self._app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.DEBUG,
            )
            self._logger.info("feishu long connection started")
            self._ws_client.start()
        except ImportError:
            self._logger.exception("lark_oapi 未安装，无法启动飞书长连接")
        except Exception:  # noqa: BLE001
            self._logger.exception("飞书长连接运行失败")

    @staticmethod
    def _send_text_message(*, api_client: Any, chat_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import (  # type: ignore[import-not-found]
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

        success = getattr(response, "success", None)
        if callable(success):
            if success():
                return
            code = getattr(response, "code", "unknown")
            msg = getattr(response, "msg", "")
            raise RuntimeError(f"send message failed: code={code}, msg={msg}")

        code = _read_path(response, "code")
        if code not in (None, 0):
            msg = _read_path(response, "msg")
            raise RuntimeError(f"send message failed: code={code}, msg={msg}")

    @staticmethod
    def _send_ack_reaction(*, api_client: Any, message_id: str, emoji_type: str) -> None:
        from lark_oapi.api.im.v1 import (  # type: ignore[import-not-found]
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
        success = getattr(response, "success", None)
        if callable(success):
            if success():
                return
            code = getattr(response, "code", "unknown")
            msg = getattr(response, "msg", "")
            raise RuntimeError(f"send reaction failed: code={code}, msg={msg}")

        code = _read_path(response, "code")
        if code not in (None, 0):
            msg = _read_path(response, "msg")
            raise RuntimeError(f"send reaction failed: code={code}, msg={msg}")


def create_feishu_runner(
    *,
    app_id: str,
    app_secret: str,
    agent: AgentLike,
    logger: logging.Logger,
    allowed_open_ids: set[str] | None,
    send_retry_count: int,
    text_chunk_size: int,
    dedup_ttl_seconds: int,
    ack_reaction_enabled: bool,
    ack_emoji_type: str,
) -> FeishuLongConnectionRunner:
    # The send function is replaced once SDK client is ready in FeishuLongConnectionRunner._run().
    processor = FeishuEventProcessor(
        agent=agent,
        send_text=lambda _chat_id, _text: None,
        send_reaction=lambda _message_id, _emoji_type: None,
        logger=logger,
        allowed_open_ids=allowed_open_ids,
        deduplicator=MessageDeduplicator(ttl_seconds=dedup_ttl_seconds),
        send_retry_count=send_retry_count,
        text_chunk_size=text_chunk_size,
        ack_reaction_enabled=ack_reaction_enabled,
        ack_emoji_type=ack_emoji_type,
    )
    return FeishuLongConnectionRunner(
        app_id=app_id,
        app_secret=app_secret,
        event_processor=processor,
        logger=logger,
    )


def _read_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
            continue
        current = getattr(current, part, None)
    return current


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None
