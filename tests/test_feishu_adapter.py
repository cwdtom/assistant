from __future__ import annotations

import logging
import threading
import time
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
        response: str = "任务处理完成。",
        task_completed: bool = True,
    ) -> None:
        self.progress_result = progress_result
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
            callback(self.progress_result)
        return self.response, self.task_completed

    def emit_progress_result(self, result: str) -> None:
        callback = self._subtask_result_callback
        if callable(callback):
            callback(result)


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

        self._wait_until(lambda: len(agent.inputs) == 1 and len(sent) == 3 and len(reactions) == 1)
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

        self._wait_until(lambda: len(reactions) == 1 and len(sent) == 2)
        self.assertEqual(reactions, [("om_2", "OK")])
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
        self.assertEqual(reactions, [("om_done", "OK"), ("om_done", "DONE")])
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
        self.assertEqual(reactions, [("om_custom_done", "OK"), ("om_custom_done", "CHECKMARK")])

    def test_event_processor_async_subtask_progress_rewrites_then_sends(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        rewrite_calls: list[str] = []
        agent = _ProgressReportingTaskAwareAgent(
            progress_result="执行结果：已添加待办 #1",
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
            and ("oc_1", "润色后：执行结果：已添加待办 #1") in sent
            and ("oc_1", "任务处理完成。") in sent
        )
        self.assertEqual(rewrite_calls, ["执行结果：已添加待办 #1"])

    def test_event_processor_async_subtask_progress_send_failure_drops_without_retry(self) -> None:
        sent: list[tuple[str, str]] = []
        reactions: list[tuple[str, str]] = []
        progress_attempts = {"count": 0}
        agent = _ProgressReportingTaskAwareAgent(
            progress_result="执行结果：已添加待办 #1",
            response="最终消息",
            task_completed=True,
        )

        def send_text(chat_id: str, text: str) -> None:
            if text == "润色后：执行结果：已添加待办 #1":
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

        agent.emit_progress_result("执行结果：已添加待办 #1")
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
                ("om_busy_1", "OK"),
                ("om_busy_2", "OK"),
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
        self.assertEqual(reactions, [("om_ack_1", "OK")])

        agent.release_first_call.set()
        first_thread.join(timeout=2.0)
        self._wait_until(lambda: len(reactions) == 3 and len(sent) == 1)

        self.assertEqual(
            reactions,
            [
                ("om_ack_1", "OK"),
                ("om_ack_2", "OK"),
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
                ("om_chat_a_1", "OK"),
                ("om_chat_b_1", "OK"),
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
                ("om_abort_1", "OK"),
                ("om_abort_2", "OK"),
                ("om_abort_2", "DONE"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
