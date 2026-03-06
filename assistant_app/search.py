from __future__ import annotations

import html
import json
import logging
import re
from typing import Any, Protocol
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from pydantic import ValidationError

from assistant_app.schemas.domain import SearchResult, WebPageFetchResult


class SearchProvider(Protocol):
    def search(self, query: str, top_k: int = 3) -> list[SearchResult]: ...


DEFAULT_SEARCH_TIMEOUT_SECONDS = 8.0
DEFAULT_WEB_FETCH_TIMEOUT_SECONDS = 15.0
DEFAULT_WEB_FETCH_MAX_TEXT_CHARS = 10000
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


def fetch_webpage_main_text(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_WEB_FETCH_TIMEOUT_SECONDS,
    max_chars: int = DEFAULT_WEB_FETCH_MAX_TEXT_CHARS,
) -> WebPageFetchResult:
    normalized_url = _normalize_fetch_url(url)
    if not normalized_url:
        raise ValueError("url 非法，需为 http:// 或 https:// 开头。")

    try:
        return _fetch_webpage_main_text_via_playwright(
            normalized_url,
            timeout_seconds=timeout_seconds,
            max_chars=max_chars,
        )
    except Exception as playwright_exc:  # noqa: BLE001
        try:
            return _fetch_webpage_main_text_via_requests(
                normalized_url,
                timeout_seconds=timeout_seconds,
                max_chars=max_chars,
            )
        except Exception as requests_exc:  # noqa: BLE001
            raise RuntimeError(
                f"网页抓取失败（Playwright+HTTP fallback）: playwright={playwright_exc}; requests={requests_exc}"
            ) from requests_exc


def _fetch_webpage_main_text_via_playwright(
    normalized_url: str,
    *,
    timeout_seconds: float,
    max_chars: int,
) -> WebPageFetchResult:
    sync_playwright = _load_sync_playwright()
    timeout_ms = max(int(timeout_seconds * 1000), 1000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(normalized_url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.evaluate("() => window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
            page.wait_for_timeout(300)
            raw_main_text = page.evaluate(
                "() => (document.body && document.body.innerText) ? document.body.innerText : ''"
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Playwright 抓取失败: {exc}") from exc
        finally:
            browser.close()

    main_text = _normalize_main_text(raw_main_text, max_chars=max_chars)
    return WebPageFetchResult.model_validate({"url": normalized_url, "main_text": main_text})


def _fetch_webpage_main_text_via_requests(
    normalized_url: str,
    *,
    timeout_seconds: float,
    max_chars: int,
) -> WebPageFetchResult:
    requests_module = _load_requests_module()
    try:
        response = requests_module.get(
            normalized_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CLI-AI-Assistant/0.1)"},
            timeout=timeout_seconds,
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"HTTP 抓取失败: {exc}") from exc

    response_url = _normalize_fetch_url(getattr(response, "url", "") or normalized_url) or normalized_url
    raw_main_text = _extract_text_from_html(str(getattr(response, "text", "") or ""))
    main_text = _normalize_main_text(raw_main_text, max_chars=max_chars)
    return WebPageFetchResult.model_validate({"url": response_url, "main_text": main_text})


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
        result = _build_search_result(title=title, snippet=snippet, url=url)
        if result is None:
            continue
        results.append(result)
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
        result = _build_search_result(title=title, snippet="", url=url)
        if result is None:
            continue
        results.append(result)
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
        result = _build_search_result(title=title, snippet=snippet, url=url)
        if result is None:
            continue
        results.append(result)
        seen_urls.add(url)
    return results


def _build_search_result(*, title: str, snippet: str, url: str) -> SearchResult | None:
    try:
        return SearchResult.model_validate({"title": title, "snippet": snippet, "url": url})
    except ValidationError:
        return None


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


def _normalize_fetch_url(url: str) -> str:
    candidate = str(url or "").strip()
    if not candidate:
        return ""
    parsed = urllib_parse.urlparse(candidate)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc:
        return ""
    return candidate


def _load_sync_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Playwright 依赖缺失。请先安装 playwright 并执行 playwright install chromium。"
        ) from exc
    return sync_playwright


def _load_requests_module() -> Any:
    try:
        import requests  # type: ignore[import-untyped]
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("requests 依赖缺失。请先安装 requests。") from exc
    return requests


def _extract_text_from_html(html_text: str) -> str:
    if not html_text:
        return ""
    body_match = re.search(r"<body[^>]*>(.*?)</body>", html_text, flags=re.IGNORECASE | re.DOTALL)
    body_html = body_match.group(1) if body_match else html_text
    cleaned = re.sub(
        r"<(script|style|noscript|svg)[^>]*>.*?</\1>",
        " ",
        body_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    cleaned = re.sub(
        r"</(p|div|li|section|article|tr|h1|h2|h3|h4|h5|h6)>",
        "\n",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned, flags=re.DOTALL)
    return html.unescape(cleaned)


def _normalize_main_text(raw_text: Any, *, max_chars: int) -> str:
    if not isinstance(raw_text, str):
        return ""
    text = raw_text.replace("\u00a0", " ")
    text = re.sub(r"\r\n?", "\n", text)
    normalized_lines = [line.strip() for line in text.split("\n")]
    normalized = "\n".join(line for line in normalized_lines if line)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized).strip()
    if max_chars <= 0:
        return ""
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars]


def _text_preview(text: str, limit: int = 80) -> str:
    normalized = text.strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."
