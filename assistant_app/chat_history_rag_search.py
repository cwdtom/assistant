from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal

_CHAT_HISTORY_URI_PATTERN = re.compile(r"^assistant://chat_history/(\d+)$")


@dataclass(frozen=True)
class ChatHistoryRagQueryResult:
    status: Literal["hit", "unavailable", "error", "empty"]
    chat_ids: list[int]


class ChatHistoryRagSearcher:
    def __init__(
        self,
        *,
        rag_db_path: str = "sqliterag.sqlite",
        logger: logging.Logger | None = None,
    ) -> None:
        self._rag_db_path = rag_db_path
        self._logger = logger or logging.getLogger("assistant_app.app")
        self._sqlite_rag_factory = self._resolve_sqlite_rag_factory()
        self._rag_instance: Any | None = None
        self._closed = False

    @property
    def available(self) -> bool:
        return self._sqlite_rag_factory is not None and not self._closed

    def search_chat_ids(self, *, keyword: str, limit: int) -> ChatHistoryRagQueryResult:
        normalized_keyword = keyword.strip()
        normalized_limit = max(limit, 1)
        if not normalized_keyword:
            return ChatHistoryRagQueryResult(status="empty", chat_ids=[])
        if not self.available:
            return ChatHistoryRagQueryResult(status="unavailable", chat_ids=[])

        try:
            rag = self._get_or_create_rag_instance()
            if rag is None:
                return ChatHistoryRagQueryResult(status="unavailable", chat_ids=[])
            results = rag.search(query=normalized_keyword, top_k=normalized_limit, new_context=True)
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "chat history rag search failed",
                extra={
                    "event": "chat_history_rag_search_failed",
                    "context": {
                        "error": repr(exc),
                        "keyword_length": len(normalized_keyword),
                        "limit": normalized_limit,
                    },
                },
                exc_info=True,
            )
            self._close_rag_instance()
            return ChatHistoryRagQueryResult(status="error", chat_ids=[])

        chat_ids = _extract_chat_ids(results=results, limit=normalized_limit)
        if not chat_ids:
            return ChatHistoryRagQueryResult(status="empty", chat_ids=[])
        return ChatHistoryRagQueryResult(status="hit", chat_ids=chat_ids)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._close_rag_instance()

    def _resolve_sqlite_rag_factory(self) -> Any | None:
        try:
            sqlite_rag_module = import_module("sqlite_rag")
            sqlite_rag_class = sqlite_rag_module.SQLiteRag
        except Exception as exc:  # noqa: BLE001
            self._logger.info(
                "chat history rag search unavailable",
                extra={
                    "event": "chat_history_rag_search_unavailable",
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
                    "chat history rag search close failed",
                    extra={"event": "chat_history_rag_search_close_failed"},
                    exc_info=True,
                )


def _extract_chat_ids(*, results: Any, limit: int) -> list[int]:
    if limit <= 0:
        return []
    normalized_ids: list[int] = []
    seen_ids: set[int] = set()

    for item in results or []:
        chat_id = _extract_chat_id_from_result(item)
        if chat_id is None or chat_id in seen_ids:
            continue
        normalized_ids.append(chat_id)
        seen_ids.add(chat_id)
        if len(normalized_ids) >= limit:
            break
    return normalized_ids


def _extract_chat_id_from_result(result: Any) -> int | None:
    document = getattr(result, "document", None)
    metadata = getattr(document, "metadata", None)
    if isinstance(metadata, dict):
        chat_id = _normalize_chat_id(metadata.get("chat_id"))
        if chat_id is not None:
            return chat_id

    uri = getattr(document, "uri", None)
    if isinstance(uri, str):
        return _chat_id_from_uri(uri)

    return None


def _normalize_chat_id(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            parsed = int(text)
            return parsed if parsed > 0 else None
    return None


def _chat_id_from_uri(uri: str) -> int | None:
    matched = _CHAT_HISTORY_URI_PATTERN.match(uri.strip())
    if matched is None:
        return None
    return int(matched.group(1))
