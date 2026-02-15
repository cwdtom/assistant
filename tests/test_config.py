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
