from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from assistant_app.llm import OpenAICompatibleClient
from assistant_app.schemas.llm import parse_chat_completion_response


class OpenAICompatibleClientTest(unittest.TestCase):
    def test_reply_uses_default_temperature_when_not_specified(self) -> None:
        client = OpenAICompatibleClient(
            api_key="test-key",
            base_url="https://api.example.com",
            model="test-model",
        )
        messages = [{"role": "user", "content": "你好"}]

        with patch.object(client, "_create_reply", return_value="ok") as mock_create:
            result = client.reply(messages)

        self.assertEqual(result, "ok")
        mock_create.assert_called_once_with(messages=messages, temperature=1.3)

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
                            "name": "schedule",
                            "description": "执行 schedule",
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
                        "function": {"name": "schedule", "arguments": "{\"command\":\"/schedule list\"}"},
                    }
                ],
                "reasoning_content": None,
            }
        )

        self.assertEqual(payload.assistant_message.role, "assistant")
        self.assertIsNone(payload.assistant_message.content)
        self.assertEqual(payload.assistant_message.tool_calls[0].function.name, "schedule")
        self.assertIsNone(payload.reasoning_content)

    def test_build_tool_reply_payload_accepts_object_tool_calls(self) -> None:
        payload = OpenAICompatibleClient._build_tool_reply_payload(
            SimpleNamespace(
                role="assistant",
                content=None,
                tool_calls=[
                    SimpleNamespace(
                        id="call_2",
                        type="function",
                        function=SimpleNamespace(name="history_list", arguments='{"limit":5}'),
                    )
                ],
                reasoning_content=None,
            )
        )

        tool_call = payload.assistant_message.tool_calls[0]
        self.assertEqual(tool_call.function.name, "history_list")
        self.assertEqual(tool_call.function.arguments, '{"limit":5}')

    def test_build_tool_reply_payload_filters_invalid_tool_calls(self) -> None:
        payload = OpenAICompatibleClient._build_tool_reply_payload(
            {
                "role": "assistant",
                "content": "ok",
                "tool_calls": [
                    {"id": "broken", "type": "function"},
                    {
                        "id": "call_3",
                        "type": "function",
                        "function": {"name": "done", "arguments": '{"response":"完成"}'},
                    },
                ],
                "reasoning_content": None,
            }
        )

        self.assertEqual(len(payload.assistant_message.tool_calls), 1)
        self.assertEqual(payload.assistant_message.tool_calls[0].function.name, "done")

    def test_parse_chat_completion_response_accepts_legacy_dict_response(self) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": " hello ",
                        "tool_calls": None,
                    }
                }
            ]
        }

        parsed = parse_chat_completion_response(response)

        self.assertEqual(parsed.first_message().role, "assistant")
        self.assertEqual(parsed.first_message().content_text(), "hello")
        self.assertEqual(parsed.first_message().tool_calls, [])

    def test_parse_chat_completion_response_accepts_object_response(self) -> None:
        response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        role="assistant",
                        content=None,
                        tool_calls=[
                            SimpleNamespace(
                                id="call_9",
                                type="function",
                                function=SimpleNamespace(name="history_list", arguments={"limit": 3}),
                            )
                        ],
                    )
                )
            ]
        )

        parsed = parse_chat_completion_response(response)
        tool_call = parsed.first_message().tool_calls[0]

        self.assertEqual(tool_call.function.name, "history_list")
        self.assertEqual(tool_call.function.arguments, '{"limit":3}')


if __name__ == "__main__":
    unittest.main()
