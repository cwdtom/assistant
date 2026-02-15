from __future__ import annotations

import html
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


class BingSearchProvider:
    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def search(self, query: str, top_k: int = 3) -> list[SearchResult]:
        normalized_query = " ".join(query.strip().split())
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
