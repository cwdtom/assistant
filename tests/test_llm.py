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

    def test_reply_with_temperature_overrides_configured_temperature(self) -> None:
        client = OpenAICompatibleClient(
            api_key="test-key",
            base_url="https://api.example.com",
            model="test-model",
            temperature=0.6,
        )
        messages = [{"role": "user", "content": "请严格输出"}]

        with patch.object(client, "_create_reply", return_value="ok") as mock_create:
            result = client.reply_with_temperature(messages, temperature=0.0)

        self.assertEqual(result, "ok")
        mock_create.assert_called_once_with(messages=messages, temperature=0.0)

    def test_reply_with_tools_rejects_reasoner_model(self) -> None:
        client = OpenAICompatibleClient(
            api_key="test-key",
            base_url="https://api.example.com",
            model="deepseek-reasoner",
            temperature=0.3,
        )

        with self.assertRaises(RuntimeError) as ctx:
            client.reply_with_tools(
                messages=[{"role": "user", "content": "你好"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "todo",
                            "description": "执行 todo",
                            "parameters": {"type": "object", "properties": {}, "required": []},
                        },
                    }
                ],
            )

        self.assertIn("thought 阶段暂不支持 thinking 模式", str(ctx.exception))

    def test_build_tool_reply_payload_normalizes_tool_calls(self) -> None:
        payload = OpenAICompatibleClient._build_tool_reply_payload(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "todo", "arguments": "{\"command\":\"/todo list\"}"},
                    }
                ],
                "reasoning_content": None,
            }
        )

        self.assertIsInstance(payload.get("assistant_message"), dict)
        assistant_message = payload["assistant_message"]
        self.assertEqual(assistant_message.get("role"), "assistant")
        self.assertIsNone(assistant_message.get("content"))
        tool_calls = assistant_message.get("tool_calls")
        self.assertIsInstance(tool_calls, list)
        self.assertEqual(tool_calls[0]["function"]["name"], "todo")
        self.assertIsNone(payload.get("reasoning_content"))


if __name__ == "__main__":
    unittest.main()
