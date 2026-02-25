from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from assistant_app.feishu_adapter import (
    FeishuEventProcessor,
    MessageDeduplicator,
    extract_text_message,
    parse_message_text,
    split_semantic_messages,
    split_text_chunks,
)


class _FakeAgent:
    def __init__(self, response: str = "") -> None:
        self.response = response
        self.inputs: list[str] = []

    def handle_input(self, user_input: str) -> str:
        self.inputs.append(user_input)
        return self.response


class FeishuAdapterTest(unittest.TestCase):
    def test_parse_message_text_supports_json_and_plain_text(self) -> None:
        self.assertEqual(parse_message_text('{"text":"你好"}'), "你好")
        self.assertEqual(parse_message_text("纯文本"), "纯文本")

    def test_split_text_chunks_keeps_order(self) -> None:
        self.assertEqual(split_text_chunks("abcdef", chunk_size=2), ["ab", "cd", "ef"])

    def test_split_semantic_messages_uses_blank_line_separator(self) -> None:
        text = "第一条结果\n\n第二条结果\n\n\n第三条结果"
        self.assertEqual(split_semantic_messages(text), ["第一条结果", "第二条结果", "第三条结果"])

    def test_extract_text_message_from_event_payload(self) -> None:
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"你好"}',
                },
            }
        }

        message = extract_text_message(payload)

        assert message is not None
        self.assertEqual(message.message_id, "om_1")
        self.assertEqual(message.chat_id, "oc_1")
        self.assertEqual(message.open_id, "ou_1")
        self.assertEqual(message.text, "你好")

    def test_extract_text_message_skips_non_text_or_non_p2p(self) -> None:
        non_text = {
            "event": {
                "sender": {"sender_type": "user"},
                "message": {
                    "message_type": "image",
                    "chat_type": "p2p",
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "content": "{}",
                },
            }
        }
        group = {
            "event": {
                "sender": {"sender_type": "user"},
                "message": {
                    "message_type": "text",
                    "chat_type": "group",
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"hi"}',
                },
            }
        }

        self.assertIsNone(extract_text_message(non_text))
        self.assertIsNone(extract_text_message(group))

    def test_message_deduplicator_respects_ttl(self) -> None:
        deduper = MessageDeduplicator(ttl_seconds=10)

        with patch("assistant_app.feishu_adapter.time.monotonic", side_effect=[100.0, 105.0, 112.0]):
            self.assertFalse(deduper.seen("om_1"))
            self.assertTrue(deduper.seen("om_1"))
            self.assertFalse(deduper.seen("om_1"))

    def test_event_processor_filters_open_id(self) -> None:
        sent: list[tuple[str, str]] = []
        agent = _FakeAgent(response="ok")
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda _message_id, _emoji_type: None,
            logger=logging.getLogger("test.feishu_adapter.filter"),
            allowed_open_ids={"ou_allow"},
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_block"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"你好"}',
                },
            }
        }

        processor.handle_event(payload)

        self.assertEqual(agent.inputs, [])
        self.assertEqual(sent, [])

    def test_event_processor_handles_message_once_and_splits_output(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        agent = _FakeAgent(response="abcdef")
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.split"),
            text_chunk_size=2,
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"安排下今天"}',
                },
            }
        }

        processor.handle_event(payload)
        processor.handle_event(payload)

        self.assertEqual(agent.inputs, ["安排下今天"])
        self.assertEqual(reactions, [("om_1", "OK")])
        self.assertEqual(sent, [("oc_1", "ab"), ("oc_1", "cd"), ("oc_1", "ef")])

    def test_event_processor_retries_three_times_before_success(self) -> None:
        attempts = {"count": 0}

        def flaky_send(_chat_id: str, _text: str) -> None:
            attempts["count"] += 1
            if attempts["count"] <= 3:
                raise RuntimeError("send failed")

        agent = _FakeAgent(response="ok")
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=flaky_send,
            send_reaction=lambda _message_id, _emoji_type: None,
            logger=logging.getLogger("test.feishu_adapter.retry"),
            send_retry_count=3,
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"hello"}',
                },
            }
        }

        with patch("assistant_app.feishu_adapter.time.sleep", return_value=None):
            processor.handle_event(payload)

        self.assertEqual(attempts["count"], 4)

    def test_event_processor_can_disable_ack_reaction(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        agent = _FakeAgent(response="ok")
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.no_ack"),
            ack_reaction_enabled=False,
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"hello"}',
                },
            }
        }

        processor.handle_event(payload)

        self.assertEqual(reactions, [])
        self.assertEqual(sent, [("oc_1", "ok")])

    def test_event_processor_splits_semantic_messages_before_chunking(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        agent = _FakeAgent(response="先同步结论。\n\n补充下一步：今天 18:00 前完成。")
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.semantic_split"),
            text_chunk_size=1500,
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_2",
                    "chat_id": "oc_1",
                    "content": '{"text":"给我结果"}',
                },
            }
        }

        processor.handle_event(payload)

        self.assertEqual(reactions, [("om_2", "OK")])
        self.assertEqual(sent, [("oc_1", "先同步结论。"), ("oc_1", "补充下一步：今天 18:00 前完成。")])


if __name__ == "__main__":
    unittest.main()
