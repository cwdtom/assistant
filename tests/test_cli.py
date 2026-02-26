from __future__ import annotations

import io
import logging
import tempfile
import unittest
from pathlib import Path

from assistant_app.cli import (
    CLEAR_TERMINAL_SEQUENCE,
    _configure_app_logger,
    _clear_terminal_history,
    _configure_feishu_logger,
    _configure_llm_trace_logger,
    _exit_cli,
    _handle_input_with_feedback,
    _resolve_progress_color,
    _should_show_waiting,
)
from assistant_app.logging_setup import JsonLinesFormatter
from assistant_app.reminder_sink import ReminderEvent, StdoutReminderSink


class _FakeAgent:
    def __init__(self, llm_enabled: bool, delay: float = 0.0) -> None:
        self.llm_client = object() if llm_enabled else None
        self.progress_callback = None

    def handle_input(self, user_input: str) -> str:
        if self.progress_callback is not None and not user_input.startswith("/"):
            self.progress_callback("步骤进度：开始规划")
            self.progress_callback("计划列表：\n1. [待办] 添加待办\n2. [待办] 确认结果")
            self.progress_callback("完成情况：成功 0 步，失败 0 步，已执行 0/20 步。")
        return f"echo:{user_input}"

    def set_progress_callback(self, callback) -> None:  # type: ignore[no-untyped-def]
        self.progress_callback = callback


class CLIFeedbackTest(unittest.TestCase):
    def test_clear_terminal_history_writes_escape_sequence(self) -> None:
        stream = io.StringIO()
        _clear_terminal_history(stream=stream)
        self.assertEqual(stream.getvalue(), CLEAR_TERMINAL_SEQUENCE)

    def test_exit_cli_clears_screen_and_prints_exit_message(self) -> None:
        stream = io.StringIO()
        _exit_cli(stream=stream, with_leading_newline=True)
        self.assertEqual(stream.getvalue(), f"{CLEAR_TERMINAL_SEQUENCE}\n已退出。\n")

    def test_should_show_waiting_for_natural_language_with_llm(self) -> None:
        agent = _FakeAgent(llm_enabled=True)
        self.assertTrue(_should_show_waiting(agent, "看一下全部待办"))
        self.assertFalse(_should_show_waiting(agent, "/todo list"))
        self.assertFalse(_should_show_waiting(agent, ""))

    def test_should_not_show_waiting_without_llm(self) -> None:
        agent = _FakeAgent(llm_enabled=False)
        self.assertFalse(_should_show_waiting(agent, "今天怎么安排"))

    def test_handle_input_with_feedback_renders_progress_lines(self) -> None:
        agent = _FakeAgent(llm_enabled=True)
        stream = io.StringIO()

        result = _handle_input_with_feedback(agent, "看一下全部待办", stream=stream)

        self.assertEqual(result, "echo:看一下全部待办")
        self.assertIn("进度> 步骤进度：开始规划", stream.getvalue())
        self.assertIn("进度> 计划列表：", stream.getvalue())
        self.assertIn("进度> 完成情况：成功 0 步，失败 0 步，已执行 0/20 步。", stream.getvalue())

    def test_handle_input_with_feedback_skips_waiting_for_command(self) -> None:
        agent = _FakeAgent(llm_enabled=True)
        stream = io.StringIO()

        result = _handle_input_with_feedback(agent, "/todo list", stream=stream)

        self.assertEqual(result, "echo:/todo list")
        self.assertEqual(stream.getvalue(), "")

    def test_resolve_progress_color_off(self) -> None:
        prefix, suffix = _resolve_progress_color("off")
        self.assertEqual(prefix, "")
        self.assertEqual(suffix, "")

    def test_configure_llm_trace_logger_deduplicates_file_handler(self) -> None:
        logger = logging.getLogger("assistant_app.llm_trace")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            with tempfile.TemporaryDirectory() as tmp:
                path = str(Path(tmp) / "llm_trace.log")
                _configure_llm_trace_logger(path)
                _configure_llm_trace_logger(path)
                file_handlers = [handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)]
                self.assertEqual(len(file_handlers), 1)
                self.assertIsInstance(file_handlers[0].formatter, JsonLinesFormatter)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_configure_llm_trace_logger_empty_path_disables_output(self) -> None:
        logger = logging.getLogger("assistant_app.llm_trace")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            _configure_llm_trace_logger("   ")
            self.assertFalse(logger.propagate)
            self.assertEqual(len(logger.handlers), 1)
            self.assertIsInstance(logger.handlers[0], logging.NullHandler)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_configure_feishu_logger_deduplicates_file_handler(self) -> None:
        logger = logging.getLogger("assistant_app.feishu")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            with tempfile.TemporaryDirectory() as tmp:
                path = str(Path(tmp) / "feishu.log")
                _configure_feishu_logger(path, retention_days=7)
                _configure_feishu_logger(path, retention_days=7)
                file_handlers = [handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)]
                self.assertEqual(len(file_handlers), 1)
                self.assertIsInstance(file_handlers[0].formatter, JsonLinesFormatter)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_configure_app_logger_deduplicates_file_handler(self) -> None:
        logger = logging.getLogger("assistant_app.app")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            with tempfile.TemporaryDirectory() as tmp:
                path = str(Path(tmp) / "app.log")
                _configure_app_logger(path, retention_days=7)
                _configure_app_logger(path, retention_days=7)
                file_handlers = [handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)]
                self.assertEqual(len(file_handlers), 1)
                self.assertIsInstance(file_handlers[0].formatter, JsonLinesFormatter)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_configure_app_logger_empty_path_disables_output(self) -> None:
        logger = logging.getLogger("assistant_app.app")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            _configure_app_logger("   ", retention_days=7)
            self.assertFalse(logger.propagate)
            self.assertEqual(len(logger.handlers), 1)
            self.assertIsInstance(logger.handlers[0], logging.NullHandler)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_configure_feishu_logger_empty_path_disables_output(self) -> None:
        logger = logging.getLogger("assistant_app.feishu")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            _configure_feishu_logger("   ", retention_days=7)
            self.assertFalse(logger.propagate)
            self.assertEqual(len(logger.handlers), 1)
            self.assertIsInstance(logger.handlers[0], logging.NullHandler)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_stdout_reminder_sink_emits_reminder_and_prompt(self) -> None:
        stream = io.StringIO()
        sink = StdoutReminderSink(stream=stream)
        sink.emit(
            ReminderEvent(
                reminder_key="todo:1:2026-02-24 10:00",
                source_type="todo",
                source_id=1,
                remind_time="2026-02-24 10:00",
                content="待办提醒 #1: 准备发布",
            )
        )

        self.assertEqual(stream.getvalue(), "\n提醒> 待办提醒 #1: 准备发布\n你> ")


if __name__ == "__main__":
    unittest.main()
