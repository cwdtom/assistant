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
        self.assertEqual(config.user_profile_path, "")
        self.assertEqual(config.plan_replan_max_steps, 20)
        self.assertEqual(config.plan_observation_history_limit, 100)
        self.assertEqual(config.internet_search_top_k, 3)
        self.assertEqual(config.search_provider, "bocha")
        self.assertIsNone(config.bocha_api_key)
        self.assertTrue(config.bocha_search_summary)
        self.assertEqual(config.schedule_max_window_days, 31)
        self.assertEqual(config.infinite_repeat_conflict_preview_days, 31)
        self.assertEqual(config.task_cancel_command, "取消当前任务")
        self.assertEqual(config.cli_progress_color, "gray")
        self.assertEqual(config.llm_trace_log_path, "logs/llm_trace.log")
        self.assertTrue(config.timer_enabled)
        self.assertEqual(config.timer_poll_interval_seconds, 15)
        self.assertEqual(config.timer_lookahead_seconds, 30)
        self.assertEqual(config.timer_catchup_seconds, 0)
        self.assertEqual(config.timer_batch_limit, 200)
        self.assertEqual(config.reminder_delivery_retention_days, 30)
        self.assertTrue(config.persona_rewrite_enabled)
        self.assertEqual(config.assistant_persona, "")
        self.assertFalse(config.feishu_enabled)
        self.assertEqual(config.feishu_app_id, "")
        self.assertEqual(config.feishu_app_secret, "")
        self.assertEqual(config.feishu_allowed_open_ids, ())
        self.assertEqual(config.feishu_send_retry_count, 3)
        self.assertEqual(config.feishu_text_chunk_size, 1500)
        self.assertEqual(config.feishu_dedup_ttl_seconds, 600)
        self.assertEqual(config.feishu_log_path, "logs/feishu.log")
        self.assertEqual(config.feishu_log_retention_days, 7)
        self.assertTrue(config.feishu_ack_reaction_enabled)
        self.assertEqual(config.feishu_ack_emoji_type, "OK")
        self.assertEqual(config.feishu_done_emoji_type, "DONE")

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
        self.assertEqual(config.user_profile_path, "")
        self.assertEqual(config.llm_trace_log_path, "logs/llm_trace.log")
        self.assertEqual(config.plan_replan_retry_count, 2)
        self.assertEqual(config.plan_observation_char_limit, 10000)
        self.assertEqual(config.plan_observation_history_limit, 100)
        self.assertEqual(config.plan_continuous_failure_limit, 2)
        self.assertEqual(config.search_provider, "bocha")
        self.assertIsNone(config.bocha_api_key)
        self.assertTrue(config.bocha_search_summary)
        self.assertTrue(config.persona_rewrite_enabled)
        self.assertEqual(config.assistant_persona, "")
        self.assertFalse(config.feishu_enabled)
        self.assertEqual(config.feishu_allowed_open_ids, ())
        self.assertTrue(config.feishu_ack_reaction_enabled)
        self.assertEqual(config.feishu_ack_emoji_type, "OK")
        self.assertEqual(config.feishu_done_emoji_type, "DONE")

    def test_load_config_reads_runtime_knobs_from_env(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "USER_PROFILE_PATH": "profiles/me.md",
            "PLAN_REPLAN_MAX_STEPS": "40",
            "PLAN_REPLAN_RETRY_COUNT": "4",
            "PLAN_OBSERVATION_CHAR_LIMIT": "12000",
            "PLAN_OBSERVATION_HISTORY_LIMIT": "80",
            "PLAN_CONTINUOUS_FAILURE_LIMIT": "3",
            "TASK_CANCEL_COMMAND": "停止任务",
            "INTERNET_SEARCH_TOP_K": "5",
            "SEARCH_PROVIDER": "bing",
            "BOCHA_API_KEY": "bocha-key",
            "BOCHA_SEARCH_SUMMARY": "off",
            "SCHEDULE_MAX_WINDOW_DAYS": "45",
            "INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS": "14",
            "CLI_PROGRESS_COLOR": "off",
            "LLM_TRACE_LOG_PATH": "logs/custom_llm_trace.log",
            "TIMER_ENABLED": "off",
            "TIMER_POLL_INTERVAL_SECONDS": "20",
            "TIMER_LOOKAHEAD_SECONDS": "45",
            "TIMER_CATCHUP_SECONDS": "999",
            "TIMER_BATCH_LIMIT": "120",
            "REMINDER_DELIVERY_RETENTION_DAYS": "7",
            "PERSONA_REWRITE_ENABLED": "off",
            "ASSISTANT_PERSONA": "你是严谨的项目经理",
            "FEISHU_ENABLED": "on",
            "FEISHU_APP_ID": "cli_test",
            "FEISHU_APP_SECRET": "secret_test",
            "FEISHU_ALLOWED_OPEN_IDS": "ou_1,ou_2, ou_3 ",
            "FEISHU_SEND_RETRY_COUNT": "5",
            "FEISHU_TEXT_CHUNK_SIZE": "1200",
            "FEISHU_DEDUP_TTL_SECONDS": "900",
            "FEISHU_LOG_PATH": "logs/feishu_custom.log",
            "FEISHU_LOG_RETENTION_DAYS": "10",
            "FEISHU_ACK_REACTION_ENABLED": "off",
            "FEISHU_ACK_EMOJI_TYPE": "THUMBSUP",
            "FEISHU_DONE_EMOJI_TYPE": "DONE_CUSTOM",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.plan_replan_max_steps, 40)
        self.assertEqual(config.plan_replan_retry_count, 4)
        self.assertEqual(config.plan_observation_char_limit, 12000)
        self.assertEqual(config.plan_observation_history_limit, 80)
        self.assertEqual(config.plan_continuous_failure_limit, 3)
        self.assertEqual(config.task_cancel_command, "停止任务")
        self.assertEqual(config.user_profile_path, "profiles/me.md")
        self.assertEqual(config.internet_search_top_k, 5)
        self.assertEqual(config.search_provider, "bing")
        self.assertEqual(config.bocha_api_key, "bocha-key")
        self.assertFalse(config.bocha_search_summary)
        self.assertEqual(config.schedule_max_window_days, 45)
        self.assertEqual(config.infinite_repeat_conflict_preview_days, 14)
        self.assertEqual(config.cli_progress_color, "off")
        self.assertEqual(config.llm_trace_log_path, "logs/custom_llm_trace.log")
        self.assertFalse(config.timer_enabled)
        self.assertEqual(config.timer_poll_interval_seconds, 20)
        self.assertEqual(config.timer_lookahead_seconds, 45)
        self.assertEqual(config.timer_catchup_seconds, 0)
        self.assertEqual(config.timer_batch_limit, 120)
        self.assertEqual(config.reminder_delivery_retention_days, 7)
        self.assertFalse(config.persona_rewrite_enabled)
        self.assertEqual(config.assistant_persona, "你是严谨的项目经理")
        self.assertTrue(config.feishu_enabled)
        self.assertEqual(config.feishu_app_id, "cli_test")
        self.assertEqual(config.feishu_app_secret, "secret_test")
        self.assertEqual(config.feishu_allowed_open_ids, ("ou_1", "ou_2", "ou_3"))
        self.assertEqual(config.feishu_send_retry_count, 5)
        self.assertEqual(config.feishu_text_chunk_size, 1200)
        self.assertEqual(config.feishu_dedup_ttl_seconds, 900)
        self.assertEqual(config.feishu_log_path, "logs/feishu_custom.log")
        self.assertEqual(config.feishu_log_retention_days, 10)
        self.assertFalse(config.feishu_ack_reaction_enabled)
        self.assertEqual(config.feishu_ack_emoji_type, "THUMBSUP")
        self.assertEqual(config.feishu_done_emoji_type, "DONE_CUSTOM")

    def test_load_config_invalid_runtime_knobs_fall_back_to_defaults(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "USER_PROFILE_PATH": "   ",
            "PLAN_REPLAN_MAX_STEPS": "0",
            "PLAN_REPLAN_RETRY_COUNT": "-3",
            "PLAN_OBSERVATION_CHAR_LIMIT": "bad",
            "PLAN_OBSERVATION_HISTORY_LIMIT": "0",
            "PLAN_CONTINUOUS_FAILURE_LIMIT": "-1",
            "TASK_CANCEL_COMMAND": "   ",
            "INTERNET_SEARCH_TOP_K": "0",
            "SEARCH_PROVIDER": "unsupported",
            "BOCHA_API_KEY": "   ",
            "BOCHA_SEARCH_SUMMARY": "bad",
            "SCHEDULE_MAX_WINDOW_DAYS": "-7",
            "INFINITE_REPEAT_CONFLICT_PREVIEW_DAYS": "abc",
            "CLI_PROGRESS_COLOR": "  ",
            "LLM_TRACE_LOG_PATH": "   ",
            "TIMER_ENABLED": "invalid",
            "TIMER_POLL_INTERVAL_SECONDS": "0",
            "TIMER_LOOKAHEAD_SECONDS": "-1",
            "TIMER_BATCH_LIMIT": "bad",
            "REMINDER_DELIVERY_RETENTION_DAYS": "0",
            "PERSONA_REWRITE_ENABLED": "invalid",
            "ASSISTANT_PERSONA": "   ",
            "FEISHU_ENABLED": "invalid",
            "FEISHU_APP_ID": "   ",
            "FEISHU_APP_SECRET": "   ",
            "FEISHU_ALLOWED_OPEN_IDS": " , ,, ",
            "FEISHU_SEND_RETRY_COUNT": "-1",
            "FEISHU_TEXT_CHUNK_SIZE": "0",
            "FEISHU_DEDUP_TTL_SECONDS": "abc",
            "FEISHU_LOG_PATH": "   ",
            "FEISHU_LOG_RETENTION_DAYS": "0",
            "FEISHU_ACK_REACTION_ENABLED": "invalid",
            "FEISHU_ACK_EMOJI_TYPE": "   ",
            "FEISHU_DONE_EMOJI_TYPE": "   ",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.plan_replan_max_steps, 20)
        self.assertEqual(config.plan_replan_retry_count, 2)
        self.assertEqual(config.plan_observation_char_limit, 10000)
        self.assertEqual(config.plan_observation_history_limit, 100)
        self.assertEqual(config.plan_continuous_failure_limit, 2)
        self.assertEqual(config.task_cancel_command, "取消当前任务")
        self.assertEqual(config.user_profile_path, "")
        self.assertEqual(config.internet_search_top_k, 3)
        self.assertEqual(config.search_provider, "bocha")
        self.assertIsNone(config.bocha_api_key)
        self.assertTrue(config.bocha_search_summary)
        self.assertEqual(config.schedule_max_window_days, 31)
        self.assertEqual(config.infinite_repeat_conflict_preview_days, 31)
        self.assertEqual(config.cli_progress_color, "gray")
        self.assertEqual(config.llm_trace_log_path, "")
        self.assertTrue(config.timer_enabled)
        self.assertEqual(config.timer_poll_interval_seconds, 15)
        self.assertEqual(config.timer_lookahead_seconds, 30)
        self.assertEqual(config.timer_catchup_seconds, 0)
        self.assertEqual(config.timer_batch_limit, 200)
        self.assertEqual(config.reminder_delivery_retention_days, 30)
        self.assertTrue(config.persona_rewrite_enabled)
        self.assertEqual(config.assistant_persona, "")
        self.assertFalse(config.feishu_enabled)
        self.assertEqual(config.feishu_app_id, "")
        self.assertEqual(config.feishu_app_secret, "")
        self.assertEqual(config.feishu_allowed_open_ids, ())
        self.assertEqual(config.feishu_send_retry_count, 3)
        self.assertEqual(config.feishu_text_chunk_size, 1500)
        self.assertEqual(config.feishu_dedup_ttl_seconds, 600)
        self.assertEqual(config.feishu_log_path, "")
        self.assertEqual(config.feishu_log_retention_days, 7)
        self.assertTrue(config.feishu_ack_reaction_enabled)
        self.assertEqual(config.feishu_ack_emoji_type, "")
        self.assertEqual(config.feishu_done_emoji_type, "")

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
