from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from assistant_app.search import (
    BOCHA_ENDPOINT,
    BOCHA_MAX_COUNT,
    BOCHA_RERANK_MODEL,
    BingSearchProvider,
    BochaSearchProvider,
    WebPageFetchResult,
    _extract_text_from_html,
    _extract_bocha_results,
    _fetch_webpage_main_text_via_requests,
    fetch_webpage_main_text,
    create_search_provider,
)


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # type: ignore[no-untyped-def]
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")


class _FakeRequestsResponse:
    def __init__(self, *, text: str, url: str = "https://example.com/final") -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


class SearchProviderTest(unittest.TestCase):
    def test_extract_bocha_results_uses_summary_then_snippet_without_local_top_k_truncation(self) -> None:
        payload = {
            "data": {
                "webPages": {
                    "value": [
                        {
                            "name": "A",
                            "url": "https://example.com/a",
                            "summary": "summary-a",
                            "snippet": "snippet-a",
                        },
                        {"name": "B", "url": "https://example.com/b", "summary": ["x", "y"]},
                        {"name": "C", "url": "https://example.com/c", "snippet": "snippet-c"},
                        {"name": "D", "url": "https://example.com/d", "snippet": "snippet-d"},
                    ]
                }
            }
        }

        results = _extract_bocha_results(payload)

        self.assertEqual(len(results), 4)
        self.assertEqual(results[0].title, "A")
        self.assertEqual(results[0].snippet, "summary-a")
        self.assertEqual(results[1].title, "B")
        self.assertEqual(results[1].snippet, "x y")
        self.assertEqual(results[2].title, "C")
        self.assertEqual(results[2].snippet, "snippet-c")
        self.assertEqual(results[3].title, "D")
        self.assertEqual(results[3].snippet, "snippet-d")

    def test_create_search_provider_bocha_without_key_falls_back_to_bing(self) -> None:
        provider = create_search_provider(provider_name="bocha", bocha_api_key=None)
        self.assertIsInstance(provider, BingSearchProvider)

    def test_create_search_provider_bocha_with_key_returns_bocha(self) -> None:
        provider = create_search_provider(provider_name="bocha", bocha_api_key="demo-key")
        self.assertIsInstance(provider, BochaSearchProvider)

    def test_bocha_provider_builds_post_request_with_reranker_enabled_by_default(self) -> None:
        provider = BochaSearchProvider(api_key="demo-key")
        payload = {
            "code": 200,
            "data": {
                "webPages": {
                    "value": [
                        {"name": "Title", "url": "https://example.com/item", "snippet": "text"},
                    ]
                }
            },
        }

        with patch("assistant_app.search.urllib_request.urlopen", return_value=_FakeHTTPResponse(payload)) as mocked:
            results_small_top_k = provider.search("  bocha   api ", top_k=1)
            results_large_top_k = provider.search("bocha api", top_k=999)

        self.assertEqual(len(results_small_top_k), 1)
        self.assertEqual(len(results_large_top_k), 1)
        self.assertEqual(len(mocked.call_args_list), 2)

        first_req = mocked.call_args_list[0].args[0]
        second_req = mocked.call_args_list[1].args[0]
        self.assertEqual(first_req.full_url, BOCHA_ENDPOINT)
        self.assertEqual(first_req.get_method(), "POST")
        self.assertEqual(second_req.full_url, BOCHA_ENDPOINT)
        self.assertEqual(second_req.get_method(), "POST")

        first_body = json.loads(first_req.data.decode("utf-8"))
        second_body = json.loads(second_req.data.decode("utf-8"))
        self.assertEqual(first_body["query"], "bocha api")
        self.assertEqual(second_body["query"], "bocha api")
        self.assertEqual(first_body["count"], BOCHA_MAX_COUNT)
        self.assertEqual(second_body["count"], BOCHA_MAX_COUNT)
        self.assertTrue(first_body["summary"])
        self.assertTrue(second_body["summary"])
        self.assertEqual(first_body["reranker"]["enable"], True)
        self.assertEqual(second_body["reranker"]["enable"], True)
        self.assertEqual(first_body["reranker"]["apiKey"], "demo-key")
        self.assertEqual(second_body["reranker"]["apiKey"], "demo-key")
        self.assertEqual(first_body["reranker"]["rerankTopK"], 1)
        self.assertEqual(second_body["reranker"]["rerankTopK"], 999)
        self.assertEqual(first_body["reranker"]["rerankModel"], BOCHA_RERANK_MODEL)
        self.assertEqual(second_body["reranker"]["rerankModel"], BOCHA_RERANK_MODEL)

        first_timeout = mocked.call_args_list[0].kwargs["timeout"]
        second_timeout = mocked.call_args_list[1].kwargs["timeout"]
        self.assertEqual(first_timeout, provider.timeout)
        self.assertEqual(second_timeout, provider.timeout)

    def test_bocha_provider_retries_without_reranker_when_rerank_request_fails(self) -> None:
        provider = BochaSearchProvider(api_key="demo-key")
        payload = {
            "code": 200,
            "data": {
                "webPages": {
                    "value": [
                        {"name": "Title", "url": "https://example.com/item", "snippet": "text"},
                    ]
                }
            },
        }

        with patch(
            "assistant_app.search.urllib_request.urlopen",
            side_effect=[RuntimeError("rerank failed"), _FakeHTTPResponse(payload)],
        ) as mocked:
            results = provider.search("bocha api", top_k=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(len(mocked.call_args_list), 2)

        first_body = json.loads(mocked.call_args_list[0].args[0].data.decode("utf-8"))
        second_body = json.loads(mocked.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertIn("reranker", first_body)
        self.assertNotIn("reranker", second_body)
        self.assertEqual(first_body["count"], BOCHA_MAX_COUNT)
        self.assertEqual(second_body["count"], BOCHA_MAX_COUNT)

    def test_bocha_provider_logs_rerank_request_start_and_done(self) -> None:
        provider = BochaSearchProvider(api_key="demo-key")
        payload = {
            "code": 200,
            "data": {
                "webPages": {
                    "value": [
                        {"name": "Title", "url": "https://example.com/item", "snippet": "text"},
                    ]
                }
            },
        }

        with patch("assistant_app.search.urllib_request.urlopen", return_value=_FakeHTTPResponse(payload)):
            with self.assertLogs("assistant_app.app", level="INFO") as captured:
                provider.search("bocha api", top_k=3)

        merged = "\n".join(captured.output)
        self.assertIn("internet_search_rerank_start", merged)
        self.assertIn("internet_search_bocha_request_start", merged)
        self.assertIn("internet_search_bocha_request_done", merged)
        self.assertIn("internet_search_rerank_done", merged)

    def test_bocha_provider_logs_fallback_when_rerank_fails(self) -> None:
        provider = BochaSearchProvider(api_key="demo-key")
        payload = {
            "code": 200,
            "data": {
                "webPages": {
                    "value": [
                        {"name": "Title", "url": "https://example.com/item", "snippet": "text"},
                    ]
                }
            },
        }

        with patch(
            "assistant_app.search.urllib_request.urlopen",
            side_effect=[RuntimeError("rerank failed"), _FakeHTTPResponse(payload)],
        ):
            with self.assertLogs("assistant_app.app", level="INFO") as captured:
                provider.search("bocha api", top_k=5)

        merged = "\n".join(captured.output)
        self.assertIn("internet_search_rerank_start", merged)
        self.assertIn("internet_search_rerank_failed_fallback", merged)
        self.assertIn("internet_search_fallback_start", merged)
        self.assertIn("internet_search_fallback_done", merged)

    def test_fetch_webpage_main_text_falls_back_to_requests_when_playwright_fails(self) -> None:
        expected = WebPageFetchResult(url="https://example.com/final", main_text="fallback body")
        with patch(
            "assistant_app.search._fetch_webpage_main_text_via_playwright",
            side_effect=RuntimeError("playwright failed"),
        ) as mocked_playwright:
            with patch(
                "assistant_app.search._fetch_webpage_main_text_via_requests",
                return_value=expected,
            ) as mocked_requests:
                result = fetch_webpage_main_text("https://example.com")

        self.assertEqual(result, expected)
        mocked_playwright.assert_called_once()
        mocked_requests.assert_called_once()

    def test_fetch_webpage_main_text_raises_when_playwright_and_requests_both_fail(self) -> None:
        with patch(
            "assistant_app.search._fetch_webpage_main_text_via_playwright",
            side_effect=RuntimeError("pw fail"),
        ):
            with patch(
                "assistant_app.search._fetch_webpage_main_text_via_requests",
                side_effect=RuntimeError("requests fail"),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    fetch_webpage_main_text("https://example.com")

        self.assertIn("Playwright+HTTP fallback", str(ctx.exception))
        self.assertIn("pw fail", str(ctx.exception))
        self.assertIn("requests fail", str(ctx.exception))

    def test_fetch_webpage_main_text_via_requests_extracts_main_text(self) -> None:
        html = """
        <html>
          <head><title>T</title><style>.x{}</style></head>
          <body>
            <h1>标题</h1>
            <p>第一段</p>
            <script>var a = 1;</script>
            <div>第二段<br/>第三行</div>
          </body>
        </html>
        """
        fake_requests = type(
            "FakeRequestsModule",
            (),
            {"get": lambda *args, **kwargs: _FakeRequestsResponse(text=html, url="https://example.com/doc")},
        )
        with patch("assistant_app.search._load_requests_module", return_value=fake_requests):
            result = _fetch_webpage_main_text_via_requests(
                "https://example.com",
                timeout_seconds=5.0,
                max_chars=300,
            )

        self.assertEqual(result.url, "https://example.com/doc")
        self.assertIn("标题", result.main_text)
        self.assertIn("第一段", result.main_text)
        self.assertIn("第二段", result.main_text)
        self.assertIn("第三行", result.main_text)
        self.assertNotIn("var a = 1", result.main_text)

    def test_extract_text_from_html_removes_script_style_and_keeps_visible_text(self) -> None:
        text = _extract_text_from_html(
            "<html><body><style>.x{}</style><h2>H</h2><p>A</p><script>alert(1)</script><div>B</div></body></html>"
        )
        self.assertIn("H", text)
        self.assertIn("A", text)
        self.assertIn("B", text)
        self.assertNotIn("alert(1)", text)


if __name__ == "__main__":
    unittest.main()
