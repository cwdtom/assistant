from __future__ import annotations

import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from assistant_app.db import AssistantDB
from assistant_app.proactive_reminder_service import ProactiveReminderService
from assistant_app.search import SearchProvider, SearchResult


class _FakeSearchProvider(SearchProvider):
    def search(self, query: str, *, top_k: int = 5):  # type: ignore[no-untyped-def]
        return [SearchResult(title=f"{query}-1", snippet="snippet", url="https://example.com")][:top_k]


class _FakeLLM:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self._payloads = list(payloads)
        self.calls: list[dict[str, object]] = []

    def reply_with_tools(self, messages, *, tools, tool_choice="auto"):  # type: ignore[no-untyped-def]
        self.calls.append({"messages": messages, "tools": tools, "tool_choice": tool_choice})
        if not self._payloads:
            raise RuntimeError("no payload")
        return self._payloads.pop(0)


def _done_payload(*, should_send: bool, message: str) -> dict[str, object]:
    return {
        "assistant_message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_done",
                    "type": "function",
                    "function": {
                        "name": "done",
                        "arguments": json.dumps(
                            {
                                "should_send": should_send,
                                "message": message,
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }
    }


class ProactiveReminderServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "assistant_test.db")
        self.db = AssistantDB(self.db_path)
        self.now = datetime(2026, 3, 5, 9, 0, 0)
        self.sent: list[tuple[str, str]] = []

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_poll_scheduled_sends_segments_without_persisting_chat_history_when_llm_requests_send(self) -> None:
        profile_path = Path(self.tmp.name) / "profile.md"
        profile_path.write_text("用户偏好：晨会前提醒", encoding="utf-8")
        llm = _FakeLLM(
            [
                _done_payload(
                    should_send=True,
                    message="第一条提醒\n\n第二条提醒",
                )
            ]
        )
        clock_now = {"value": self.now}
        service = ProactiveReminderService(
            db=self.db,
            llm_client=llm,
            search_provider=_FakeSearchProvider(),
            logger=logging.getLogger("test.proactive_service.success"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            lookahead_hours=24,
            interval_minutes=60,
            night_quiet_hint="23:00-08:00",
            max_steps=20,
            user_profile_path=str(profile_path),
            internet_search_top_k=3,
            final_content_rewriter=lambda text: text,
            clock=lambda: clock_now["value"],
        )

        service.poll_scheduled()

        self.assertEqual(self.sent, [("ou_target", "第一条提醒"), ("ou_target", "第二条提醒")])
        self.assertEqual(self.db.recent_turns(limit=5), [])
        first_call_messages = llm.calls[0]["messages"]
        self.assertIn("用户偏好：晨会前提醒", first_call_messages[1]["content"])

    def test_poll_scheduled_skips_send_when_llm_decides_not_to_send(self) -> None:
        llm = _FakeLLM([_done_payload(should_send=False, message="提醒")])
        service = ProactiveReminderService(
            db=self.db,
            llm_client=llm,
            search_provider=_FakeSearchProvider(),
            logger=logging.getLogger("test.proactive_service.low_score"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            lookahead_hours=24,
            interval_minutes=60,
            night_quiet_hint="23:00-08:00",
            max_steps=1,
            user_profile_path="",
            internet_search_top_k=3,
            clock=lambda: self.now,
        )

        with self.assertLogs("test.proactive_service.low_score", level="INFO") as captured:
            service.poll_scheduled()

        self.assertEqual(self.sent, [])
        self.assertEqual(self.db.recent_turns(limit=5), [])
        self.assertTrue(
            any(
                "proactive decision decided: should_send=False" in item
                for item in captured.output
            )
        )

    def test_poll_scheduled_skips_when_not_due(self) -> None:
        llm = _FakeLLM([_done_payload(should_send=True, message="提醒")])
        clock_now = {"value": self.now}
        service = ProactiveReminderService(
            db=self.db,
            llm_client=llm,
            search_provider=_FakeSearchProvider(),
            logger=logging.getLogger("test.proactive_service.due"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            lookahead_hours=24,
            interval_minutes=60,
            night_quiet_hint="23:00-08:00",
            max_steps=20,
            user_profile_path="",
            internet_search_top_k=3,
            clock=lambda: clock_now["value"],
        )

        service.poll_scheduled()
        clock_now["value"] = self.now + timedelta(minutes=30)
        service.poll_scheduled()

        self.assertEqual(self.sent, [("ou_target", "提醒")])
        self.assertEqual(len(llm.calls), 1)

    def test_poll_scheduled_profile_read_failure_falls_back(self) -> None:
        missing_profile = Path(self.tmp.name) / "missing_profile.md"
        llm = _FakeLLM([_done_payload(should_send=False, message="")])
        service = ProactiveReminderService(
            db=self.db,
            llm_client=llm,
            search_provider=_FakeSearchProvider(),
            logger=logging.getLogger("test.proactive_service.profile_fallback"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            lookahead_hours=24,
            interval_minutes=60,
            night_quiet_hint="23:00-08:00",
            max_steps=20,
            user_profile_path=str(missing_profile),
            internet_search_top_k=3,
            clock=lambda: self.now,
        )

        with self.assertLogs("test.proactive_service.profile_fallback", level="WARNING") as captured:
            service.poll_scheduled()

        self.assertEqual(self.sent, [])
        self.assertTrue(any("failed to read proactive user profile" in item for item in captured.output))
        first_call_messages = llm.calls[0]["messages"]
        self.assertIn('"loaded": false', first_call_messages[1]["content"])

    def test_poll_scheduled_rejects_blank_message_when_llm_requests_send(self) -> None:
        llm = _FakeLLM([_done_payload(should_send=True, message=" ")])
        service = ProactiveReminderService(
            db=self.db,
            llm_client=llm,
            search_provider=_FakeSearchProvider(),
            logger=logging.getLogger("test.proactive_service.invalid_send"),
            target_open_id="ou_target",
            send_text_to_open_id=lambda open_id, text: self.sent.append((open_id, text)),
            lookahead_hours=24,
            interval_minutes=60,
            night_quiet_hint="23:00-08:00",
            max_steps=1,
            user_profile_path="",
            internet_search_top_k=3,
            clock=lambda: self.now,
        )

        with self.assertLogs("test.proactive_service.invalid_send", level="WARNING") as captured:
            service.poll_scheduled()

        self.assertEqual(self.sent, [])
        self.assertTrue(any("proactive react invalid done payload" in item for item in captured.output))

if __name__ == "__main__":
    unittest.main()
