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
    def test_extract_bocha_results_uses_snippet_then_summary(self) -> None:
        payload = {
            "data": {
                "webPages": {
                    "value": [
                        {"name": "A", "url": "https://example.com/a", "snippet": "snippet-a"},
                        {"name": "B", "url": "https://example.com/b", "summary": ["x", "y"]},
                        {"name": "A-dup", "url": "https://example.com/a", "snippet": "ignored"},
                    ]
                }
            }
        }

        results = _extract_bocha_results(payload, top_k=3)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].title, "A")
        self.assertEqual(results[0].snippet, "snippet-a")
        self.assertEqual(results[1].title, "B")
        self.assertEqual(results[1].snippet, "x y")

    def test_create_search_provider_bocha_without_key_falls_back_to_bing(self) -> None:
        provider = create_search_provider(provider_name="bocha", bocha_api_key=None)
        self.assertIsInstance(provider, BingSearchProvider)

    def test_create_search_provider_bocha_with_key_returns_bocha(self) -> None:
        provider = create_search_provider(provider_name="bocha", bocha_api_key="demo-key")
        self.assertIsInstance(provider, BochaSearchProvider)

    def test_bocha_provider_builds_post_request_and_clamps_count(self) -> None:
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
            results = provider.search("  bocha   api ", top_k=999)

        self.assertEqual(len(results), 1)
        req = mocked.call_args.args[0]
        self.assertEqual(req.full_url, BOCHA_ENDPOINT)
        self.assertEqual(req.get_method(), "POST")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["query"], "bocha api")
        self.assertEqual(body["count"], BOCHA_MAX_COUNT)
        self.assertTrue(body["summary"])
        self.assertEqual(mocked.call_args.kwargs["timeout"], provider.timeout)


if __name__ == "__main__":
    unittest.main()
