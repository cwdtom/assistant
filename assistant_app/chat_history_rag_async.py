from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib import import_module
from typing import Any


@dataclass(frozen=True)
class ChatHistoryInsertEvent:
    chat_id: int
    user_content: str
    assistant_content: str
    created_at: str


class AsyncChatHistoryRagIndexer:
    def __init__(
        self,
        *,
        rag_db_path: str = "sqliterag.sqlite",
        logger: logging.Logger | None = None,
    ) -> None:
        self._rag_db_path = rag_db_path
        self._logger = logger or logging.getLogger("assistant_app.app")
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chat-history-rag")
        self._sqlite_rag_factory = self._resolve_sqlite_rag_factory()
        self._rag_instance: Any | None = None
        self._closed = False

    def enqueue(self, event: ChatHistoryInsertEvent) -> None:
        if self._closed:
            return
        if self._sqlite_rag_factory is None:
            return
        uri = _build_chat_history_uri(event.chat_id)
        self._logger.info(
            "chat history rag enqueue",
            extra={
                "event": "chat_history_rag_enqueue",
                "context": {
                    "chat_id": event.chat_id,
                    "uri": uri,
                    "rag_db_path": self._rag_db_path,
                },
            },
        )
        try:
            self._executor.submit(self._index_event, event)
        except Exception:  # noqa: BLE001
            self._logger.warning(
                "chat history rag index failed",
                extra={
                    "event": "chat_history_rag_index_failed",
                    "context": {
                        "chat_id": event.chat_id,
                        "uri": uri,
                        "error": "submit_failed",
                    },
                },
                exc_info=True,
            )

    def close(self, *, wait: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=wait, cancel_futures=not wait)
        self._close_rag_instance()

    def _resolve_sqlite_rag_factory(self) -> Any | None:
        try:
            sqlite_rag_module = import_module("sqlite_rag")
            sqlite_rag_class = sqlite_rag_module.SQLiteRag
        except Exception as exc:  # noqa: BLE001
            self._logger.info(
                "chat history rag unavailable",
                extra={
                    "event": "chat_history_rag_unavailable",
                    "context": {
                        "reason": repr(exc),
                        "rag_db_path": self._rag_db_path,
                    },
                },
            )
            return None

        def _factory() -> Any:
            return sqlite_rag_class.create(db_path=self._rag_db_path, require_existing=False)

        return _factory

    def _index_event(self, event: ChatHistoryInsertEvent) -> None:
        uri = _build_chat_history_uri(event.chat_id)
        metadata = {
            "source": "chat_history",
            "chat_id": event.chat_id,
            "created_at": event.created_at,
        }
        content = _build_chat_history_content(event)
        try:
            rag = self._get_or_create_rag_instance()
            if rag is None:
                return
            rag.add_text(text=content, uri=uri, metadata=metadata)
            self._logger.info(
                "chat history rag index success",
                extra={
                    "event": "chat_history_rag_index_success",
                    "context": {
                        "chat_id": event.chat_id,
                        "uri": uri,
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "chat history rag index failed",
                extra={
                    "event": "chat_history_rag_index_failed",
                    "context": {
                        "chat_id": event.chat_id,
                        "uri": uri,
                        "error": repr(exc),
                    },
                },
                exc_info=True,
            )
            self._close_rag_instance()

    def _get_or_create_rag_instance(self) -> Any | None:
        if self._sqlite_rag_factory is None:
            return None
        if self._rag_instance is not None:
            return self._rag_instance
        self._rag_instance = self._sqlite_rag_factory()
        return self._rag_instance

    def _close_rag_instance(self) -> None:
        rag = self._rag_instance
        self._rag_instance = None
        if rag is None:
            return
        close = getattr(rag, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                self._logger.warning(
                    "chat history rag close failed",
                    extra={"event": "chat_history_rag_close_failed"},
                    exc_info=True,
                )


def _build_chat_history_uri(chat_id: int) -> str:
    return f"assistant://chat_history/{chat_id}"


def _build_chat_history_content(event: ChatHistoryInsertEvent) -> str:
    return "\n".join(
        (
            f"chat_id: {event.chat_id}",
            f"用户: {event.user_content}",
            f"助手: {event.assistant_content}",
        )
    )
