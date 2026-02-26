from __future__ import annotations

import io
import json
import logging
import tempfile
import unittest
from pathlib import Path

from assistant_app.logging_setup import JsonLinesFormatter, configure_app_logger, configure_llm_trace_logger


class LoggingSetupTest(unittest.TestCase):
    def test_json_lines_formatter_formats_plain_message(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("tests.logging_setup.plain")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            logger.handlers.clear()
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = logging.StreamHandler(stream)
            handler.setFormatter(JsonLinesFormatter())
            logger.addHandler(handler)

            logger.info(
                "timer tick completed",
                extra={"event": "timer_tick", "context": {"delivered": 1}},
            )

            lines = [line for line in stream.getvalue().splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload.get("level"), "INFO")
            self.assertEqual(payload.get("event"), "timer_tick")
            self.assertEqual(payload.get("message"), "timer tick completed")
            self.assertEqual(payload.get("context"), {"delivered": 1})
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_json_lines_formatter_merges_json_message_payload(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("tests.logging_setup.structured")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            logger.handlers.clear()
            logger.propagate = False
            logger.setLevel(logging.INFO)
            handler = logging.StreamHandler(stream)
            handler.setFormatter(JsonLinesFormatter())
            logger.addHandler(handler)

            logger.info(json.dumps({"event": "llm_request", "phase": "plan", "attempt": 1}, ensure_ascii=False))

            lines = [line for line in stream.getvalue().splitlines() if line.strip()]
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload.get("event"), "llm_request")
            self.assertEqual(payload.get("phase"), "plan")
            self.assertEqual(payload.get("attempt"), 1)
            self.assertNotIn("message", payload)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate

    def test_configured_logger_writes_json_lines(self) -> None:
        logger = logging.getLogger("assistant_app.llm_trace")
        original_handlers = list(logger.handlers)
        original_propagate = logger.propagate
        try:
            with tempfile.TemporaryDirectory() as tmp:
                log_path = Path(tmp) / "llm_trace.log"
                configure_llm_trace_logger(str(log_path))
                logger.info(json.dumps({"event": "llm_response", "call_id": 7}, ensure_ascii=False))

                lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                self.assertEqual(len(lines), 1)
                payload = json.loads(lines[0])
                self.assertEqual(payload.get("event"), "llm_response")
                self.assertEqual(payload.get("call_id"), 7)
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
            configure_app_logger("   ", retention_days=7)
            self.assertEqual(len(logger.handlers), 1)
            self.assertIsInstance(logger.handlers[0], logging.NullHandler)
            self.assertFalse(logger.propagate)
        finally:
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()
            for handler in original_handlers:
                logger.addHandler(handler)
            logger.propagate = original_propagate


if __name__ == "__main__":
    unittest.main()
