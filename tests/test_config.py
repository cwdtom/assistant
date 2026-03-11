from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant_app.config import (
    UNKNOWN_APP_VERSION,
    load_config,
    load_env_file,
    load_startup_app_version,
)
from pydantic import ValidationError


class ConfigTest(unittest.TestCase):
    def test_load_config_prefers_deepseek_env(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
            "DEEPSEEK_MODEL": "deepseek-chat",
            "ASSISTANT_DB_PATH": "custom.db",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.api_key, "deep-key")
        self.assertEqual(config.base_url, "https://api.deepseek.com")
        self.assertEqual(config.model, "deepseek-chat")
        self.assertEqual(config.llm_temperature, 1.3)
        self.assertEqual(config.db_path, "custom.db")
        self.assertEqual(config.user_profile_path, "")
        self.assertEqual(config.plan_replan_max_steps, 100)
        self.assertEqual(config.plan_observation_history_limit, 100)
        self.assertEqual(config.internet_search_top_k, 3)
        self.assertEqual(config.search_provider, "bocha")
        self.assertIsNone(config.bocha_api_key)
        self.assertTrue(config.bocha_search_summary)
        self.assertEqual(config.schedule_max_window_days, 31)
        self.assertEqual(config.task_cancel_command, "取消当前任务")
        self.assertEqual(config.cli_progress_color, "gray")
        self.assertEqual(config.llm_trace_log_path, "logs/app.log")
        self.assertEqual(config.app_log_path, "logs/app.log")
        self.assertEqual(config.app_log_retention_days, 7)
        self.assertTrue(config.timer_enabled)
        self.assertEqual(config.timer_poll_interval_seconds, 15)
        self.assertEqual(config.timer_lookahead_seconds, 30)
        self.assertEqual(config.timer_batch_limit, 200)
        self.assertTrue(config.persona_rewrite_enabled)
        self.assertEqual(config.assistant_persona, "")
        self.assertEqual(config.feishu_app_id, "")
        self.assertEqual(config.feishu_app_secret, "")
        self.assertEqual(config.feishu_allowed_open_ids, ())
        self.assertEqual(config.feishu_send_retry_count, 3)
        self.assertEqual(config.feishu_text_chunk_size, 5000)
        self.assertEqual(config.feishu_dedup_ttl_seconds, 600)
        self.assertEqual(config.feishu_log_path, "logs/app.log")
        self.assertEqual(config.feishu_log_retention_days, 7)
        self.assertTrue(config.feishu_ack_reaction_enabled)
        self.assertEqual(config.feishu_ack_emoji_type, "Get")
        self.assertEqual(config.feishu_done_emoji_type, "DONE")
        self.assertEqual(config.feishu_calendar_id, "")
        self.assertEqual(config.feishu_calendar_bootstrap_past_days, 2)
        self.assertEqual(config.feishu_calendar_bootstrap_future_days, 5)
        self.assertEqual(config.proactive_reminder_target_open_id, "")
        self.assertEqual(config.proactive_reminder_interval_minutes, 60)
        self.assertEqual(config.proactive_reminder_lookahead_hours, 24)
        self.assertEqual(config.proactive_reminder_night_quiet_hint, "23:00-08:00")

    def test_load_config_ignores_openai_compatibility_env(self) -> None:
        env = {
            "OPENAI_API_KEY": "legacy-key",
            "OPENAI_BASE_URL": "https://legacy.example.com/v1",
            "OPENAI_MODEL": "legacy-model",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertIsNone(config.api_key)
        self.assertEqual(config.base_url, "https://api.deepseek.com")
        self.assertEqual(config.model, "deepseek-chat")
        self.assertEqual(config.llm_temperature, 1.3)
        self.assertEqual(config.db_path, "assistant.db")
        self.assertEqual(config.user_profile_path, "")
        self.assertEqual(config.llm_trace_log_path, "logs/app.log")
        self.assertEqual(config.app_log_path, "logs/app.log")
        self.assertEqual(config.app_log_retention_days, 7)
        self.assertEqual(config.plan_replan_retry_count, 3)
        self.assertEqual(config.plan_observation_char_limit, 10000)
        self.assertEqual(config.plan_observation_history_limit, 100)
        self.assertEqual(config.plan_continuous_failure_limit, 3)
        self.assertEqual(config.search_provider, "bocha")
        self.assertIsNone(config.bocha_api_key)
        self.assertTrue(config.bocha_search_summary)
        self.assertTrue(config.persona_rewrite_enabled)
        self.assertEqual(config.assistant_persona, "")
        self.assertEqual(config.feishu_allowed_open_ids, ())
        self.assertTrue(config.feishu_ack_reaction_enabled)
        self.assertEqual(config.feishu_ack_emoji_type, "Get")
        self.assertEqual(config.feishu_done_emoji_type, "DONE")
        self.assertEqual(config.feishu_calendar_id, "")
        self.assertEqual(config.feishu_calendar_bootstrap_past_days, 2)
        self.assertEqual(config.feishu_calendar_bootstrap_future_days, 5)

    def test_load_config_log_paths_follow_app_log_path_by_default(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "APP_LOG_PATH": "logs/merged.log",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.app_log_path, "logs/merged.log")
        self.assertEqual(config.llm_trace_log_path, "logs/merged.log")
        self.assertEqual(config.feishu_log_path, "logs/merged.log")

    def test_load_config_reads_runtime_knobs_from_env(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "USER_PROFILE_PATH": "profiles/me.md",
            "USER_PROFILE_REFRESH_ENABLED": "off",
            "USER_PROFILE_REFRESH_HOUR": "5",
            "USER_PROFILE_REFRESH_LOOKBACK_DAYS": "45",
            "USER_PROFILE_REFRESH_MAX_TURNS": "999",
            "LLM_TEMPERATURE": "1.2",
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
            "CLI_PROGRESS_COLOR": "off",
            "LLM_TRACE_LOG_PATH": "logs/custom_llm_trace.log",
            "APP_LOG_PATH": "logs/custom_app.log",
            "APP_LOG_RETENTION_DAYS": "9",
            "TIMER_ENABLED": "off",
            "TIMER_POLL_INTERVAL_SECONDS": "20",
            "TIMER_LOOKAHEAD_SECONDS": "45",
            "TIMER_BATCH_LIMIT": "120",
            "PERSONA_REWRITE_ENABLED": "off",
            "ASSISTANT_PERSONA": "你是严谨的项目经理",
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
            "FEISHU_CALENDAR_ID": "feishu.cn_demo@group.calendar.feishu.cn",
            "FEISHU_CALENDAR_BOOTSTRAP_PAST_DAYS": "3",
            "FEISHU_CALENDAR_BOOTSTRAP_FUTURE_DAYS": "8",
            "PROACTIVE_REMINDER_TARGET_OPEN_ID": "ou_target_1",
            "PROACTIVE_REMINDER_INTERVAL_MINUTES": "120",
            "PROACTIVE_REMINDER_LOOKAHEAD_HOURS": "48",
            "PROACTIVE_REMINDER_NIGHT_QUIET_HINT": "22:00-07:00",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.plan_replan_max_steps, 40)
        self.assertEqual(config.llm_temperature, 1.2)
        self.assertEqual(config.plan_replan_retry_count, 4)
        self.assertEqual(config.plan_observation_char_limit, 12000)
        self.assertEqual(config.plan_observation_history_limit, 80)
        self.assertEqual(config.plan_continuous_failure_limit, 3)
        self.assertEqual(config.task_cancel_command, "停止任务")
        self.assertEqual(config.user_profile_path, "profiles/me.md")
        self.assertFalse(hasattr(config, "user_profile_refresh_enabled"))
        self.assertFalse(hasattr(config, "user_profile_refresh_hour"))
        self.assertFalse(hasattr(config, "user_profile_refresh_lookback_days"))
        self.assertFalse(hasattr(config, "user_profile_refresh_max_turns"))
        self.assertEqual(config.internet_search_top_k, 5)
        self.assertEqual(config.search_provider, "bing")
        self.assertEqual(config.bocha_api_key, "bocha-key")
        self.assertFalse(config.bocha_search_summary)
        self.assertEqual(config.schedule_max_window_days, 45)
        self.assertEqual(config.cli_progress_color, "off")
        self.assertEqual(config.llm_trace_log_path, "logs/custom_llm_trace.log")
        self.assertEqual(config.app_log_path, "logs/custom_app.log")
        self.assertEqual(config.app_log_retention_days, 9)
        self.assertFalse(config.timer_enabled)
        self.assertEqual(config.timer_poll_interval_seconds, 20)
        self.assertEqual(config.timer_lookahead_seconds, 45)
        self.assertEqual(config.timer_batch_limit, 120)
        self.assertFalse(config.persona_rewrite_enabled)
        self.assertEqual(config.assistant_persona, "你是严谨的项目经理")
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
        self.assertEqual(config.feishu_calendar_id, "feishu.cn_demo@group.calendar.feishu.cn")
        self.assertEqual(config.feishu_calendar_bootstrap_past_days, 3)
        self.assertEqual(config.feishu_calendar_bootstrap_future_days, 8)
        self.assertEqual(config.proactive_reminder_target_open_id, "ou_target_1")
        self.assertEqual(config.proactive_reminder_interval_minutes, 120)
        self.assertEqual(config.proactive_reminder_lookahead_hours, 48)
        self.assertEqual(config.proactive_reminder_night_quiet_hint, "22:00-07:00")

    def test_load_config_rejects_invalid_bool_value(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "TIMER_ENABLED": "invalid",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValidationError):
                load_config(load_dotenv=False)

    def test_load_config_rejects_invalid_float_value(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "LLM_TEMPERATURE": "bad",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValidationError):
                load_config(load_dotenv=False)

    def test_load_config_rejects_out_of_range_value(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "PROACTIVE_REMINDER_INTERVAL_MINUTES": "59",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ValidationError):
                load_config(load_dotenv=False)

    def test_load_config_normalizes_blank_text_fields(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "   ",
            "APP_LOG_PATH": "   ",
            "LLM_TRACE_LOG_PATH": "   ",
            "FEISHU_LOG_PATH": "   ",
            "FEISHU_ACK_EMOJI_TYPE": "   ",
            "FEISHU_DONE_EMOJI_TYPE": "   ",
            "TASK_CANCEL_COMMAND": "   ",
            "PROACTIVE_REMINDER_NIGHT_QUIET_HINT": "   ",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertIsNone(config.api_key)
        self.assertEqual(config.app_log_path, "")
        self.assertEqual(config.llm_trace_log_path, "")
        self.assertEqual(config.feishu_log_path, "")
        self.assertEqual(config.feishu_ack_emoji_type, "")
        self.assertEqual(config.feishu_done_emoji_type, "")
        self.assertEqual(config.task_cancel_command, "取消当前任务")
        self.assertEqual(config.proactive_reminder_night_quiet_hint, "")

    def test_load_config_parses_feishu_open_id_list(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "FEISHU_ALLOWED_OPEN_IDS": " ou_1, ,ou_2 ,ou_3 ",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.feishu_allowed_open_ids, ("ou_1", "ou_2", "ou_3"))

    def test_load_env_file_prefers_dotenv_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("DEEPSEEK_API_KEY=file-key\nDEEPSEEK_MODEL=deepseek-chat\n", encoding="utf-8")
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "existing-key"}, clear=True):
                load_env_file(str(env_path))
                self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "file-key")
                self.assertEqual(os.environ["DEEPSEEK_MODEL"], "deepseek-chat")

    def test_load_config_prefers_dotenv_over_process_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("FEISHU_APP_ID=file-id\nFEISHU_APP_SECRET=file-secret\n", encoding="utf-8")
            with patch.dict(
                os.environ,
                {"FEISHU_APP_ID": "existing-id", "FEISHU_APP_SECRET": "existing-secret"},
                clear=True,
            ):
                original_cwd = Path.cwd()
                os.chdir(tmp)
                try:
                    config = load_config(load_dotenv=True)
                finally:
                    os.chdir(original_cwd)

        self.assertEqual(config.feishu_app_id, "file-id")
        self.assertEqual(config.feishu_app_secret, "file-secret")

    def test_load_config_ignores_removed_proactive_threshold_in_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "DEEPSEEK_API_KEY=file-key\nPROACTIVE_REMINDER_SCORE_THRESHOLD=95\n",
                encoding="utf-8",
            )
            original_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                config = load_config(load_dotenv=True)
            finally:
                os.chdir(original_cwd)

        self.assertEqual(config.api_key, "file-key")

    def test_load_config_ignores_removed_user_profile_refresh_fields_in_env(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "USER_PROFILE_REFRESH_ENABLED": "invalid",
            "USER_PROFILE_REFRESH_HOUR": "25",
            "USER_PROFILE_REFRESH_LOOKBACK_DAYS": "0",
            "USER_PROFILE_REFRESH_MAX_TURNS": "0",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.api_key, "deep-key")
        self.assertFalse(hasattr(config, "user_profile_refresh_enabled"))
        self.assertFalse(hasattr(config, "user_profile_refresh_hour"))
        self.assertFalse(hasattr(config, "user_profile_refresh_lookback_days"))
        self.assertFalse(hasattr(config, "user_profile_refresh_max_turns"))

    def test_load_config_ignores_removed_user_profile_refresh_fields_in_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "DEEPSEEK_API_KEY=file-key",
                        "USER_PROFILE_REFRESH_ENABLED=invalid",
                        "USER_PROFILE_REFRESH_HOUR=25",
                        "USER_PROFILE_REFRESH_LOOKBACK_DAYS=0",
                        "USER_PROFILE_REFRESH_MAX_TURNS=0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            original_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                config = load_config(load_dotenv=True)
            finally:
                os.chdir(original_cwd)

        self.assertEqual(config.api_key, "file-key")
        self.assertFalse(hasattr(config, "user_profile_refresh_enabled"))
        self.assertFalse(hasattr(config, "user_profile_refresh_hour"))
        self.assertFalse(hasattr(config, "user_profile_refresh_lookback_days"))
        self.assertFalse(hasattr(config, "user_profile_refresh_max_turns"))

    def test_load_config_ignores_removed_feishu_calendar_reconcile_interval_in_env(self) -> None:
        env = {
            "DEEPSEEK_API_KEY": "deep-key",
            "FEISHU_CALENDAR_RECONCILE_INTERVAL_MINUTES": "15",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config(load_dotenv=False)

        self.assertEqual(config.api_key, "deep-key")
        self.assertFalse(hasattr(config, "feishu_calendar_reconcile_interval_minutes"))

    def test_load_config_ignores_removed_feishu_calendar_reconcile_interval_in_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "DEEPSEEK_API_KEY=file-key\nFEISHU_CALENDAR_RECONCILE_INTERVAL_MINUTES=15\n",
                encoding="utf-8",
            )
            original_cwd = Path.cwd()
            os.chdir(tmp)
            try:
                config = load_config(load_dotenv=True)
            finally:
                os.chdir(original_cwd)

        self.assertEqual(config.api_key, "file-key")
        self.assertFalse(hasattr(config, "feishu_calendar_reconcile_interval_minutes"))

    def test_load_startup_app_version_reads_project_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pyproject = Path(tmp) / "pyproject.toml"
            pyproject.write_text(
                '[build-system]\nrequires=["setuptools"]\n\n[project]\nname="demo"\nversion = "2.3.4"\n',
                encoding="utf-8",
            )

            version = load_startup_app_version(pyproject_path=pyproject)

        self.assertEqual(version, "2.3.4")

    def test_load_startup_app_version_falls_back_to_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pyproject = Path(tmp) / "pyproject.toml"
            pyproject.write_text('[project]\nname="demo"\n', encoding="utf-8")

            with self.assertLogs("test.config.version", level="WARNING") as captured:
                version = load_startup_app_version(
                    pyproject_path=pyproject,
                    logger=logging.getLogger("test.config.version"),
                )

        self.assertEqual(version, UNKNOWN_APP_VERSION)
        self.assertTrue(any("failed to load app version from pyproject" in item for item in captured.output))


if __name__ == "__main__":
    unittest.main()
