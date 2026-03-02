from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from typing import Protocol
from urllib import parse as urllib_parse
from urllib import request as urllib_request


@dataclass(frozen=True)
class SearchResult:
    title: str
    snippet: str
    url: str


class SearchProvider(Protocol):
    def search(self, query: str, top_k: int = 3) -> list[SearchResult]: ...


DEFAULT_SEARCH_TIMEOUT_SECONDS = 8.0
BOCHA_ENDPOINT = "https://api.bochaai.com/v1/web-search"
BOCHA_MAX_COUNT = 50
BOCHA_RERANK_MODEL = "gte-rerank"
_SEARCH_LOGGER = logging.getLogger("assistant_app.app")


class BingSearchProvider:
    def __init__(self, timeout: float = DEFAULT_SEARCH_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        normalized_query = _normalize_query(query)
        if not normalized_query or top_k <= 0:
            return []
        params = urllib_parse.urlencode({"q": normalized_query, "setlang": "zh-Hans"})
        req = urllib_request.Request(
            f"https://www.bing.com/search?{params}",
            headers={"User-Agent": "Mozilla/5.0 (compatible; CLI-AI-Assistant/0.1)"},
        )
        with urllib_request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="ignore")
        return _extract_bing_results(body, top_k=top_k)


class BochaSearchProvider:
    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = BOCHA_ENDPOINT,
        timeout: float = DEFAULT_SEARCH_TIMEOUT_SECONDS,
        summary: bool = True,
    ) -> None:
        self.api_key = api_key.strip()
        self.endpoint = endpoint.strip() or BOCHA_ENDPOINT
        self.timeout = timeout
        self.summary = summary

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        normalized_query = _normalize_query(query)
        if not normalized_query or top_k <= 0:
            return []

        log_context = {
            "query_preview": _text_preview(normalized_query),
            "query_length": len(normalized_query),
            "top_k": max(top_k, 1),
            "count": BOCHA_MAX_COUNT,
            "summary": self.summary,
            "rerank_model": BOCHA_RERANK_MODEL,
        }
        _SEARCH_LOGGER.info(
            "internet_search_rerank_start",
            extra={"event": "internet_search_rerank_start", "context": log_context},
        )
        try:
            parsed = self._request_search(
                query=normalized_query,
                top_k=top_k,
                use_reranker=True,
            )
            if not isinstance(parsed, dict):
                raise ValueError("bocha rerank response is not a JSON object")
            rerank_results = _extract_bocha_results(parsed)
            _SEARCH_LOGGER.info(
                "internet_search_rerank_done",
                extra={
                    "event": "internet_search_rerank_done",
                    "context": {**log_context, "result_count": len(rerank_results)},
                },
            )
            return rerank_results
        except Exception as rerank_exc:  # noqa: BLE001
            _SEARCH_LOGGER.warning(
                "internet_search_rerank_failed_fallback",
                extra={
                    "event": "internet_search_rerank_failed_fallback",
                    "context": {**log_context, "error": repr(rerank_exc)},
                },
            )

        _SEARCH_LOGGER.info(
            "internet_search_fallback_start",
            extra={"event": "internet_search_fallback_start", "context": log_context},
        )
        try:
            parsed = self._request_search(
                query=normalized_query,
                top_k=top_k,
                use_reranker=False,
            )
        except Exception as fallback_exc:  # noqa: BLE001
            _SEARCH_LOGGER.warning(
                "internet_search_fallback_failed",
                extra={
                    "event": "internet_search_fallback_failed",
                    "context": {**log_context, "error": repr(fallback_exc)},
                },
            )
            raise

        if not isinstance(parsed, dict):
            _SEARCH_LOGGER.warning(
                "internet_search_fallback_invalid_response",
                extra={
                    "event": "internet_search_fallback_invalid_response",
                    "context": {**log_context, "response_type": type(parsed).__name__},
                },
            )
            return []

        fallback_results = _extract_bocha_results(parsed)
        _SEARCH_LOGGER.info(
            "internet_search_fallback_done",
            extra={
                "event": "internet_search_fallback_done",
                "context": {**log_context, "result_count": len(fallback_results)},
            },
        )
        return fallback_results

    def _request_search(self, *, query: str, top_k: int, use_reranker: bool) -> object:
        request_context = {
            "query_preview": _text_preview(query),
            "query_length": len(query),
            "top_k": max(top_k, 1),
            "count": BOCHA_MAX_COUNT,
            "use_reranker": use_reranker,
            "endpoint": self.endpoint,
        }
        _SEARCH_LOGGER.info(
            "internet_search_bocha_request_start",
            extra={"event": "internet_search_bocha_request_start", "context": request_context},
        )
        payload = self._build_payload(query=query, top_k=top_k, use_reranker=use_reranker)
        req = urllib_request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; CLI-AI-Assistant/0.1)",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                status_code = int(getattr(resp, "status", 200))
                body = resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:  # noqa: BLE001
            _SEARCH_LOGGER.warning(
                "internet_search_bocha_request_failed",
                extra={
                    "event": "internet_search_bocha_request_failed",
                    "context": {**request_context, "error": repr(exc)},
                },
            )
            raise
        _SEARCH_LOGGER.info(
            "internet_search_bocha_request_done",
            extra={
                "event": "internet_search_bocha_request_done",
                "context": {
                    **request_context,
                    "status_code": status_code,
                    "response_size": len(body),
                },
            },
        )
        return json.loads(body)

    def _build_payload(self, *, query: str, top_k: int, use_reranker: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "query": query,
            "summary": self.summary,
            "count": BOCHA_MAX_COUNT,
        }
        if use_reranker:
            payload["reranker"] = {
                "enable": True,
                "apiKey": self.api_key,
                "rerankTopK": max(top_k, 1),
                "rerankModel": BOCHA_RERANK_MODEL,
            }
        return payload


def create_search_provider(
    *,
    provider_name: str,
    bocha_api_key: str | None,
    bocha_summary: bool = True,
    timeout: float = DEFAULT_SEARCH_TIMEOUT_SECONDS,
) -> SearchProvider:
    normalized_provider = provider_name.strip().lower()
    if normalized_provider in {"bocha", "bochaai"}:
        if bocha_api_key and bocha_api_key.strip():
            return BochaSearchProvider(api_key=bocha_api_key, timeout=timeout, summary=bocha_summary)
        # Keep CLI usable when BOCHA_API_KEY is not configured yet.
        return BingSearchProvider(timeout=timeout)
    return BingSearchProvider(timeout=timeout)


def _extract_bing_results(html_text: str, top_k: int = 3) -> list[SearchResult]:
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    algo_blocks = re.findall(r'<li class="b_algo".*?</li>', html_text, flags=re.IGNORECASE | re.DOTALL)
    for block in algo_blocks:
        link_match = re.search(
            r"<h2>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>\s*</h2>",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if link_match is None:
            continue
        url = html.unescape(link_match.group(1)).strip()
        if not _is_valid_result_url(url) or url in seen_urls:
            continue
        title = _clean_html_text(link_match.group(2))
        snippet_match = re.search(
            r'<div class="b_caption"[^>]*>.*?<p[^>]*>(.*?)</p>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippet = _clean_html_text(snippet_match.group(1)) if snippet_match else ""
        if not title:
            continue
        results.append(SearchResult(title=title, snippet=snippet, url=url))
        seen_urls.add(url)
        if len(results) >= top_k:
            return results

    for link_match in re.finditer(
        r"<a[^>]*href=\"(https?://[^\"]+)\"[^>]*>(.*?)</a>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        url = html.unescape(link_match.group(1)).strip()
        if not _is_valid_result_url(url) or url in seen_urls:
            continue
        title = _clean_html_text(link_match.group(2))
        if not title:
            continue
        results.append(SearchResult(title=title, snippet="", url=url))
        seen_urls.add(url)
        if len(results) >= top_k:
            break

    return results


def _extract_bocha_results(payload: dict[str, object]) -> list[SearchResult]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    web_pages = data.get("webPages")
    if not isinstance(web_pages, dict):
        return []
    raw_values = web_pages.get("value")
    if not isinstance(raw_values, list):
        return []

    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    for item in raw_values:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not _is_valid_result_url(url) or url in seen_urls:
            continue
        title = str(item.get("name") or "").strip()
        if not title:
            continue
        snippet = _bocha_snippet(item)
        results.append(SearchResult(title=title, snippet=snippet, url=url))
        seen_urls.add(url)
    return results


def _is_valid_result_url(url: str) -> bool:
    lower = url.lower()
    if not lower.startswith(("http://", "https://")):
        return False
    if "bing.com/search" in lower:
        return False
    return True


def _clean_html_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return " ".join(text.split()).strip()


def _bocha_snippet(item: dict[str, object]) -> str:
    summary = item.get("summary")
    if isinstance(summary, str):
        summary_text = summary.strip()
        if summary_text:
            return summary_text
    if isinstance(summary, list):
        parts = []
        for part in summary:
            if isinstance(part, str):
                cleaned = part.strip()
                if cleaned:
                    parts.append(cleaned)
            elif isinstance(part, dict):
                text = str(part.get("text") or "").strip()
                if text:
                    parts.append(text)
        summary_text = " ".join(parts).strip()
        if summary_text:
            return summary_text

    snippet = str(item.get("snippet") or "").strip()
    if snippet:
        return snippet
    return ""


def _normalize_query(query: str) -> str:
    return " ".join(query.strip().split())


def _text_preview(text: str, limit: int = 80) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."
