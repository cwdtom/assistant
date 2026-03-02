from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from assistant_app.search import (
    BOCHA_ENDPOINT,
    BOCHA_MAX_COUNT,
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
    def test_extract_bocha_results_uses_summary_then_snippet(self) -> None:
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
                    ]
                }
            }
        }

        results = _extract_bocha_results(payload, top_k=3)

        self.assertEqual(len(results), 3)
        self.assertEqual(results[0].title, "A")
        self.assertEqual(results[0].snippet, "summary-a")
        self.assertEqual(results[1].title, "B")
        self.assertEqual(results[1].snippet, "x y")
        self.assertEqual(results[2].title, "C")
        self.assertEqual(results[2].snippet, "snippet-c")

    def test_create_search_provider_bocha_without_key_falls_back_to_bing(self) -> None:
        provider = create_search_provider(provider_name="bocha", bocha_api_key=None)
        self.assertIsInstance(provider, BingSearchProvider)

    def test_create_search_provider_bocha_with_key_returns_bocha(self) -> None:
        provider = create_search_provider(provider_name="bocha", bocha_api_key="demo-key")
        self.assertIsInstance(provider, BochaSearchProvider)

    def test_bocha_provider_builds_post_request_with_fixed_count(self) -> None:
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

        first_timeout = mocked.call_args_list[0].kwargs["timeout"]
        second_timeout = mocked.call_args_list[1].kwargs["timeout"]
        self.assertEqual(first_timeout, provider.timeout)
        self.assertEqual(second_timeout, provider.timeout)


if __name__ == "__main__":
    unittest.main()
