from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant_app.config import load_config

_PRODUCT_SUMMARY = "一个中文优先的AI Agent个人助手，支持自然语言任务执行、日程管理、历史检索、定时后台任务和飞书接入。"


class DocsConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.readme_path = cls.repo_root / "README.md"
        cls.index_path = cls.repo_root / "index.html"
        cls.readme_content = cls.readme_path.read_text(encoding="utf-8")
        cls.index_content = cls.index_path.read_text(encoding="utf-8")

    def test_readme_lists_current_command_contract(self) -> None:
        expected_snippets = [
            "- `/help`",
            "- `/version`",
            "- `/date`",
            "- `/schedule add|list|get|update|delete|repeat|view`",
            "- `/history list|search`",
            "- `/thoughts add|list|get|update|delete`",
        ]
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.readme_content)

    def test_readme_core_defaults_match_runtime_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config(load_dotenv=False)

        expected_snippets = [
            f"`DEEPSEEK_BASE_URL`：默认 `{config.base_url}`",
            f"`DEEPSEEK_MODEL`：默认 `{config.model}`",
            (f"`LLM_TEMPERATURE`：默认 LLM 调用温度（默认 `{config.llm_temperature}`，范围 `0.0~2.0`）"),
            "`SEARCH_PROVIDER`：搜索 provider（`bocha|bochaai|bing`；其中 `bochaai` 作为 `bocha` 兼容别名）",
            (f"`TIMER_ENABLED`：是否启用本地定时后台任务线程（默认 `{str(config.timer_enabled).lower()}`）"),
            (f"`TIMER_POLL_INTERVAL_SECONDS`：后台 timer 扫描周期秒数（默认 `{config.timer_poll_interval_seconds}`）"),
        ]
        for snippet in expected_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, self.readme_content)

    def test_readme_and_index_share_core_summary(self) -> None:
        self.assertIn(_PRODUCT_SUMMARY, self.readme_content)
        self.assertIn(_PRODUCT_SUMMARY, self.index_content)

    def test_readme_and_index_share_quickstart_commands(self) -> None:
        shared_commands = [
            "./scripts/bootstrap.sh --dev",
            "cp .env.example .env",
            "python main.py",
            "./scripts/assistant.sh start",
        ]
        for command in shared_commands:
            with self.subTest(command=command):
                self.assertIn(command, self.readme_content)
                self.assertIn(command, self.index_content)


if __name__ == "__main__":
    unittest.main()
