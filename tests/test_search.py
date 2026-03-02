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
    _extract_bocha_results,
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


if __name__ == "__main__":
    unittest.main()
