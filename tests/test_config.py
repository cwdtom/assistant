from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant_app.config import load_config, load_env_file


class ConfigTest(unittest.TestCase):
    def test_load_config_prefers_deepseek_env(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_MODEL": "deepseek-chat",
            "ASSISTANT_DB_PATH": "custom.db",
            "OPENAI_API_KEY": "legacy-key",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.api_key, "deep-key")
        self.assertEqual(config.base_url, "https://api.deepseek.com")
        self.assertEqual(config.model, "deepseek-chat")
        self.assertEqual(config.db_path, "custom.db")
        self.assertEqual(config.plan_replan_max_steps, 20)
        self.assertEqual(config.internet_search_top_k, 3)
        self.assertEqual(config.schedule_max_window_days, 31)
        self.assertEqual(config.infinite_repeat_conflict_preview_days, 31)
        self.assertEqual(config.task_cancel_command, "取消当前任务")
        self.assertEqual(config.cli_progress_color, "gray")
        self.assertEqual(config.llm_trace_log_path, "logs/llm_trace.log")

    def test_load_config_falls_back_to_openai_env(self) -> None:
        env = {
            "OPENAI_API_KEY": "legacy-key",
            "OPENAI_BASE_URL": "https://legacy.example.com/v1",
            "OPENAI_MODEL": "legacy-model",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.api_key, "legacy-key")
        self.assertEqual(config.base_url, "https://legacy.example.com/v1")
        self.assertEqual(config.model, "legacy-model")
        self.assertEqual(config.db_path, "assistant.db")
        self.assertEqual(config.llm_trace_log_path, "logs/llm_trace.log")
        self.assertEqual(config.plan_replan_retry_count, 2)
        self.assertEqual(config.plan_observation_char_limit, 10000)
        self.assertEqual(config.plan_observation_history_limit, 100)
        self.assertEqual(config.plan_continuous_failure_limit, 2)

    def test_load_config_reads_runtime_knobs_from_env(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "PLAN_REPLAN_MAX_STEPS": "40",
            "PLAN_REPLAN_RETRY_COUNT": "4",
            "PLAN_OBSERVATION_CHAR_LIMIT": "12000",
            "PLAN_OBSERVATION_HISTORY_LIMIT": "120",
            "PLAN_CONTINUOUS_FAILURE_LIMIT": "3",
            "TASK_CANCEL_COMMAND": "停止任务",
            "INTERNET_SEARCH_TOP_K": "5",
            "SCHEDULE_MAX_WINDOW_DAYS": "45",
            "INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS": "14",
            "CLI_PROGRESS_COLOR": "off",
            "LLM_TRACE_LOG_PATH": "logs/custom_llm_trace.log",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.plan_replan_max_steps, 40)
        self.assertEqual(config.plan_replan_retry_count, 4)
        self.assertEqual(config.plan_observation_char_limit, 12000)
        self.assertEqual(config.plan_observation_history_limit, 120)
        self.assertEqual(config.plan_continuous_failure_limit, 3)
        self.assertEqual(config.task_cancel_command, "停止任务")
        self.assertEqual(config.internet_search_top_k, 5)
        self.assertEqual(config.schedule_max_window_days, 45)
        self.assertEqual(config.infinite_repeat_conflict_preview_days, 14)
        self.assertEqual(config.cli_progress_color, "off")
        self.assertEqual(config.llm_trace_log_path, "logs/custom_llm_trace.log")

    def test_load_config_invalid_runtime_knobs_fall_back_to_defaults(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "PLAN_REPLAN_MAX_STEPS": "0",
            "PLAN_REPLAN_RETRY_COUNT": "-3",
            "PLAN_OBSERVATION_CHAR_LIMIT": "bad",
            "PLAN_OBSERVATION_HISTORY_LIMIT": "0",
            "PLAN_CONTINUOUS_FAILURE_LIMIT": "-1",
            "TASK_CANCEL_COMMAND": "   ",
            "INTERNET_SEARCH_TOP_K": "0",
            "SCHEDULE_MAX_WINDOW_DAYS": "-7",
            "INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS": "abc",
            "CLI_PROGRESS_COLOR": "  ",
            "LLM_TRACE_LOG_PATH": "   ",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.plan_replan_max_steps, 20)
        self.assertEqual(config.plan_replan_retry_count, 2)
        self.assertEqual(config.plan_observation_char_limit, 10000)
        self.assertEqual(config.plan_observation_history_limit, 100)
        self.assertEqual(config.plan_continuous_failure_limit, 2)
        self.assertEqual(config.task_cancel_command, "取消当前任务")
        self.assertEqual(config.internet_search_top_k, 3)
        self.assertEqual(config.schedule_max_window_days, 31)
        self.assertEqual(config.infinite_repeat_conflict_preview_days, 31)
        self.assertEqual(config.cli_progress_color, "gray")
        self.assertEqual(config.llm_trace_log_path, "")

    def test_load_env_file_sets_only_missing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("DEEPSEEK_API_KEY=file-key\nDEEPSEEK_MODEL=deepseek-chat\n", encoding="utf-8")
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "existing-key"}, clear=True):
                load_env_file(str(env_path))
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "existing-key")
                self.assertEqual(os.environ["DEEPSEEK_MODEL"], "deepseek-chat")


if __name__ == "__main__":
    unittest.main()
