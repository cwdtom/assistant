from __future__ import annotations

import io
import json
import logging
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

try:
    from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
except ImportError:  # pragma: no cover - optional dependency in some environments
    P2ImMessageReceiveV1 = None

from assistant_app.feishu_adapter import (
    FeishuEventProcessor,
    FeishuLongConnectionRunner,
    MessageDeduplicator,
    _mask_log_text,
    _mask_open_id,
    extract_text_message,
    parse_message_text,
    split_semantic_messages,
    split_text_chunks,
)
from assistant_app.logging_setup import JsonLinesFormatter


class _FakeAgent:
    def __init__(self, response: str = "") -> None:
        self.response = response
        self.inputs: list[str] = []

    def handle_input(self, user_input: str) -> str:
        self.inputs.append(user_input)
        return self.response


class _TaskAwareFakeAgent(_FakeAgent):
    def __init__(self, response: str = "", *, task_completed: bool = False) -> None:
        super().__init__(response=response)
        self.task_completed = task_completed

    def handle_input_with_task_status(self, user_input: str) -> tuple[str, bool]:
        response = self.handle_input(user_input)
        return response, self.task_completed


class _InterruptibleTaskAwareAgent:
    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.interrupt_calls = 0
        self.first_call_started = threading.Event()
        self._first_call_interrupted = threading.Event()

    def handle_input_with_task_status(self, user_input: str) -> tuple[str, bool]:
        self.inputs.append(user_input)
        if len(self.inputs) == 1:
            self.first_call_started.set()
            self._first_call_interrupted.wait(timeout=2.0)
            return "第一条已中断", False
        return "合并任务完成", True

    def interrupt_current_task(self) -> None:
        self.interrupt_calls += 1
        self._first_call_interrupted.set()


class _SlowInterruptibleTaskAwareAgent:
    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.interrupt_calls = 0
        self.first_call_started = threading.Event()
        self.release_first_call = threading.Event()

    def handle_input_with_task_status(self, user_input: str) -> tuple[str, bool]:
        self.inputs.append(user_input)
        if len(self.inputs) == 1:
            self.first_call_started.set()
            self.release_first_call.wait(timeout=2.0)
            return "第一条完成", False
        return "合并任务完成", True

    def interrupt_current_task(self) -> None:
        self.interrupt_calls += 1


class _ChunkedResponseAgent:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def handle_input_with_task_status(self, user_input: str) -> tuple[str, bool]:
        self.inputs.append(user_input)
        if len(self.inputs) == 1:
            return "abcdef", False
        return "合并任务完成", True

    def interrupt_current_task(self) -> None:
        return


class _ProgressReportingTaskAwareAgent:
    def __init__(
        self,
        *,
        progress_result: str = "执行结果",
        progress_results: list[str] | None = None,
        response: str = "任务处理完成。",
        task_completed: bool = True,
    ) -> None:
        self.progress_result = progress_result
        self.progress_results = list(progress_results or [])
        self.response = response
        self.task_completed = task_completed
        self.inputs: list[str] = []
        self._subtask_result_callback = None

    def set_subtask_result_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        self._subtask_result_callback = callback

    def handle_input_with_task_status(self, user_input: str) -> tuple[str, bool]:
        self.inputs.append(user_input)
        callback = self._subtask_result_callback
        if callable(callback):
            if self.progress_results:
                for item in self.progress_results:
                    callback(item)
            else:
                callback(self.progress_result)
        return self.response, self.task_completed

    def emit_progress_result(self, result: str) -> None:
        callback = self._subtask_result_callback
        if callable(callback):
            callback(result)


class _RecordListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        with self._lock:
            self.records.append(record)

    def messages(self) -> list[str]:
        with self._lock:
            return [record.getMessage() for record in self.records]


class _FakeImMessageAPI:
    def __init__(self, response) -> None:  # type: ignore[no-untyped-def]
        self.response = response
        self.requests = []

    def create(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.response


class _FakeImReactionAPI:
    def __init__(self, response) -> None:  # type: ignore[no-untyped-def]
        self.response = response
        self.requests = []

    def create(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.response


class _FakeImApiClient:
    def __init__(self, *, message_response, reaction_response=None) -> None:  # type: ignore[no-untyped-def]
        self.im = SimpleNamespace(
            v1=SimpleNamespace(
                message=_FakeImMessageAPI(message_response),
                message_reaction=_FakeImReactionAPI(
                    reaction_response if reaction_response is not None else message_response
                ),
            )
        )


class FeishuAdapterTest(unittest.TestCase):
    def _wait_until(self, predicate, *, timeout: float = 2.0) -> None:  # type: ignore[no-untyped-def]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("condition not met within timeout")

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

    def test_extract_post_message_keeps_raw_json_as_text(self) -> None:
        raw_content = (
            '{"zh_cn":{"title":"日报","content":[[{"tag":"text","text":"今天完成联调"}],'
            '[{"tag":"text","text":"明天继续"}]]}}'
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_2"}},
                "message": {
                    "message_type": "post",
                    "chat_type": "p2p",
                    "message_id": "om_2",
                    "chat_id": "oc_2",
                    "content": raw_content,
                },
            }
        }

        message = extract_text_message(payload)

        assert message is not None
        self.assertEqual(message.message_id, "om_2")
        self.assertEqual(message.chat_id, "oc_2")
        self.assertEqual(message.open_id, "ou_2")
        self.assertEqual(message.text, raw_content)

    def test_extract_text_message_supports_root_level_payload(self) -> None:
        payload = {
            "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_root"}},
            "message": {
                "message_type": "text",
                "chat_type": "p2p",
                "message_id": "om_root",
                "chat_id": "oc_root",
                "content": '{"text":"根级消息"}',
            },
        }

        message = extract_text_message(payload)

        assert message is not None
        self.assertEqual(message.message_id, "om_root")
        self.assertEqual(message.chat_id, "oc_root")
        self.assertEqual(message.open_id, "ou_root")
        self.assertEqual(message.text, "根级消息")

    def test_send_text_message_accepts_attribute_response_without_success_method(self) -> None:
        api_client = _FakeImApiClient(message_response=SimpleNamespace(code=0, msg="ok"))

        FeishuLongConnectionRunner._send_text_message(
            api_client=api_client,
            chat_id="oc_1",
            text="你好",
        )

        request = api_client.im.v1.message.requests[0]
        self.assertEqual(request.receive_id_type, "chat_id")
        self.assertEqual(request.request_body.receive_id, "oc_1")
        self.assertEqual(request.request_body.content, '{"text": "你好"}')

    def test_send_text_message_by_open_id_accepts_attribute_response_without_success_method(self) -> None:
        api_client = _FakeImApiClient(message_response=SimpleNamespace(code="0", msg="ok"))

        FeishuLongConnectionRunner._send_text_message_by_open_id(
            api_client=api_client,
            open_id="ou_1",
            text="主动提醒",
        )

        request = api_client.im.v1.message.requests[0]
        self.assertEqual(request.receive_id_type, "open_id")
        self.assertEqual(request.request_body.receive_id, "ou_1")
        self.assertEqual(request.request_body.content, '{"text": "主动提醒"}')

    def test_send_text_message_raises_for_attribute_response_error_code(self) -> None:
        api_client = _FakeImApiClient(message_response=SimpleNamespace(code="999", msg="bad request"))

        with self.assertRaisesRegex(RuntimeError, "send message failed: code=999, msg=bad request"):
            FeishuLongConnectionRunner._send_text_message(
                api_client=api_client,
                chat_id="oc_1",
                text="你好",
            )

    def test_send_ack_reaction_raises_for_attribute_response_error_code(self) -> None:
        api_client = _FakeImApiClient(
            message_response=SimpleNamespace(code=0, msg="ok"),
            reaction_response=SimpleNamespace(code="951", msg="rate limited"),
        )

        with self.assertRaisesRegex(RuntimeError, "send reaction failed: code=951, msg=rate limited"):
            FeishuLongConnectionRunner._send_ack_reaction(
                api_client=api_client,
                message_id="om_1",
                emoji_type="DONE",
            )

    def test_extract_text_message_skips_blank_json_text(self) -> None:
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_3"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_3",
                    "chat_id": "oc_3",
                    "content": '{"text":"   "}',
                },
            }
        }

        self.assertIsNone(extract_text_message(payload))

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

    def test_handle_event_logs_invalid_payload_reason(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("test.feishu_adapter.invalid_payload")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            logger.handlers.clear()
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = logging.StreamHandler(stream)
            handler.setFormatter(JsonLinesFormatter())
            logger.addHandler(handler)
            processor = FeishuEventProcessor(
                agent=_FakeAgent(),
                send_text=lambda _chat_id, _text: None,
                send_reaction=lambda _message_id, _emoji_type: None,
                logger=logger,
            )

            processor.handle_event(
                {
                    "event": {
                        "sender": {"sender_type": "user"},
                        "message": {
                            "message_type": "text",
                            "chat_type": "group",
                            "message_id": "om_group",
                            "chat_id": "oc_group",
                            "content": '{"text":"你好"}',
                        },
                    }
                }
            )

            records = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
            invalid_events = [item for item in records if item.get("event") == "feishu_event_payload_invalid"]
            self.assertEqual(len(invalid_events), 1)
            self.assertEqual(
                invalid_events[0].get("context"),
                {
                    "reason": "unsupported_chat_type",
                    "message_type": "text",
                    "chat_type": "group",
                },
            )
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_handle_event_valid_payload_does_not_log_invalid_event(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("test.feishu_adapter.valid_payload")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            logger.handlers.clear()
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = logging.StreamHandler(stream)
            handler.setFormatter(JsonLinesFormatter())
            logger.addHandler(handler)
            agent = _FakeAgent(response="ok")
            processor = FeishuEventProcessor(
                agent=agent,
                send_text=lambda _chat_id, _text: None,
                send_reaction=lambda _message_id, _emoji_type: None,
                logger=logger,
            )

            processor.handle_event(
                {
                    "event": {
                        "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_valid"}},
                        "message": {
                            "message_type": "text",
                            "chat_type": "p2p",
                            "message_id": "om_valid",
                            "chat_id": "oc_valid",
                            "content": '{"text":"你好"}',
                        },
                    }
                }
            )

            self._wait_until(lambda: len(agent.inputs) == 1)
            records = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
            events = [item.get("event") for item in records]
            self.assertNotIn("feishu_event_payload_invalid", events)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    @unittest.skipIf(P2ImMessageReceiveV1 is None, "lark_oapi not installed")
    def test_handle_event_accepts_lark_sdk_event_object(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("test.feishu_adapter.sdk_event")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            logger.handlers.clear()
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = logging.StreamHandler(stream)
            handler.setFormatter(JsonLinesFormatter())
            logger.addHandler(handler)
            agent = _FakeAgent(response="ok")
            processor = FeishuEventProcessor(
                agent=agent,
                send_text=lambda _chat_id, _text: None,
                send_reaction=lambda _message_id, _emoji_type: None,
                logger=logger,
            )

            payload = P2ImMessageReceiveV1(
                {
                    "event": {
                        "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_sdk"}},
                        "message": {
                            "message_type": "text",
                            "chat_type": "p2p",
                            "message_id": "om_sdk",
                            "chat_id": "oc_sdk",
                            "content": '{"text":"SDK 对象消息"}',
                        },
                    }
                }
            )

            processor.handle_event(payload)

            self._wait_until(lambda: len(agent.inputs) == 1)
            self.assertEqual(agent.inputs, ["SDK 对象消息"])
            records = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
            events = [item.get("event") for item in records]
            self.assertNotIn("feishu_event_payload_invalid", events)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

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

        self._wait_until(lambda: len(agent.inputs) == 1 and len(sent) == 3 and len(reactions) == 1)
        self.assertEqual(agent.inputs, ["安排下今天"])
        self.assertEqual(reactions, [("om_1", "Get")])
        self.assertEqual(sent, [("oc_1", "ab"), ("oc_1", "cd"), ("oc_1", "ef")])

    def test_event_processor_logs_received_and_sent_message_text(self) -> None:
        sent: list[tuple[str, str]] = []
        logger = logging.getLogger("test.feishu_adapter.message_text_logs")
        handler = _RecordListHandler()
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        agent = _FakeAgent(response="处理完成")
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda _message_id, _emoji_type: None,
            logger=logger,
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_log_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"请记录这条消息"}',
                },
            }
        }

        processor.handle_event(payload)

        masked_open_id = _mask_open_id("ou_1")
        masked_inbound_text = _mask_log_text("请记录这条消息")
        masked_sent_text = _mask_log_text("处理完成")
        inbound_log = (
            "feishu inbound message received: "
            f"message_id=om_log_1 chat_id=oc_1 open_id={masked_open_id} text={masked_inbound_text}"
        )
        sent_log = f"feishu response sent: message_id=om_log_1 message=1/1 chunk=1/1 text={masked_sent_text}"
        self._wait_until(
            lambda: any(
                inbound_log in message
                for message in handler.messages()
            )
            and any(
                sent_log in message
                for message in handler.messages()
            ),
            timeout=2.0,
        )
        messages = handler.messages()
        self.assertTrue(
            any(
                (
                    "feishu inbound message received: message_id=om_log_1 "
                    f"chat_id=oc_1 open_id={masked_open_id} text={masked_inbound_text}"
                )
                in message
                for message in messages
            )
        )
        self.assertTrue(
            any(
                (
                    "feishu response sent: message_id=om_log_1 "
                    f"message=1/1 chunk=1/1 text={masked_sent_text}"
                )
                in message
                for message in messages
            )
        )
        self.assertFalse(any("open_id=ou_1 text=请记录这条消息" in message for message in messages))
        self.assertEqual(sent, [("oc_1", "处理完成")])

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
            send_retry_backoff_seconds=0,
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

        self._wait_until(lambda: attempts["count"] == 4)
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

        self._wait_until(lambda: len(sent) == 1)
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
            text_chunk_size=5000,
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

        self._wait_until(lambda: len(reactions) == 1 and len(sent) == 2)
        self.assertEqual(reactions, [("om_2", "Get")])
        self.assertEqual(sent, [("oc_1", "先同步结论。"), ("oc_1", "补充下一步：今天 18:00 前完成。")])

    def test_event_processor_sends_done_reaction_when_task_completed(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        agent = _TaskAwareFakeAgent(response="任务处理完成。", task_completed=True)
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.done_reaction"),
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_done",
                    "chat_id": "oc_1",
                    "content": '{"text":"安排并给出结论"}',
                },
            }
        }

        processor.handle_event(payload)

        self._wait_until(lambda: len(agent.inputs) == 1 and len(reactions) == 2 and len(sent) == 1)
        self.assertEqual(agent.inputs, ["安排并给出结论"])
        self.assertEqual(reactions, [("om_done", "Get"), ("om_done", "DONE")])
        self.assertEqual(sent, [("oc_1", "任务处理完成。")])

    def test_event_processor_uses_configured_done_emoji_type(self) -> None:
        reactions: list[tuple[str, str]] = []
        agent = _TaskAwareFakeAgent(response="已完成。", task_completed=True)
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda _chat_id, _text: None,
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.custom_done_emoji"),
            done_emoji_type="CHECKMARK",
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_custom_done",
                    "chat_id": "oc_1",
                    "content": '{"text":"执行并结束"}',
                },
            }
        }

        processor.handle_event(payload)

        self._wait_until(lambda: len(reactions) == 2)
        self.assertEqual(reactions, [("om_custom_done", "Get"), ("om_custom_done", "CHECKMARK")])

    def test_event_processor_task_completed_without_text_skips_text_send(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        agent = _TaskAwareFakeAgent(response="", task_completed=True)
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.done_without_text"),
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_done_empty",
                    "chat_id": "oc_1",
                    "content": '{"text":"收到"}',
                },
            }
        }

        processor.handle_event(payload)

        self._wait_until(lambda: len(reactions) == 2)
        self.assertEqual(reactions, [("om_done_empty", "Get"), ("om_done_empty", "DONE")])
        self.assertEqual(sent, [])

    def test_event_processor_async_subtask_progress_rewrites_then_sends(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        rewrite_calls: list[str] = []
        agent = _ProgressReportingTaskAwareAgent(
            progress_result="执行结果：已添加日程 #1",
            response="任务处理完成。",
            task_completed=True,
        )
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.async_progress"),
            progress_content_rewriter=lambda text: rewrite_calls.append(text) or f"润色后：{text}",
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_progress_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"执行任务"}',
                },
            }
        }

        processor.handle_event(payload)

        self._wait_until(
            lambda: len(reactions) == 2
            and ("oc_1", "润色后：执行结果：已添加日程 #1") in sent
            and ("oc_1", "任务处理完成。") in sent
        )
        self.assertEqual(rewrite_calls, ["执行结果：已添加日程 #1"])

    def test_event_processor_async_subtask_progress_without_rewriter_sends_raw_text(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        agent = _ProgressReportingTaskAwareAgent(
            progress_result="执行结果：已添加日程 #1",
            response="任务处理完成。",
            task_completed=True,
        )
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.async_progress_raw"),
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_progress_raw_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"执行任务"}',
                },
            }
        }

        processor.handle_event(payload)

        self._wait_until(
            lambda: len(reactions) == 2
            and ("oc_1", "执行结果：已添加日程 #1") in sent
            and ("oc_1", "任务处理完成。") in sent
        )

    def test_event_processor_async_subtask_progress_sends_plan_goal_message(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        agent = _ProgressReportingTaskAwareAgent(
            progress_results=["任务目标：先整理今日日程，再给出结论", "执行结果：已整理日程"],
            response="任务处理完成。",
            task_completed=True,
        )
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.async_progress_goal"),
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_progress_goal_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"执行任务"}',
                },
            }
        }

        processor.handle_event(payload)

        self._wait_until(
            lambda: len(reactions) == 2
            and ("oc_1", "任务目标：先整理今日日程，再给出结论") in sent
            and ("oc_1", "执行结果：已整理日程") in sent
            and ("oc_1", "任务处理完成。") in sent
        )

    def test_event_processor_async_subtask_progress_send_failure_drops_without_retry(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        progress_attempts = {"count": 0}
        agent = _ProgressReportingTaskAwareAgent(
            progress_result="执行结果：已添加日程 #1",
            response="最终消息",
            task_completed=True,
        )

        def send_text(chat_id: str, text: str) -> None:
            if text == "润色后：执行结果：已添加日程 #1":
                progress_attempts["count"] += 1
                raise RuntimeError("progress send failed")
            sent.append((chat_id, text))

        processor = FeishuEventProcessor(
            agent=agent,
            send_text=send_text,
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.async_progress_drop"),
            progress_content_rewriter=lambda text: f"润色后：{text}",
            send_retry_count=3,
            send_retry_backoff_seconds=0,
        )
        payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_progress_2",
                    "chat_id": "oc_1",
                    "content": '{"text":"执行任务"}',
                },
            }
        }

        processor.handle_event(payload)

        self._wait_until(lambda: len(reactions) == 2 and ("oc_1", "最终消息") in sent)
        self._wait_until(lambda: progress_attempts["count"] == 1)
        self.assertEqual(progress_attempts["count"], 1)

    def test_event_processor_drops_subtask_progress_when_no_active_task(self) -> None:
        sent: list[tuple[str, str]] = []
        agent = _ProgressReportingTaskAwareAgent()
        FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda _message_id, _emoji_type: None,
            logger=logging.getLogger("test.feishu_adapter.drop_progress_without_active_task"),
            progress_content_rewriter=lambda text: f"润色后：{text}",
        )

        agent.emit_progress_result("执行结果：已添加日程 #1")
        time.sleep(0.05)

        self.assertEqual(sent, [])

    def test_event_processor_interrupts_and_merges_new_input_when_busy(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        agent = _InterruptibleTaskAwareAgent()
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.interrupt_merge"),
        )
        first_payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_busy_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"第一条需求"}',
                },
            }
        }
        second_payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_busy_2",
                    "chat_id": "oc_1",
                    "content": '{"text":"第二条补充"}',
                },
            }
        }

        first_thread = threading.Thread(target=processor.handle_event, args=(first_payload,))
        first_thread.start()
        self.assertTrue(agent.first_call_started.wait(timeout=2.0))
        processor.handle_event(second_payload)
        first_thread.join(timeout=2.0)
        self._wait_until(lambda: len(reactions) == 3 and len(sent) == 1 and len(agent.inputs) == 2)

        self.assertEqual(agent.interrupt_calls, 1)
        self.assertEqual(agent.inputs, ["第一条需求", "第一条需求\n第二条补充"])
        self.assertEqual(sent, [("oc_1", "合并任务完成")])
        self.assertEqual(
            reactions,
            [
                ("om_busy_1", "Get"),
                ("om_busy_2", "Get"),
                ("om_busy_2", "DONE"),
            ],
        )

    def test_event_processor_acks_second_message_when_merged_task_starts(self) -> None:
        reactions: list[tuple[str, str]] = []
        sent: list[tuple[str, str]] = []
        agent = _SlowInterruptibleTaskAwareAgent()
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.ack_when_merged_start"),
        )
        first_payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_ack_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"第一条需求"}',
                },
            }
        }
        second_payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_ack_2",
                    "chat_id": "oc_1",
                    "content": '{"text":"第二条补充"}',
                },
            }
        }

        first_thread = threading.Thread(target=processor.handle_event, args=(first_payload,))
        first_thread.start()
        self.assertTrue(agent.first_call_started.wait(timeout=2.0))
        processor.handle_event(second_payload)

        # 第二条不在“收到时”ACK，而是在合并任务真正开始执行时ACK。
        self.assertEqual(reactions, [("om_ack_1", "Get")])

        agent.release_first_call.set()
        first_thread.join(timeout=2.0)
        self._wait_until(lambda: len(reactions) == 3 and len(sent) == 1)

        self.assertEqual(
            reactions,
            [
                ("om_ack_1", "Get"),
                ("om_ack_2", "Get"),
                ("om_ack_2", "DONE"),
            ],
        )
        self.assertEqual(sent, [("oc_1", "合并任务完成")])

    def test_event_processor_keeps_chat_boundary_when_interrupted(self) -> None:
        reactions: list[tuple[str, str]] = []
        sent: list[tuple[str, str]] = []
        agent = _SlowInterruptibleTaskAwareAgent()
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda chat_id, text: sent.append((chat_id, text)),
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.chat_boundary"),
        )
        first_payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_chat_a_1",
                    "chat_id": "oc_chat_a",
                    "content": '{"text":"A 会话需求"}',
                },
            }
        }
        second_payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_2"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_chat_b_1",
                    "chat_id": "oc_chat_b",
                    "content": '{"text":"B 会话补充"}',
                },
            }
        }

        first_thread = threading.Thread(target=processor.handle_event, args=(first_payload,))
        first_thread.start()
        self.assertTrue(agent.first_call_started.wait(timeout=2.0))
        processor.handle_event(second_payload)
        agent.release_first_call.set()
        first_thread.join(timeout=2.0)

        self._wait_until(lambda: len(agent.inputs) == 2 and len(sent) == 1 and len(reactions) == 3)
        self.assertEqual(agent.inputs, ["A 会话需求", "B 会话补充"])
        self.assertEqual(sent, [("oc_chat_b", "合并任务完成")])
        self.assertEqual(
            reactions,
            [
                ("om_chat_a_1", "Get"),
                ("om_chat_b_1", "Get"),
                ("om_chat_b_1", "DONE"),
            ],
        )

    def test_event_processor_aborts_old_response_when_interrupted_during_send(self) -> None:
        reactions: list[tuple[str, str]] = []
        sent: list[tuple[str, str]] = []
        first_chunk_sent = threading.Event()
        release_first_chunk = threading.Event()
        agent = _ChunkedResponseAgent()

        def send_text(chat_id: str, text: str) -> None:
            sent.append((chat_id, text))
            if text == "ab" and not first_chunk_sent.is_set():
                first_chunk_sent.set()
                release_first_chunk.wait(timeout=2.0)

        processor = FeishuEventProcessor(
            agent=agent,
            send_text=send_text,
            send_reaction=lambda message_id, emoji_type: reactions.append((message_id, emoji_type)),
            logger=logging.getLogger("test.feishu_adapter.abort_during_send"),
            text_chunk_size=2,
        )
        first_payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_abort_1",
                    "chat_id": "oc_1",
                    "content": '{"text":"第一条需求"}',
                },
            }
        }
        second_payload = {
            "event": {
                "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_1"}},
                "message": {
                    "message_type": "text",
                    "chat_type": "p2p",
                    "message_id": "om_abort_2",
                    "chat_id": "oc_1",
                    "content": '{"text":"第二条补充"}',
                },
            }
        }

        first_thread = threading.Thread(target=processor.handle_event, args=(first_payload,))
        first_thread.start()
        self.assertTrue(first_chunk_sent.wait(timeout=2.0))
        processor.handle_event(second_payload)
        release_first_chunk.set()
        first_thread.join(timeout=2.0)
        self._wait_until(lambda: len(reactions) == 3 and len(sent) == 4 and len(agent.inputs) == 2)

        self.assertEqual(agent.inputs, ["第一条需求", "第一条需求\n第二条补充"])
        self.assertEqual(sent, [("oc_1", "ab"), ("oc_1", "合并"), ("oc_1", "任务"), ("oc_1", "完成")])
        self.assertEqual(
            reactions,
            [
                ("om_abort_1", "Get"),
                ("om_abort_2", "Get"),
                ("om_abort_2", "DONE"),
            ],
        )

    def test_feishu_runner_send_open_id_text_uses_open_id_sender(self) -> None:
        agent = _FakeAgent(response="ok")
        logger = logging.getLogger("test.feishu_adapter.open_id_send")
        handler = _RecordListHandler()
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda _chat_id, _text: None,
            send_reaction=lambda _message_id, _emoji_type: None,
            logger=logger,
        )
        runner = FeishuLongConnectionRunner(
            app_id="app_id",
            app_secret="app_secret",
            event_processor=processor,
            logger=logger,
            sdk_module=None,
        )
        sent: list[tuple[str, str]] = []
        runner._send_text_to_open_id = lambda open_id, text: sent.append((open_id, text))

        runner.send_open_id_text(open_id="ou_target", text="任务完成")

        self.assertEqual(sent, [("ou_target", "任务完成")])
        masked_open_id = _mask_open_id("ou_target")
        masked_text = _mask_log_text("任务完成")
        self.assertTrue(
            any(
                f"feishu open_id response sent: open_id={masked_open_id} text={masked_text}" in message
                for message in handler.messages()
            )
        )
        self.assertFalse(any("open_id=ou_target text=任务完成" in message for message in handler.messages()))

    def test_feishu_runner_send_open_id_text_requires_open_id_and_text(self) -> None:
        agent = _FakeAgent(response="ok")
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda _chat_id, _text: None,
            send_reaction=lambda _message_id, _emoji_type: None,
            logger=logging.getLogger("test.feishu_adapter.open_id_validation"),
        )
        runner = FeishuLongConnectionRunner(
            app_id="app_id",
            app_secret="app_secret",
            event_processor=processor,
            logger=logging.getLogger("test.feishu_adapter.open_id_validation"),
            sdk_module=None,
        )
        runner._send_text_to_open_id = lambda _open_id, _text: None

        with self.assertRaisesRegex(ValueError, "open_id is required"):
            runner.send_open_id_text(open_id=" ", text="任务完成")
        with self.assertRaisesRegex(ValueError, "text is required"):
            runner.send_open_id_text(open_id="ou_target", text="  ")

    def test_feishu_runner_send_open_id_text_strips_whitespace(self) -> None:
        agent = _FakeAgent(response="ok")
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda _chat_id, _text: None,
            send_reaction=lambda _message_id, _emoji_type: None,
            logger=logging.getLogger("test.feishu_adapter.open_id_strip"),
        )
        runner = FeishuLongConnectionRunner(
            app_id="app_id",
            app_secret="app_secret",
            event_processor=processor,
            logger=logging.getLogger("test.feishu_adapter.open_id_strip"),
            sdk_module=None,
        )
        sent: list[tuple[str, str]] = []
        runner._send_text_to_open_id = lambda open_id, text: sent.append((open_id, text))

        runner.send_open_id_text(open_id=" ou_target ", text=" 任务完成 ")

        self.assertEqual(sent, [("ou_target", "任务完成")])

    def test_feishu_runner_send_open_id_text_requires_sender_ready(self) -> None:
        agent = _FakeAgent(response="ok")
        processor = FeishuEventProcessor(
            agent=agent,
            send_text=lambda _chat_id, _text: None,
            send_reaction=lambda _message_id, _emoji_type: None,
            logger=logging.getLogger("test.feishu_adapter.open_id_not_ready"),
        )
        runner = FeishuLongConnectionRunner(
            app_id="app_id",
            app_secret="app_secret",
            event_processor=processor,
            logger=logging.getLogger("test.feishu_adapter.open_id_not_ready"),
            sdk_module=None,
        )

        with self.assertRaises(RuntimeError):
            runner.send_open_id_text(open_id="ou_target", text="任务完成")


if __name__ == "__main__":
    unittest.main()
