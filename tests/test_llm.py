from __future__ import annotations

import unittest
from unittest.mock import patch

from assistant_app.llm import OpenAICompatibleClient


class OpenAICompatibleClientTest(unittest.TestCase):
    def test_reply_uses_configured_temperature(self) -> None:
        client = OpenAICompatibleClient(
            api_key="test-key",
            base_url="https://api.example.com",
            model="test-model",
            temperature=0.8,
        )
        messages = [{"role": "user", "content": "你好"}]

        with patch.object(client, "_create_reply", return_value="ok") as mock_create:
            result = client.reply(messages)

        self.assertEqual(result, "ok")
        mock_create.assert_called_once_with(messages=messages, temperature=0.8)

    def test_reply_json_uses_configured_temperature_with_json_object_mode(self) -> None:
        client = OpenAICompatibleClient(
            api_key="test-key",
            base_url="https://api.example.com",
            model="test-model",
            temperature=0.6,
        )
        messages = [{"role": "user", "content": "请输出 JSON"}]

        with patch.object(client, "_create_reply", return_value='{"ok": true}') as mock_create:
            result = client.reply_json(messages)

        self.assertEqual(result, '{"ok": true}')
        mock_create.assert_called_once_with(
            messages=messages,
            temperature=0.6,
            response_format={"type": "json_object"},
        )


if __name__ == "__main__":
    unittest.main()
